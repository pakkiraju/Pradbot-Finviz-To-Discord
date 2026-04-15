"""FinViz Elite in-play screener: news today/yesterday + liquidity + rel vol (v=152 export)."""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import quote, urlencode

from fetch_elite import _get_api_key, _normalize_row, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import _fmt_finviz_float_shares, _fmt_volume_cell

logger = logging.getLogger(__name__)

# Filters: news today|yesterday, avg vol >1M, current vol >500K, price >$1, rel vol >1.5
INPLAY_F = "news_date_today|yesterday,sh_avgvol_o1000,sh_curvol_o500,sh_price_o1,sh_relvol_o1.5"

# Small caps: market cap $5M–$2B, current vol >1M, rel vol >1.5 (no news filter)
INPLAY_SMALLCAP_F = "cap_0.005to2,sh_curvol_o1000,sh_relvol_o1.5"

INPLAY_ROW_LIMIT = 20
# Discord embed description max 4096; leave margin for truncation notice
_INPLAY_DESC_MAX = 3900

InplayMode = Literal["default", "smallcaps"]


def inplay_screener_url() -> str:
    """User-facing FinViz screener link (v=151 as in browser)."""
    q = urlencode({"v": "151", "f": INPLAY_F, "ft": "4", "o": "-change"})
    return f"https://elite.finviz.com/screener.ashx?{q}"


def inplay_smallcap_screener_url() -> str:
    """User-facing v=152 screener (same column layout as export)."""
    q = urlencode(
        {
            "v": "152",
            "f": INPLAY_SMALLCAP_F,
            "ft": "4",
            "o": "-change",
            "c": V152_EXPORT_COLUMNS,
        }
    )
    return f"https://elite.finviz.com/screener.ashx?{q}"


def _inplay_export_url() -> str:
    # v=152 + full custom columns so CSV includes News URL (indices vary by filter for smaller c=).
    q = urlencode(
        {
            "v": "152",
            "f": INPLAY_F,
            "ft": "4",
            "o": "-change",
            "c": V152_EXPORT_COLUMNS,
        }
    )
    return f"https://elite.finviz.com/export.ashx?{q}"


def _inplay_smallcap_export_url() -> str:
    q = urlencode(
        {
            "v": "152",
            "f": INPLAY_SMALLCAP_F,
            "ft": "4",
            "o": "-change",
            "c": V152_EXPORT_COLUMNS,
        }
    )
    return f"https://elite.finviz.com/export.ashx?{q}"


def _news_url_from_raw(raw: dict) -> str:
    for k, v in raw.items():
        if str(k).strip().lower() == "news url" and v is not None:
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return ""


def _fallback_news_url(ticker: str) -> str:
    return f"https://elite.finviz.com/quote.ashx?t={quote(ticker, safe='')}&p=n"


def _cell_from_raw(raw: dict, *names: str) -> str:
    """First non-empty CSV cell whose header matches one of *names* (case-insensitive)."""
    want = {n.lower() for n in names}
    for k, v in raw.items():
        if str(k).strip().lower() in want and v is not None:
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return ""


def _fmt_float_shares_display(raw: str) -> str:
    """Shares float — parsed via :func:`finviz_earnings._fmt_finviz_float_shares`."""
    return _fmt_finviz_float_shares(raw)


def _enrich_smallcap_row(raw: dict, normed: dict) -> None:
    """Add display fields for small-cap table (mutates *normed*)."""
    normed["country"] = _cell_from_raw(raw, "country")
    raw_float = _cell_from_raw(raw, "shares float") or _cell_from_raw(raw, "float")
    normed["float_display"] = _fmt_float_shares_display(raw_float)
    sf = _cell_from_raw(raw, "short float", "short interest")
    if not sf:
        sf = str(normed.get("short_float_pct") or "").strip()
    normed["short_float_display"] = sf if sf else "—"
    mc = str(normed.get("mkt_cap") or "").strip()
    if not mc:
        mc = _cell_from_raw(raw, "market cap")
    normed["mcap_display"] = mc if mc else "—"


def fetch_inplay_rows(
    limit: int | None = None,
    *,
    mode: InplayMode = "default",
) -> tuple[list[dict], str]:
    """Fetch in-play screen rows and the screener URL for embeds.

    *mode* ``default``: news + liquidity screen; each row includes ``news_url``.
    *mode* ``smallcaps``: cap $5M–$2B + cur vol + rel vol; extra country / float (K/M/B) / cap fields; ``news_url`` from v=152 export like default.
    """
    cap = limit if limit is not None else INPLAY_ROW_LIMIT
    if mode == "smallcaps":
        screener = inplay_smallcap_screener_url()
        export_url = _inplay_smallcap_export_url()
        caller = "inplay_smallcap"
    else:
        screener = inplay_screener_url()
        export_url = _inplay_export_url()
        caller = "inplay"

    if not _get_api_key():
        logger.error("FINVIZ_API_KEY not set; cannot fetch inplay export")
        return [], screener

    raw_rows = fetch_csv_export(export_url, caller=caller, timeout=120)
    if not raw_rows:
        logger.warning("%s export returned no rows", caller)
        return [], screener

    out: list[dict] = []
    seen: set[str] = set()
    for raw in raw_rows:
        normed = _normalize_row(raw)
        t = normed.get("ticker") or ""
        if not t or t in seen:
            continue
        seen.add(t)
        if mode == "smallcaps":
            _enrich_smallcap_row(raw, normed)
        nu = _news_url_from_raw(raw)
        if not nu:
            nu = _fallback_news_url(t)
        elif nu.startswith("/"):
            nu = "https://elite.finviz.com" + nu
        elif not nu.startswith("http"):
            nu = "https://" + nu.lstrip("/")
        normed["news_url"] = nu
        # v=152 Volume is full shares for large counts; use same heuristic as /earnings.
        normed["volume"] = _fmt_volume_cell(str(normed.get("volume") or ""))
        out.append(normed)
        if len(out) >= cap:
            break

    return out, screener


def _table_cell(s: str, *, max_len: int = 14) -> str:
    """Safe single cell: no pipes/newlines (breaks Markdown tables)."""
    t = str(s).replace("|", "/").replace("\n", " ").strip()
    if not t:
        t = "—"
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t


def format_inplay_description(rows: list[dict], *, max_chars: int = _INPLAY_DESC_MAX) -> str:
    """GitHub-style Markdown table; last column is [news](url) (clickable; not inside a code block)."""
    if not rows:
        return "*No stocks matched this screen.*"

    header = "| Symbol | Price | Change | Vol | News |"
    out_lines: list[str] = [header]
    total = len(header)

    for r in rows:
        raw_tk = (r.get("ticker") or "").strip()
        tk = _table_cell(raw_tk, max_len=8)
        pr = _table_cell(r.get("price") or "—", max_len=10)
        ch = _table_cell(r.get("change") or "—", max_len=10)
        vo = _table_cell(r.get("volume") or "—", max_len=10)
        nu = (r.get("news_url") or "").strip()
        if not nu:
            nu = _fallback_news_url(raw_tk or tk)
        link = f"[news]({nu})"
        row = f"| {tk} | {pr} | {ch} | {vo} | {link} |"
        if total + len(row) + 1 > max_chars:
            out_lines.append("| … | … | … | … | *truncated* |")
            break
        out_lines.append(row)
        total += len(row) + 1

    return "\n".join(out_lines)


def format_inplay_smallcap_description(rows: list[dict], *, max_chars: int = _INPLAY_DESC_MAX) -> str:
    """Wide Markdown table: core quotes + country, market cap, float (K/M/B), short float, news link."""
    if not rows:
        return "*No stocks matched this screen.*"

    header = "| Sym | Price | Chg | Vol | Country | MCap | Float | Shrt% | News |"
    out_lines: list[str] = [header]
    total = len(header)

    for r in rows:
        raw_tk = (r.get("ticker") or "").strip()
        tk = _table_cell(raw_tk, max_len=5)
        pr = _table_cell(r.get("price") or "—", max_len=6)
        ch = _table_cell(r.get("change") or "—", max_len=6)
        vo = _table_cell(r.get("volume") or "—", max_len=7)
        co = _table_cell(r.get("country") or "—", max_len=6)
        mc = _table_cell(r.get("mcap_display") or "—", max_len=6)
        fl = _table_cell(r.get("float_display") or "—", max_len=7)
        sh = _table_cell(r.get("short_float_display") or "—", max_len=5)
        nu = (r.get("news_url") or "").strip()
        if not nu:
            nu = _fallback_news_url(raw_tk or tk)
        link = f"[news]({nu})"
        row = f"| {tk} | {pr} | {ch} | {vo} | {co} | {mc} | {fl} | {sh} | {link} |"
        if total + len(row) + 1 > max_chars:
            out_lines.append("| … | … | … | … | … | … | … | … | … | *truncated* |")
            break
        out_lines.append(row)
        total += len(row) + 1

    return "\n".join(out_lines)
