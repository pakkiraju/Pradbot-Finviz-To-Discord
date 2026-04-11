"""Single-ticker snapshot from FinViz Elite v=152 export (one CSV row).

Uses ``&t=TICKER`` on export.ashx with the same column bundle as /earnings and heatmaps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

from fetch_elite import _get_api_key, _parse_num, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import _cell, _fmt_volume_cell

logger = logging.getLogger(__name__)


@dataclass
class V152TickerSnapshot:
    """Display-ready strings; use empty string for missing."""

    pe: str
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
    """Shares float: FinViz often uses K/M/B or bare millions — reuse volume-style parse."""
    return _fmt_volume_cell(raw)


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
        avg_vol_display=_fmt_volume_cell(avg_raw) if avg_raw else "—",
        rel_vol_display=rel_raw if rel_raw else "—",
        shares_float_display=_float_display(float_raw) if float_raw else "—",
        short_float_display=_short_float_display(sf_raw),
        gap_raw=gap_raw,
    )
