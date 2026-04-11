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

# Timeframe presets recognised by Finviz elite chart.ashx (`p=` query param).
# Intraday / hourly: i1, i3, i5, i15, i30, h; longer: d, w, m.
TIMEFRAMES = {
    "i1": "i1",
    "i3": "i3",
    "i5": "i5",
    "i15": "i15",
    "i30": "i30",
    "h": "h",
    "d": "d",
    "w": "w",
    "m": "m",
}

# Display labels for embed title / footer (keys match TIMEFRAMES / slash choice values).
CHART_TIMEFRAME_LABELS: dict[str, str] = {
    "i1": "1 minute",
    "i3": "3 minute",
    "i5": "5 minute",
    "i15": "15 minute",
    "i30": "30 minute",
    "h": "1 hour",
    "d": "Daily",
    "w": "Weekly",
    "m": "Monthly",
}

# Short tags for attachment filenames (no spaces).
CHART_TIMEFRAME_FILE_TAG: dict[str, str] = {
    "i1": "1m",
    "i3": "3m",
    "i5": "5m",
    "i15": "15m",
    "i30": "30m",
    "h": "1h",
    "d": "daily",
    "w": "weekly",
    "m": "monthly",
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
    """Build the Finviz Elite **stock** chart image URL.

    Parameters
    ----------
    symbol : str
        Ticker symbol (already validated/uppercased).
    timeframe : str
        One of TIMEFRAMES keys (e.g. i5 for 5-minute, d for daily).
    """
    tf = TIMEFRAMES.get(timeframe, "d")
    # ty=c = candle chart, ta=1 = show technical analysis overlays,
    # p=i1/i5/h/d/w/m = timeframe, s=l = large size
    base = "https://elite.finviz.com/chart.ashx"
    url = f"{base}?t={symbol}&ty=c&ta=1&p={tf}&s=l"

    api_key = _get_api_key()
    if api_key:
        url += f"&auth={api_key}"
    return url


def _download_chart_png(url: str, log_tag: str) -> bytes | None:
    """GET *url* and return PNG bytes. Accepts PNG magic bytes regardless of Content-Type."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[%s] 429 rate limit, waiting %ds (attempt %d)", log_tag, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[%s] request failed: %s (attempt %d)", log_tag, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[%s] failed after %d retries", log_tag, _MAX_RETRIES)
        return None

    data = resp.content
    if len(data) >= 4 and data[:4] == b"\x89PNG":
        return data

    ct = resp.headers.get("Content-Type", "")
    logger.warning("[%s] not a PNG (Content-Type=%s, len=%d)", log_tag, ct, len(data))
    return None


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
    return _download_chart_png(url, f"chart:{symbol}")
