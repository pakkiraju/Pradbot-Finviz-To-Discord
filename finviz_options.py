"""Fetch options-chain CSV from FinViz Elite.

Uses the official export/options endpoint documented in the Elite help pages:
  https://elite.finviz.com/export/options?t=SYMBOL&ty=oc&e=YYYY-MM-DD&auth=KEY

When no expiry is specified the &e= param is omitted — Finviz returns ALL
expirations with an explicit Expiry column.  The code then filters to the
nearest future expiry automatically.

Mirrors the retry and auth patterns from fetch_elite.py.
"""

import csv
import io
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date
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

# Tolerant column name mapping — keys are lowercased/stripped header names,
# values are the canonical field names used in OptionsRow.
#
# Actual Finviz Elite CSV headers (confirmed April 2026):
#   Without &e=:  Contract Name, Last Trade, Expiry, Strike, Last Close, …
#   With &e=:     Contract Name, Last Trade, Strike, Last Close, …
#                 (no Expiry column when a specific date is requested)
_COLUMN_MAP = {
    "strike": "strike",
    "strike price": "strike",
    "contract name": "contract_name",
    "type": "opt_type",
    "option type": "opt_type",
    "call/put": "opt_type",
    "c/p": "opt_type",
    "last": "last",
    "last price": "last",
    "last close": "last",
    "bid": "bid",
    "ask": "ask",
    "volume": "volume",
    "vol": "volume",
    "open interest": "oi",
    "open int.": "oi",
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

# OCC-style contract name encodes the expiry date, e.g. MSFT260718C00400000
# means expiry 2026-07-18, Call, strike $400.
_CONTRACT_RE = re.compile(
    r"^[A-Z0-9.]+(\d{2})(\d{2})(\d{2})([CP])\d+$"
)


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
    import env_setup

    env_setup.configure_environment()
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


def _build_options_url(symbol: str, expiry: str | None = None) -> str:
    """Build the Finviz Elite options export URL.

    When *expiry* is None the ``&e=`` param is omitted and Finviz returns all
    expirations (with an Expiry column in the CSV).
    """
    base = "https://elite.finviz.com/export/options"
    url = f"{base}?t={symbol}&ty=oc"
    if expiry:
        url += f"&e={expiry}"
    api_key = _get_api_key()
    if api_key:
        url += f"&auth={api_key}"
    return url


def _fetch_csv(symbol: str, url: str) -> str | None:
    """GET the export URL with retries.  Returns raw CSV text or None."""
    logger.info("[options:%s] fetching %s", symbol, url.split("&auth=")[0])

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
        return None

    text = resp.text.strip().lstrip("\ufeff")

    if not text:
        logger.warning("[options:%s] empty response from FinViz", symbol)
        return None

    if text.startswith("<"):
        if "login" in text[:2000].lower() or "sign in" in text[:2000].lower():
            logger.error("[options:%s] FinViz returned login page — check FINVIZ_API_KEY", symbol)
        else:
            logger.warning("[options:%s] got HTML instead of CSV (first 200 chars: %s)", symbol, text[:200])
        return None

    return text


def fetch_options(symbol: str, expiry: str | None = None) -> list[OptionsRow]:
    """Download and parse the options-chain CSV from FinViz Elite.

    When *expiry* is None the endpoint is called without ``&e=``, which returns
    every expiration.  The rows are then filtered to the nearest future expiry.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return []

    url = _build_options_url(symbol, expiry)
    text = _fetch_csv(symbol, url)
    if text is None:
        return []

    all_rows = _parse_csv(text, symbol, expiry or "")

    if expiry is not None:
        return all_rows

    # No expiry requested — pick the nearest future expiry from the data.
    # Prefer the first expiry *after* today because 0DTE gamma is always zero,
    # making GEX analysis meaningless.  Fall back to today if nothing later.
    today = date.today().isoformat()
    expiries = sorted({r.expiry for r in all_rows if r.expiry})
    if not expiries:
        logger.warning("[options:%s] CSV parsed but no expiry info found in rows", symbol)
        return all_rows

    nearest = next((d for d in expiries if d > today), None)
    if nearest is None:
        nearest = next((d for d in expiries if d >= today), expiries[-1])
    logger.info(
        "[options:%s] %d total expiries found, auto-selected nearest: %s",
        symbol, len(expiries), nearest,
    )
    filtered = [r for r in all_rows if r.expiry == nearest]
    logger.info("[options:%s] filtered to %d rows for expiry %s", symbol, len(filtered), nearest)
    return filtered


def _normalise_date(raw: str) -> str:
    """Convert M/D/YYYY or similar to YYYY-MM-DD.  Pass through if already ISO."""
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return raw


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

    col_map: dict[str, str] = {}
    for raw_header in reader.fieldnames:
        key = raw_header.strip().lower()
        if key in _COLUMN_MAP:
            col_map[raw_header] = _COLUMN_MAP[key]

    logger.info("[options:%s] CSV headers: %s", symbol, list(reader.fieldnames))
    logger.info("[options:%s] mapped columns: %s", symbol, {v: k for k, v in col_map.items()})

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

        # Expiry: prefer explicit column, then contract name, then fallback
        row_expiry = mapped.get("expiry") or ""
        if row_expiry:
            row_expiry = _normalise_date(row_expiry)
        if not row_expiry:
            contract = mapped.get("contract_name", "").strip().strip('"')
            m = _CONTRACT_RE.match(contract)
            if m:
                yy, mm, dd = m.group(1), m.group(2), m.group(3)
                row_expiry = f"20{yy}-{mm}-{dd}"

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
            expiry=row_expiry or fallback_expiry,
        ))

    has_gamma = any(r.gamma is not None and r.gamma != 0.0 for r in rows)
    logger.info("[options:%s] parsed %d rows, gamma data: %s", symbol, len(rows), "yes" if has_gamma else "no")
    return rows
