"""Fetch scan data via FinViz Elite — fully self-contained.

Reads FINVIZ_API_KEY from .env in this folder. Fetches CSV directly from
elite.finviz.com/export.ashx with auth= query param. No dependency on the
Market Metrics Dashboard codebase.
"""

import csv
import io
import logging
import os
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ENV_LOADED = False
_MAX_RETRIES = 4
_DELAY_SEC = float(os.environ.get("FINVIZ_ELITE_DELAY_SEC", "1.5"))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
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


def _append_auth(url: str) -> str:
    """Append auth=API_KEY to the URL."""
    api_key = _get_api_key()
    if not api_key:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}auth={api_key}"


def _fetch_csv(url: str, caller: str = "") -> list[dict]:
    """GET a CSV export URL with retries and return list of row dicts."""
    authed_url = _append_auth(url)

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(authed_url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[%s] 429 rate limit, waiting %ds (attempt %d)", caller, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[%s] request failed: %s (attempt %d)", caller, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[%s] failed after %d retries", caller, _MAX_RETRIES)
        return []

    text = resp.text.strip().lstrip("\ufeff")
    if text.startswith("<"):
        if "login" in text[:2000].lower() or "sign in" in text[:2000].lower():
            logger.error("[%s] FinViz returned login page — check FINVIZ_API_KEY in .env", caller)
        else:
            logger.warning("[%s] got HTML instead of CSV", caller)
        return []

    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        for r in rows:
            if "ticker" in r and "Ticker" not in r:
                r["Ticker"] = r["ticker"]
        return rows
    except Exception as e:
        logger.warning("[%s] CSV parse failed: %s", caller, e)
        return []


def _parse_num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "").replace("%", "")
    if not s or s in ("-", "\u2014"):
        return None
    m = re.match(r"([\d.-]+)\s*([KMB])?", s, re.I)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    val = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    if suffix == "K":
        val *= 1e3
    elif suffix == "M":
        val *= 1e6
    elif suffix == "B":
        val *= 1e9
    return val


def _normalize_row(raw: dict) -> dict:
    """Normalize a FinViz CSV row into the standard shape used by Discord embeds."""
    def _v(*candidates):
        for c in candidates:
            val = raw.get(c)
            if val is not None and str(val).strip() not in ("", "-"):
                return str(val).strip()
        return ""

    ticker = _v("Ticker", "ticker")
    price = _v("Price", "price", "Last", "Close")
    change = _v("Change", "change")
    volume = _v("Volume", "volume")
    avg_vol = _v("Average Volume", "Avg Volume", "avg_volume", "Avg Vol")
    rel_vol = _v("Relative Volume", "Rel Volume", "rel_volume", "Rel Vol")
    atr_raw = _v("ATR", "atr", "Average True Range")

    if not rel_vol and volume and avg_vol:
        v_num, a_num = _parse_num(volume), _parse_num(avg_vol)
        if v_num and a_num and a_num != 0:
            rel_vol = f"{v_num / a_num:.2f}"

    atr_val = _parse_num(atr_raw)
    price_num = _parse_num(price)
    atr_pct = round(atr_val / price_num * 100, 2) if atr_val and price_num and price_num != 0 else None

    row = {
        "ticker": ticker.upper(),
        "price": price,
        "change": change,
        "volume": volume,
        "avg_vol": avg_vol,
        "rel_vol": rel_vol,
        "atr_pct": atr_pct,
    }

    mcap = _v("Market Cap", "market_cap", "MarketCap")
    if mcap:
        row["mkt_cap"] = mcap
        mc_num = _parse_num(mcap)
        if mc_num:
            row["market_cap"] = mc_num
    sf = _v("Short Float", "Short Interest")
    if sf:
        row["short_float_pct"] = sf if "%" in sf else f"{sf}%"
    roe = _v("ROE", "Return on Equity")
    if roe:
        pct = _parse_num(roe)
        if pct is not None:
            row["roe"] = pct
    margin = _v("Net Profit Margin", "Profit Margin", "Net Margin")
    if margin:
        pct = _parse_num(margin)
        if pct is not None:
            row["net_margin"] = pct
    ed = _v("Earnings Date", "Earnings")
    if ed:
        row["earnings_date"] = ed

    return row


def fetch_scan(scan_def) -> list[dict]:
    """Fetch scan data from FinViz Elite for the given ScanDef.

    Iterates over scan_def.export_urls, fetches CSV from each, normalizes
    rows, and deduplicates by ticker.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return []

    rows: list[dict] = []
    seen: set[str] = set()

    for i, url in enumerate(scan_def.export_urls):
        if i > 0:
            time.sleep(_DELAY_SEC)

        raw_rows = _fetch_csv(url, caller=scan_def.scan_id)
        for r in raw_rows:
            normed = _normalize_row(r)
            t = normed.get("ticker", "")
            if t and t not in seen:
                seen.add(t)
                rows.append(normed)

    rows.sort(key=_change_sort_key, reverse=True)
    return rows[:50]


def _change_sort_key(row: dict) -> float:
    val = row.get("change")
    if val is None or val == "":
        return 0.0
    try:
        return float(str(val).replace("%", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
