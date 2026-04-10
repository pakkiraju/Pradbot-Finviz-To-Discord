"""Fetch options-chain CSV from FinViz Elite.

Uses the official export/options endpoint documented in the Elite help pages:
  https://elite.finviz.com/export/options?t=SYMBOL&ty=oc&e=YYYY-MM-DD&auth=KEY

Mirrors the retry and auth patterns from fetch_elite.py.
"""

import csv
import io
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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

# Tolerant column name mapping — keys are lowercased/stripped header names,
# values are the canonical field names used in OptionsRow.
_COLUMN_MAP = {
    "strike": "strike",
    "strike price": "strike",
    "type": "opt_type",
    "option type": "opt_type",
    "call/put": "opt_type",
    "c/p": "opt_type",
    "last": "last",
    "last price": "last",
    "bid": "bid",
    "ask": "ask",
    "volume": "volume",
    "vol": "volume",
    "open interest": "oi",
    "open int": "oi",
    "oi": "oi",
    "openint": "oi",
    "implied volatility": "iv",
    "impl vol": "iv",
    "iv": "iv",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "rho": "rho",
    "expiry": "expiry",
    "expiration": "expiry",
    "expiration date": "expiry",
    "exp date": "expiry",
}


@dataclass
class OptionsRow:
    """One row from the options chain CSV, normalised."""
    strike: float
    opt_type: str       # "call" or "put"
    last: float | None
    bid: float | None
    ask: float | None
    volume: int
    oi: int
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    expiry: str         # YYYY-MM-DD or raw string from CSV


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


def _parse_float(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("%", "")
    if not s or s in ("-", "\u2014", "N/A", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s) -> int:
    v = _parse_float(s)
    return int(v) if v is not None else 0


def _normalise_type(raw: str) -> str:
    """Map raw call/put indicators to 'call' or 'put'."""
    r = raw.strip().lower()
    if r in ("call", "c"):
        return "call"
    if r in ("put", "p"):
        return "put"
    return r


def build_options_url(symbol: str, expiry: str | None = None) -> str:
    """Build the Finviz Elite options export URL.

    Parameters
    ----------
    symbol : str
        Ticker (already uppercased/validated).
    expiry : str or None
        Expiration date YYYY-MM-DD. If None, omit the &e= param so Finviz
        returns the default (nearest) expiry.
    """
    base = "https://elite.finviz.com/export/options"
    url = f"{base}?t={symbol}&ty=oc"
    if expiry:
        url += f"&e={expiry}"
    api_key = _get_api_key()
    if api_key:
        url += f"&auth={api_key}"
    return url


def scrape_expiry_dates(symbol: str) -> list[str]:
    """Scrape the list of available expiration dates from the Finviz quote page.

    Returns dates as YYYY-MM-DD strings, sorted ascending. Falls back to an
    empty list on failure (caller should handle gracefully).
    """
    api_key = _get_api_key()
    url = f"https://elite.finviz.com/quote.ashx?t={symbol}&ty=oc"
    if api_key:
        url += f"&auth={api_key}"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("[options:%s] failed to scrape expiry list: %s", symbol, e)
        return []

    # Finviz puts expiry dates in <select> or links with date values
    soup = BeautifulSoup(resp.text, "html.parser")
    dates: list[str] = []
    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")

    for option in soup.find_all("option"):
        val = option.get("value", "")
        m = date_re.search(val)
        if m:
            dates.append(m.group())
    for a in soup.find_all("a", href=True):
        m = date_re.search(a["href"])
        if m and "ty=oc" in a["href"]:
            d = m.group()
            if d not in dates:
                dates.append(d)

    dates.sort()
    return dates


def nearest_expiry(symbol: str) -> str | None:
    """Return the nearest future expiry for the symbol, or None."""
    dates = scrape_expiry_dates(symbol)
    today = date.today().isoformat()
    for d in dates:
        if d >= today:
            return d
    return dates[0] if dates else None


def fetch_options(symbol: str, expiry: str | None = None) -> list[OptionsRow]:
    """Download and parse the options-chain CSV from FinViz Elite.

    If *expiry* is None, the nearest available expiry is discovered
    automatically.  Returns an empty list on any failure.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return []

    if expiry is None:
        expiry = nearest_expiry(symbol)
        if not expiry:
            logger.warning("[options:%s] could not determine nearest expiry", symbol)

    url = build_options_url(symbol, expiry)

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[options:%s] 429 rate limit, waiting %ds (attempt %d)", symbol, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[options:%s] request failed: %s (attempt %d)", symbol, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[options:%s] failed after %d retries", symbol, _MAX_RETRIES)
        return []

    text = resp.text.strip().lstrip("\ufeff")
    if text.startswith("<"):
        if "login" in text[:2000].lower() or "sign in" in text[:2000].lower():
            logger.error("[options:%s] FinViz returned login page — check FINVIZ_API_KEY", symbol)
        else:
            logger.warning("[options:%s] got HTML instead of CSV", symbol)
        return []

    return _parse_csv(text, symbol, expiry or "")


def _parse_csv(text: str, symbol: str, fallback_expiry: str) -> list[OptionsRow]:
    """Parse raw CSV text into OptionsRow objects."""
    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        logger.warning("[options:%s] CSV parse init failed: %s", symbol, e)
        return []

    if not reader.fieldnames:
        logger.warning("[options:%s] CSV has no headers", symbol)
        return []

    # Build mapping: CSV header -> canonical name
    col_map: dict[str, str] = {}
    for raw_header in reader.fieldnames:
        key = raw_header.strip().lower()
        if key in _COLUMN_MAP:
            col_map[raw_header] = _COLUMN_MAP[key]

    logger.debug("[options:%s] CSV headers: %s", symbol, reader.fieldnames)
    logger.debug("[options:%s] mapped columns: %s", symbol, col_map)

    rows: list[OptionsRow] = []
    for raw in reader:
        mapped = {}
        for csv_col, canon in col_map.items():
            mapped[canon] = raw.get(csv_col, "")

        strike = _parse_float(mapped.get("strike"))
        if strike is None:
            continue

        opt_type = _normalise_type(mapped.get("opt_type", ""))
        if opt_type not in ("call", "put"):
            continue

        rows.append(OptionsRow(
            strike=strike,
            opt_type=opt_type,
            last=_parse_float(mapped.get("last")),
            bid=_parse_float(mapped.get("bid")),
            ask=_parse_float(mapped.get("ask")),
            volume=_parse_int(mapped.get("volume")),
            oi=_parse_int(mapped.get("oi")),
            iv=_parse_float(mapped.get("iv")),
            delta=_parse_float(mapped.get("delta")),
            gamma=_parse_float(mapped.get("gamma")),
            theta=_parse_float(mapped.get("theta")),
            vega=_parse_float(mapped.get("vega")),
            expiry=mapped.get("expiry") or fallback_expiry,
        ))

    return rows
