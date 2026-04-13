"""Massive (Polygon-compatible) REST client — aggregates only, no WebSockets."""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

MASSIVE_BASE = "https://api.massive.com"
_MAX_RETRIES = 4

_HEADERS_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def get_massive_api_key() -> str | None:
    for name in ("MASSIVE_API_KEY", "POLYGON_API_KEY"):
        k = os.environ.get(name, "").strip()
        if k:
            return k
    return None


def _auth_headers() -> dict[str, str]:
    key = get_massive_api_key()
    if not key:
        return dict(_HEADERS_UA)
    h = dict(_HEADERS_UA)
    h["Authorization"] = f"Bearer {key}"
    return h


def get_json(url: str, *, caller: str = "", timeout: float = 120.0) -> dict[str, Any] | None:
    """GET JSON; retries on 429/5xx. *url* may be absolute (including next_url)."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_auth_headers(), timeout=timeout)
            if resp.status_code == 429:
                wait = (2**attempt) * 15
                logger.warning("[%s] Massive 429, waiting %ds (attempt %d)", caller, wait, attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep((2**attempt) * 2)
                if attempt < _MAX_RETRIES - 1:
                    continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("[%s] Massive request failed: %s (attempt %d)", caller, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2**attempt) * 2)
            continue
    logger.error("[%s] Massive failed after %d retries", caller, _MAX_RETRIES)
    return None


def _collect_results(first_url: str, *, caller: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    url: str | None = first_url
    while url:
        data = get_json(url, caller=caller)
        if not data:
            break
        st = data.get("status")
        if st and st != "OK":
            logger.warning("[%s] Massive status=%s", caller, st)
        part = data.get("results")
        if part:
            out.extend(part)
        url = data.get("next_url")
        if not url:
            break
    return out


def fetch_daily_bars(
    ticker: str,
    from_date: str,
    to_date: str,
    *,
    caller: str = "massive_daily",
) -> list[dict[str, Any]]:
    """Daily OHLCV bars, sort ascending."""
    path = f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    q = urlencode({"adjusted": "true", "sort": "asc", "limit": "50000"})
    first = f"{MASSIVE_BASE}{path}?{q}"
    return _collect_results(first, caller=caller)


def fetch_aggregate_bars(
    ticker: str,
    multiplier: int,
    timespan: str,
    from_spec: str | int,
    to_spec: str | int,
    *,
    caller: str = "massive_aggs",
) -> list[dict[str, Any]]:
    """OHLCV aggregates. *from_spec* / *to_spec*: Unix **ms** (int) for minute/hour, or **YYYY-MM-DD** for day."""
    path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_spec}/{to_spec}"
    q = urlencode({"adjusted": "true", "sort": "asc", "limit": "50000"})
    first = f"{MASSIVE_BASE}{path}?{q}"
    return _collect_results(first, caller=caller)


def fetch_minute_bars(
    ticker: str,
    from_ms: int,
    to_ms: int,
    *,
    caller: str = "massive_minute",
) -> list[dict[str, Any]]:
    """Minute OHLCV bars from *from_ms* to *to_ms* (Unix ms), ascending."""
    return fetch_aggregate_bars(ticker, 1, "minute", from_ms, to_ms, caller=caller)


def fetch_five_minute_bars(
    ticker: str,
    from_ms: int,
    to_ms: int,
    *,
    caller: str = "massive_5m",
) -> list[dict[str, Any]]:
    """5-minute OHLCV bars (Unix ms range)."""
    return fetch_aggregate_bars(ticker, 5, "minute", from_ms, to_ms, caller=caller)


def fetch_hour_bars(
    ticker: str,
    from_ms: int,
    to_ms: int,
    *,
    caller: str = "massive_hour",
) -> list[dict[str, Any]]:
    """Hourly OHLCV bars (Unix ms range)."""
    return fetch_aggregate_bars(ticker, 1, "hour", from_ms, to_ms, caller=caller)


def sum_minute_volume(ticker: str, start_ms: int, end_ms: int, *, caller: str) -> float:
    """Sum volume for minute bars with start timestamp ``t`` in ``[start_ms, end_ms)``."""
    if start_ms >= end_ms:
        return 0.0
    bars = fetch_minute_bars(ticker, start_ms, end_ms, caller=caller)
    total = 0.0
    for b in bars:
        t = int(b.get("t", 0))
        if start_ms <= t < end_ms:
            v = b.get("v")
            if v is not None:
                total += float(v)
    return total
