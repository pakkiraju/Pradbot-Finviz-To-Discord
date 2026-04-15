"""Single-ticker snapshot from FinViz Elite v=152 export (one CSV row).

Uses ``&t=TICKER`` on export.ashx with the same column bundle as /earnings and heatmaps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from fetch_elite import _get_api_key, _parse_num, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import _cell, _fmt_finviz_float_shares, _fmt_volume_cell

logger = logging.getLogger(__name__)


def _fmt_compact_kmbt(n: float) -> str:
    """Compact K / M / B / T for market cap (USD) or share float (shares)."""
    if n == 0:
        return "0"
    abs_n = abs(n)
    if abs_n >= 1e12:
        return f"{n / 1e12:.2f}T"
    if abs_n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if abs_n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if abs_n >= 1e3:
        return f"{n / 1e3:.2f}K"
    return f"{n:.2f}"


def _mcap_cell_to_usd(raw: str) -> float | None:
    """Parse FinViz market cap to USD (same rules as in-play earnings).

    Plain numbers **without** K/M/B/T are usually **millions of USD** (e.g. ``2467000`` → ~$2.47T).
    Treating them as full dollars yields bogus labels like ``2.67M`` for megacaps.
    Plain values ``>= 10_000_000`` are treated as **full USD** (some exports use expanded dollars).
    """
    o = (raw or "").strip().replace("\u00a0", " ")
    if not o or o in "-—":
        return None
    m = re.match(r"^([\d.,]+)\s*T\s*$", o.replace(",", "").strip(), re.I)
    if m:
        return float(m.group(1)) * 1e12
    up = o.upper()
    has_kmb = bool(re.search(r"[0-9][\s]*[KMB]\s*$", up.replace(",", "")))
    n = _parse_num(o)
    if n is None:
        return None
    if has_kmb:
        return n
    if n >= 10_000_000:
        return n
    return n * 1e6


def _compact_mcap_display(raw: str) -> str:
    """Market cap: FinViz K/M/B/T or plain millions; normalize to short suffix form."""
    s = (raw or "").strip()
    if not s or s in "-—":
        return "—"
    usd = _mcap_cell_to_usd(s)
    if usd is not None and usd > 0:
        return _fmt_compact_kmbt(usd)
    return s[:22]


def _compact_float_shares_display(raw: str) -> str:
    """Shares float: FinViz-specific scale (not volume); compact K/M/B/T."""
    return _fmt_finviz_float_shares(raw)


@dataclass
class V152TickerSnapshot:
    """Display-ready strings; use empty string for missing."""

    pe: str
    market_cap_display: str
    sector: str
    industry: str
    sector_theme: str
    country: str
    avg_vol_display: str
    rel_vol_display: str
    shares_float_display: str
    short_float_display: str
    gap_raw: str


def _export_url_ticker(ticker: str) -> str:
    q = quote(ticker, safe=".-")
    return (
        f"https://elite.finviz.com/export.ashx?v=152&ft=4&t={q}&o=-volume&c={V152_EXPORT_COLUMNS}"
    )


def _float_display(raw: str) -> str:
    """Shares float: FinViz often uses thousands or K/M/B — compact K/M/B/T."""
    return _compact_float_shares_display(raw)


def _short_float_display(raw: str) -> str:
    s = (raw or "").strip()
    if not s or s in "-—":
        return "—"
    if "%" in s:
        return s
    return f"{s}%"


def fetch_v152_ticker_snapshot(ticker: str) -> V152TickerSnapshot | None:
    """Fetch one v=152 row for *ticker* (uppercase). Returns None if missing or on failure."""
    if not _get_api_key():
        logger.error("FINVIZ_API_KEY not set; cannot fetch v152 ticker snapshot")
        return None
    sym = ticker.strip().upper()
    url = _export_url_ticker(sym)
    rows = fetch_csv_export(url, caller=f"v152_ticker:{sym}", timeout=60)
    if not rows:
        q = quote(sym, safe=".-")
        alt = f"https://elite.finviz.com/export.ashx?v=152&t={q}&o=-volume&c={V152_EXPORT_COLUMNS}"
        logger.info("v152 ticker %s: retry without ft=4", sym)
        rows = fetch_csv_export(alt, caller=f"v152_ticker_alt:{sym}", timeout=60)
    if not rows:
        logger.warning("v152 ticker snapshot: no rows for %s", sym)
        return None

    raw = rows[0]
    tick = _cell(raw, "Ticker", "ticker").upper()
    if tick and tick != sym:
        for r in rows:
            t = _cell(r, "Ticker", "ticker").upper()
            if t == sym:
                raw = r
                break

    pe = _cell(raw, "P/E", "P/E (TTM)", "PE", "pe")
    mcap_raw = _cell(raw, "Market Cap", "market_cap", "MarketCap", "Market Cap.")
    sector = _cell(raw, "Sector", "sector")
    industry = _cell(raw, "Industry", "industry", "Industry ")
    sector_theme = _cell(
        raw,
        "Sector/Theme",
        "Sector/Theme AscDesc",
        "sector_theme",
        "Sector Theme",
    )
    country = _cell(raw, "Country", "country")
    vol_raw = _cell(raw, "Volume", "volume")
    avg_raw = _cell(raw, "Average Volume", "Avg Volume", "Average volume", "Avg Vol")
    rel_raw = _cell(raw, "Relative Volume", "Rel Volume", "rel_volume", "Rel Vol")
    if not rel_raw and vol_raw and avg_raw:
        v_num, a_num = _parse_num(vol_raw), _parse_num(avg_raw)
        if v_num and a_num and a_num != 0:
            rel_raw = f"{v_num / a_num:.2f}"
    float_raw = _cell(raw, "Shares Float", "Float", "shares float")
    sf_raw = _cell(raw, "Short Float", "Short Interest", "Short float")

    gap_raw = _cell(
        raw,
        "Gap",
        "Performance (Gap)",
        "Today Performance (Gap)",
        "Gap %",
    )

    return V152TickerSnapshot(
        pe=pe or "—",
        market_cap_display=_compact_mcap_display(mcap_raw) if mcap_raw else "—",
        sector=((sector or "").strip()[:80] or "—"),
        industry=((industry or "").strip()[:80] or "—"),
        sector_theme=((sector_theme or "").strip()[:80] or "—"),
        country=((country or "").strip()[:42] or "—"),
        avg_vol_display=_fmt_volume_cell(avg_raw) if avg_raw else "—",
        rel_vol_display=rel_raw if rel_raw else "—",
        shares_float_display=_float_display(float_raw) if float_raw else "—",
        short_float_display=_short_float_display(sf_raw),
        gap_raw=gap_raw,
    )
