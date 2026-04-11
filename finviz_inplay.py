"""FinViz Elite in-play screener: news today/yesterday + liquidity + rel vol (v=152 export)."""

from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

from fetch_elite import _get_api_key, _normalize_row, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import _fmt_volume_cell

logger = logging.getLogger(__name__)

# Filters: news today|yesterday, avg vol >1M, current vol >500K, price >$1, rel vol >1.5
INPLAY_F = "news_date_today|yesterday,sh_avgvol_o1000,sh_curvol_o500,sh_price_o1,sh_relvol_o1.5"

INPLAY_ROW_LIMIT = 20
# Discord embed description max 4096; leave margin for truncation notice
_INPLAY_DESC_MAX = 3900


def inplay_screener_url() -> str:
    """User-facing FinViz screener link (v=151 as in browser)."""
    q = urlencode({"v": "151", "f": INPLAY_F, "ft": "4", "o": "-change"})
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


def _news_url_from_raw(raw: dict) -> str:
    for k, v in raw.items():
        if str(k).strip().lower() == "news url" and v is not None:
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return ""


def _fallback_news_url(ticker: str) -> str:
    return f"https://elite.finviz.com/quote.ashx?t={quote(ticker, safe='')}&p=n"


def fetch_inplay_rows(limit: int | None = None) -> tuple[list[dict], str]:
    """Fetch in-play screen rows and the screener URL for embeds.

    Each row includes: ticker, price, change, volume (formatted shares), news_url.
    """
    cap = limit if limit is not None else INPLAY_ROW_LIMIT
    screener = inplay_screener_url()
    if not _get_api_key():
        logger.error("FINVIZ_API_KEY not set; cannot fetch inplay export")
        return [], screener

    url = _inplay_export_url()
    raw_rows = fetch_csv_export(url, caller="inplay", timeout=120)
    if not raw_rows:
        logger.warning("inplay export returned no rows")
        return [], screener

    out: list[dict] = []
    seen: set[str] = set()
    for raw in raw_rows:
        normed = _normalize_row(raw)
        t = normed.get("ticker") or ""
        if not t or t in seen:
            continue
        seen.add(t)
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
