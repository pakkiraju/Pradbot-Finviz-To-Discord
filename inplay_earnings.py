"""In-play earnings screen: FinViz Elite earnings-date filter + Massive extended-hours % vs 21d avg vol."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fetch_elite import _get_api_key, _normalize_row, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import _fmt_shares_compact, _fmt_volume_cell, _finviz_thousands_to_shares
from massive_rest import (
    fetch_daily_bars,
    get_massive_api_key,
    sum_minute_volume,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# FinViz: earnings AMC/BMO + avg vol >1M + price >$1 (matches v=151 screener)
EARNINGS_F = "earningsdate_yesterdayafter|todaybefore,sh_avgvol_o1000,sh_price_o1"

INPLAY_EARNINGS_CANDIDATE_CAP = 150
_INPLAY_EARNINGS_DESC_MAX = 3900
_EAVOL_STRONG_PCT = 20.0

_MAX_WORKERS = 8


def earnings_screener_url() -> str:
    """User-facing v=151 screener (same filters as browser link)."""
    q = urlencode({"v": "151", "f": EARNINGS_F, "ft": "4"})
    return f"https://elite.finviz.com/screener.ashx?{q}"


def _earnings_export_url() -> str:
    """v=152 CSV export with full columns; same *f=* filters as screener."""
    q = urlencode(
        {
            "v": "152",
            "f": EARNINGS_F,
            "ft": "4",
            "o": "-change",
            "c": V152_EXPORT_COLUMNS,
        }
    )
    return f"https://elite.finviz.com/export.ashx?{q}"


def _fmt_volume_cell_v152(raw: str) -> str:
    """v=152 Volume: bare integers are **full shares**; decimals / K/M/B use thousands rules."""
    s = (raw or "").strip().replace(",", "")
    if not s or s in "-—":
        return "—"
    if re.match(r"^\d+$", s):
        try:
            return _fmt_shares_compact(float(s))
        except ValueError:
            return (raw or "—")[:12]
    sh = _finviz_thousands_to_shares(s)
    if sh is not None:
        return _fmt_shares_compact(sh)
    return _fmt_volume_cell(raw)


def _ms_to_et_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=ET).date()


def _et_today() -> date:
    return datetime.now(ET).date()


def trading_session_dates_from_spy() -> tuple[date, date] | None:
    """(prior_trading_date, current_trading_date) in America/New_York from SPY daily bars."""
    end = _et_today()
    start = end - timedelta(days=45)
    bars = fetch_daily_bars("SPY", start.isoformat(), end.isoformat(), caller="spy_sessions")
    if not bars:
        logger.error("inplay_earnings: SPY daily bars empty — cannot resolve sessions")
        return None
    dates: list[date] = []
    for b in bars:
        t = b.get("t")
        if t is not None:
            dates.append(_ms_to_et_date(int(t)))
    uniq = sorted(set(dates))
    if len(uniq) < 2:
        logger.error("inplay_earnings: fewer than 2 SPY sessions in window")
        return None
    return uniq[-2], uniq[-1]


def _et_range_ms(day: date, h1: int, m1: int, h2: int, m2: int) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, h1, m1, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, h2, m2, tzinfo=ET)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _avg_daily_volume_21(ticker: str, et_today: date) -> float | None:
    start = et_today - timedelta(days=120)
    bars = fetch_daily_bars(
        ticker,
        start.isoformat(),
        et_today.isoformat(),
        caller=f"avg21_{ticker}",
    )
    if not bars:
        return None
    dated: list[tuple[date, dict[str, Any]]] = []
    for b in bars:
        t = b.get("t")
        if t is None:
            continue
        dated.append((_ms_to_et_date(int(t)), b))
    completed = [b for d, b in dated if d < et_today]
    if len(completed) < 21:
        completed = [b for d, b in dated]
        if len(completed) >= 22 and dated[-1][0] == et_today:
            completed = completed[:-1]
    if len(completed) < 21:
        return None
    last21 = completed[-21:]
    return sum(float(b["v"]) for b in last21) / 21.0


def _enrich_one_ticker(
    normed: dict[str, Any],
    *,
    ticker: str,
    et_today: date,
    ah_lo: int,
    ah_hi: int,
    pm_lo: int,
    pm_hi: int,
) -> dict[str, Any]:
    out = dict(normed)
    out["volume"] = _fmt_volume_cell_v152(str(normed.get("volume") or ""))
    out["pct_eavol"] = None
    out["eavol_ge_20"] = False
    try:
        avg = _avg_daily_volume_21(ticker, et_today)
        if avg is None or avg <= 0:
            return out
        ah = sum_minute_volume(ticker, ah_lo, ah_hi, caller=f"eavol_ah_{ticker}")
        pm = sum_minute_volume(ticker, pm_lo, pm_hi, caller=f"eavol_pm_{ticker}")
        ext = ah + pm
        pct = (ext / avg) * 100.0
        out["pct_eavol"] = pct
        out["eavol_ge_20"] = pct >= _EAVOL_STRONG_PCT
    except Exception as e:
        logger.warning("inplay_earnings %s: %s", ticker, e)
    return out


def fetch_inplay_earnings_rows() -> tuple[list[dict[str, Any]], str]:
    """FinViz earnings universe + Massive overnight %EAVOL; sorted by %EAVOL desc (missing last)."""
    screener = earnings_screener_url()
    if not _get_api_key():
        logger.error("FINVIZ_API_KEY not set; cannot fetch inplay earnings export")
        return [], screener
    if not get_massive_api_key():
        logger.error("MASSIVE_API_KEY not set; cannot compute extended-hours metrics")
        return [], screener

    sessions = trading_session_dates_from_spy()
    if not sessions:
        return [], screener
    prior_d, current_d = sessions

    ah_lo, ah_hi = _et_range_ms(prior_d, 16, 0, 20, 0)
    pm_lo, pm_hi = _et_range_ms(current_d, 4, 0, 9, 30)
    et_day = _et_today()

    raw_rows = fetch_csv_export(_earnings_export_url(), caller="inplay_earnings", timeout=120)
    if not raw_rows:
        logger.warning("inplay_earnings export returned no rows")
        return [], screener

    candidates: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        normed = _normalize_row(raw)
        t = (normed.get("ticker") or "").strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        candidates.append((t, normed))
        if len(candidates) >= INPLAY_EARNINGS_CANDIDATE_CAP:
            break

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = {
            ex.submit(
                _enrich_one_ticker,
                n,
                ticker=t,
                et_today=et_day,
                ah_lo=ah_lo,
                ah_hi=ah_hi,
                pm_lo=pm_lo,
                pm_hi=pm_hi,
            ): t
            for t, n in candidates
        }
        for fut in as_completed(futs):
            enriched.append(fut.result())

    def _sort_key(r: dict[str, Any]) -> tuple:
        p = r.get("pct_eavol")
        if p is None:
            return (1, 0.0, (r.get("ticker") or "").upper())
        return (0, -float(p), (r.get("ticker") or "").upper())

    enriched.sort(key=_sort_key)
    return enriched, screener


def _table_cell(s: str, *, max_len: int = 14) -> str:
    t = str(s).replace("|", "/").replace("\n", " ").strip()
    if not t:
        t = "—"
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t


def format_inplay_earnings_description(
    rows: list[dict[str, Any]],
    *,
    max_chars: int = _INPLAY_EARNINGS_DESC_MAX,
) -> str:
    if not rows:
        return "*No stocks matched this FinViz earnings screen.*"

    legend = (
        "**%EAVOL ≥ 20%** is shown in **bold**; all rows sorted by %EAVOL (highest first; "
        "“—” if not computed)."
    )
    header = "| Symbol | Price | Change | Vol | %EAVOL |"
    out_lines: list[str] = [legend, "", header]
    total = sum(len(x) + 1 for x in out_lines)

    for r in rows:
        raw_tk = (r.get("ticker") or "").strip()
        tk = _table_cell(raw_tk, max_len=8)
        pr = _table_cell(r.get("price") or "—", max_len=10)
        ch = _table_cell(r.get("change") or "—", max_len=10)
        vo = _table_cell(r.get("volume") or "—", max_len=12)
        pe = r.get("pct_eavol")
        strong = bool(r.get("eavol_ge_20"))
        if pe is not None:
            pct_s = f"{float(pe):.1f}%"
            if strong:
                pct_s = f"**{pct_s}**"
        else:
            pct_s = "—"
        pct_s = _table_cell(pct_s, max_len=18)
        row = f"| {tk} | {pr} | {ch} | {vo} | {pct_s} |"
        if total + len(row) + 1 > max_chars:
            out_lines.append("| … | … | … | … | … | *truncated* |")
            break
        out_lines.append(row)
        total += len(row) + 1

    return "\n".join(out_lines)
