"""Fetch OHLCV quote history for a ticker from FinViz Elite.

Uses the quote export endpoint:
  https://elite.finviz.com/quote_export.ashx?t=SYMBOL&p=d&auth=KEY

Returns rows newest-first so callers can slice the most recent bars.
"""

import csv
import io
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ENV_LOADED = False
_MAX_RETRIES = 4

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class QuoteBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def _load_env():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    import env_setup

    env_setup.configure_environment()
    _ENV_LOADED = True


def _get_api_key() -> str | None:
    _load_env()
    key = os.environ.get("FINVIZ_API_KEY", "").strip()
    return key or None


def _parse_float(s: str) -> float:
    return float(s.strip().replace(",", ""))


def _parse_int(s: str) -> int:
    return int(float(s.strip().replace(",", "")))


def fetch_quote(symbol: str, last_n: int = 5) -> list[QuoteBar]:
    """Fetch daily OHLCV bars for *symbol*, returning the most recent *last_n* bars newest-first."""
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return []

    url = f"https://elite.finviz.com/quote_export.ashx?t={symbol}&p=d&auth={api_key}"
    logger.info("[quote:%s] fetching %s", symbol, url.split("&auth=")[0])

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[quote:%s] 429 rate limit, waiting %ds (attempt %d)", symbol, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[quote:%s] request failed: %s (attempt %d)", symbol, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[quote:%s] failed after %d retries", symbol, _MAX_RETRIES)
        return []

    text = resp.text.strip().lstrip("\ufeff")

    if not text:
        logger.warning("[quote:%s] empty response from FinViz", symbol)
        return []

    if text.startswith("<"):
        if "login" in text[:2000].lower() or "sign in" in text[:2000].lower():
            logger.error("[quote:%s] FinViz returned login page — check FINVIZ_API_KEY", symbol)
        else:
            logger.warning("[quote:%s] got HTML instead of CSV", symbol)
        return []

    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        logger.warning("[quote:%s] CSV parse failed: %s", symbol, e)
        return []

    rows: list[QuoteBar] = []
    for raw in reader:
        try:
            rows.append(QuoteBar(
                date=raw.get("Date", "").strip(),
                open=_parse_float(raw.get("Open", "0")),
                high=_parse_float(raw.get("High", "0")),
                low=_parse_float(raw.get("Low", "0")),
                close=_parse_float(raw.get("Close", "0")),
                volume=_parse_int(raw.get("Volume", "0")),
            ))
        except (ValueError, TypeError):
            continue

    logger.info("[quote:%s] parsed %d bars", symbol, len(rows))
    # CSV is oldest-first; reverse so newest is first, then slice
    rows.reverse()
    return rows[:last_n]
