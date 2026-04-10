"""Fetch stock chart images from FinViz Elite.

Builds the chart.ashx URL, appends auth=FINVIZ_API_KEY, downloads the PNG,
and validates the response before returning raw bytes. Retry and rate-limit
handling mirrors the patterns in fetch_elite.py.
"""

import logging
import os
import re
import time
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

_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")

# Timeframe presets recognised by Finviz chart.ashx
TIMEFRAMES = {
    "d": "d",   # daily (default)
    "w": "w",   # weekly
    "m": "m",   # monthly
}


def _load_env():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass
    _ENV_LOADED = True


def _get_api_key() -> str | None:
    _load_env()
    key = os.environ.get("FINVIZ_API_KEY", "").strip()
    return key or None


def validate_symbol(symbol: str) -> str | None:
    """Normalise and validate a ticker symbol.

    Returns the uppercased symbol if valid, or None if it fails validation.
    """
    cleaned = symbol.strip().upper()
    if _SYMBOL_RE.match(cleaned):
        return cleaned
    return None


def build_chart_url(symbol: str, timeframe: str = "d") -> str:
    """Build the Finviz Elite chart image URL.

    Parameters
    ----------
    symbol : str
        Ticker symbol (already validated/uppercased).
    timeframe : str
        One of 'd' (daily), 'w' (weekly), 'm' (monthly).
    """
    tf = TIMEFRAMES.get(timeframe, "d")
    # ty=c = candle chart, ta=1 = show technical analysis overlays,
    # p=d/w/m = timeframe, s=l = large size
    base = "https://elite.finviz.com/chart.ashx"
    url = f"{base}?t={symbol}&ty=c&ta=1&p={tf}&s=l"

    api_key = _get_api_key()
    if api_key:
        url += f"&auth={api_key}"
    return url


def fetch_chart(symbol: str, timeframe: str = "d") -> bytes | None:
    """Download a chart PNG from FinViz Elite.

    Returns the raw PNG bytes on success, or None on failure. Logs warnings
    for transient errors and retries with exponential backoff.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return None

    url = build_chart_url(symbol, timeframe)

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[chart:%s] 429 rate limit, waiting %ds (attempt %d)", symbol, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[chart:%s] request failed: %s (attempt %d)", symbol, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[chart:%s] failed after %d retries", symbol, _MAX_RETRIES)
        return None

    content_type = resp.headers.get("Content-Type", "")
    if "image" not in content_type:
        logger.warning("[chart:%s] unexpected Content-Type: %s", symbol, content_type)
        return None

    data = resp.content
    # PNG magic bytes: \x89PNG
    if not data or data[:4] != b"\x89PNG":
        logger.warning("[chart:%s] response is not a valid PNG (%d bytes)", symbol, len(data))
        return None

    return data
