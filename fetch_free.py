"""Fetch scan data from the free finviz.com using the unofficial finviz library.

Uses the `finviz` package (mariostoev/finviz) which scrapes the HTML screener
pages — no CSV export or API key required.

Converts the Elite screener URLs stored in scan_registry.py to free finviz.com
URLs, strips Elite-only filters (tad_*), and uses Screener.init_from_url().

Fully self-contained — zero dependency on the Market Metrics Dashboard.

Limitations vs Elite:
  - Elite-only tad_* (custom technical analysis descriptor) filters are stripped.
  - Some advanced filters may behave differently or be unavailable on free.
  - Rate limiting is stricter; built-in delays are used between pages.
  - Results are "best effort" parity, not identical to Elite.
"""

import logging
import os
import random
import re
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger(__name__)

os.environ["DISABLE_TQDM"] = "1"

from finviz.screener import Screener

_DELAY_SEC = float(os.environ.get("FINVIZ_FREE_DELAY_SEC", "5"))
_MAX_RETRIES = 3


def _elite_url_to_free_screener(url: str) -> str:
    """Convert an elite.finviz.com URL to a free finviz.com screener URL.

    - Swaps host from elite.finviz.com to finviz.com
    - Changes /export.ashx to /screener.ashx if present
    - Strips auth param
    - Strips Elite-only tad_* filters
    - Removes custom column param (c=) since the Screener handles that
    """
    parsed = urlparse(url)

    host = parsed.hostname or ""
    new_netloc = parsed.netloc.replace(host, "finviz.com")

    path = parsed.path
    if "/export.ashx" in path:
        path = path.replace("/export.ashx", "/screener.ashx")

    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("auth", None)
    qs.pop("c", None)

    if "f" in qs:
        filters_str = qs["f"][0] if qs["f"] else ""
        cleaned = ",".join(
            f for f in filters_str.split(",")
            if not f.startswith("tad_")
        )
        if cleaned:
            qs["f"] = [cleaned]
        else:
            qs.pop("f", None)

    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(netloc=new_netloc, path=path, query=new_query))


def _screener_table_from_v(url: str) -> str:
    """Infer the Screener table name from the v= parameter in the URL."""
    qs = parse_qs(urlparse(url).query)
    v = qs.get("v", ["141"])[0]
    first_two = v[:2] if len(v) >= 2 else v
    table_map = {
        "11": "Overview",
        "12": "Valuation",
        "13": "Ownership",
        "14": "Performance",
        "15": "Custom",
        "16": "Financial",
        "17": "Technical",
    }
    return table_map.get(first_two, "Performance")


def _normalize_row(raw: dict) -> dict:
    """Normalize a finviz Screener row dict to the standard shape for Discord embeds."""
    def _v(*candidates):
        for c in candidates:
            val = raw.get(c)
            if val is not None and str(val).strip() not in ("", "-"):
                return str(val).strip()
        return ""

    ticker = _v("Ticker", "ticker")
    price = _v("Price", "price")
    change = _v("Change", "change")
    volume = _v("Volume", "volume")
    rel_vol = _v("Relative Volume", "Rel Volume", "rel_volume")
    avg_vol = _v("Average Volume", "Avg Volume", "avg_volume")

    if not rel_vol and volume and avg_vol:
        v_num, a_num = _parse_num(volume), _parse_num(avg_vol)
        if v_num and a_num and a_num != 0:
            rel_vol = f"{v_num / a_num:.2f}"

    atr_raw = _v("ATR", "Average True Range")
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

    mcap = _v("Market Cap", "Market Cap.")
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


def _change_sort_key(row: dict) -> float:
    val = row.get("change")
    if val is None or val == "":
        return 0.0
    try:
        return float(str(val).replace("%", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _fetch_with_screener(url: str, caller: str = "") -> list[dict]:
    """Use the finviz Screener to fetch rows, with retry + backoff for 429s."""
    free_url = _elite_url_to_free_screener(url)
    logger.info("[%s] Scraping: %s", caller, free_url)

    for attempt in range(_MAX_RETRIES):
        try:
            screener = Screener.init_from_url(free_url)
            raw_rows = list(screener)
            logger.info("[%s] Got %d rows from screener", caller, len(raw_rows))
            return raw_rows
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "Too Many Requests" in err_str
            is_no_results = "No results" in err_str

            if is_no_results:
                logger.info("[%s] No results for this scan (may be expected)", caller)
                return []

            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                wait = (2 ** attempt) * 10 + random.uniform(2, 6)
                logger.warning("[%s] Rate limited (429), waiting %.0fs before retry %d/%d",
                               caller, wait, attempt + 2, _MAX_RETRIES)
                time.sleep(wait)
                continue

            logger.error("[%s] Screener failed (attempt %d/%d): %s",
                         caller, attempt + 1, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(5 + random.uniform(1, 3))
                continue
            return []

    return []


def fetch_scan(scan_def) -> list[dict]:
    """Fetch scan data using the free finviz Screener for the given ScanDef.

    Iterates over the scan's export_urls (converting them to screener URLs), or
    *screener_url* alone when export_urls is empty (e.g. Top Gainers / Losers).
    Deduplicates tickers, sorts by daily change (gainers: highest first; losers: lowest first),
    returns top 50.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    urls = scan_def.export_urls
    if not urls and scan_def.screener_url:
        urls = [scan_def.screener_url]

    for i, url in enumerate(urls):
        if i > 0:
            time.sleep(_DELAY_SEC)

        raw_rows = _fetch_with_screener(url, caller=scan_def.scan_id)
        for r in raw_rows:
            normed = _normalize_row(r)
            t = normed.get("ticker", "")
            if t and t not in seen:
                seen.add(t)
                rows.append(normed)

    mk = getattr(scan_def, "movers_kind", None)
    reverse = mk != "losers"
    rows.sort(key=_change_sort_key, reverse=reverse)
    return rows[:50]


def fetch_scan_with_screener(scan_def) -> tuple[list[dict], str]:
    """Same as :func:`fetch_scan` but also returns *scan_def.screener_url* for embed links."""
    rows = fetch_scan(scan_def)
    return rows, scan_def.screener_url or ""
