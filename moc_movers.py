"""Market-on-close style movers from Massive: minute aggregates + optional trade refinement."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from typing import Any

from zoneinfo import ZoneInfo

from massive_rest import (
    fetch_daily_bars,
    fetch_grouped_daily,
    fetch_minute_bars,
    fetch_trades,
    get_massive_api_key,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# 3:49 PM – 4:05 PM ET: covers last RTH minutes + a little cushion
_MOC_FETCH_LO = (15, 49)
_MOC_FETCH_HI = (16, 5)

# Reference instant for "before the last 15 seconds" (ET on session day)
_CUT_HMS = (15, 59, 45)
_END_HMS = (16, 0, 0)

# Wider trade pull so we have a print at or before 3:59:45
_TRADE_PULL_LO = (15, 57, 0)

_DEFAULT_MAX_TICKERS = 500
_DEFAULT_WORKERS = 10
_REFINE_COUNT = 20


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def moc_max_tickers() -> int:
    return max(50, min(5000, _env_int("MOC_MAX_TICKERS", _DEFAULT_MAX_TICKERS)))


def moc_workers() -> int:
    return max(4, min(32, _env_int("MOC_WORKERS", _DEFAULT_WORKERS)))


def _ms_to_et_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=ET).date()


def _et_to_ns(d: date, h: int, m: int, s: int = 0) -> int:
    dt = datetime(d.year, d.month, d.day, h, m, s, tzinfo=ET)
    return int(dt.timestamp() * 1_000_000_000)


def _et_range_ms(d: date, lo: tuple[int, int], hi: tuple[int, int]) -> tuple[int, int]:
    a = datetime(d.year, d.month, d.day, lo[0], lo[1], 0, tzinfo=ET)
    b = datetime(d.year, d.month, d.day, hi[0], hi[1], 0, tzinfo=ET)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def _bar_start_et_parts(t_ms: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(t_ms / 1000, tz=ET)
    return dt.hour, dt.minute


def _pick_bar_close_by_start_hm(
    bars: list[dict[str, Any]], want_h: int, want_m: int
) -> float | None:
    """Close of the minute aggregate whose bar **start** in ET is want_h:want_m."""
    for b in bars:
        t = int(b.get("t", 0))
        h, m = _bar_start_et_parts(t)
        if h == want_h and m == want_m:
            c = b.get("c")
            if c is not None:
                return float(c)
    return None


def _pick_bar_ohlc_by_start_hm(
    bars: list[dict[str, Any]], want_h: int, want_m: int
) -> tuple[float | None, float | None]:
    for b in bars:
        t = int(b.get("t", 0))
        h, m = _bar_start_et_parts(t)
        if h == want_h and m == want_m:
            o, c = b.get("o"), b.get("c")
            if o is not None and c is not None:
                return float(o), float(c)
            if c is not None:
                return float(c), float(c)
    return None, None


def _last_rth_minute_ohlc(bars: list[dict[str, Any]]) -> tuple[float | None, float | None, int | None]:
    """Last regular-session minute (normally 3:59–4:00 PM ET bar); fallback if early close."""
    o, c = _pick_bar_ohlc_by_start_hm(bars, 15, 59)
    if o is not None and c is not None:
        return o, c, None
    best: tuple[int, float, float] | None = None
    for b in bars:
        t = int(b.get("t", 0))
        h, m = _bar_start_et_parts(t)
        if h < 9 or (h == 9 and m < 30):
            continue
        if h > 15 or (h == 15 and m > 59):
            continue
        bo, bc = b.get("o"), b.get("c")
        if bo is None or bc is None:
            continue
        if best is None or t > best[0]:
            best = (t, float(bo), float(bc))
    if best is None:
        return None, None, None
    return best[1], best[2], best[0]


def resolve_default_session_date() -> date:
    """Most recent **completed** regular session (uses SPY daily bars + ET clock)."""
    now = datetime.now(ET)
    today = now.date()
    start = today - timedelta(days=14)
    bars = fetch_daily_bars("SPY", start.isoformat(), today.isoformat(), caller="moc_session")
    if not bars:
        return today - timedelta(days=1)
    dates: list[date] = []
    for b in bars:
        t = b.get("t")
        if t is not None:
            dates.append(_ms_to_et_date(int(t)))
    uniq = sorted(set(dates))
    if not uniq:
        return today - timedelta(days=1)
    last_bar = uniq[-1]
    if last_bar == today and now.time() < time(16, 0):
        if len(uniq) >= 2:
            return uniq[-2]
        return last_bar
    return last_bar


def validate_session_date(d: date) -> str | None:
    """Return error message or None if OK."""
    if d.weekday() >= 5:
        return "Session date must be a weekday."
    return None


def session_incomplete_error(session: date) -> str | None:
    """If *session* is today in ET and before 4:00 PM, data is incomplete."""
    now = datetime.now(ET)
    if session == now.date() and now.time() < time(16, 0):
        return (
            "Today's regular session is not finished yet (before 4:00 PM ET). "
            "Pick an earlier **session_date** or run again after the close."
        )
    return None


def _universe_tickers(session: date, cap: int) -> list[str]:
    rows = fetch_grouped_daily(session.isoformat(), caller="moc_grouped")
    if not rows:
        return []
    scored: list[tuple[float, str]] = []
    for r in rows:
        sym = (r.get("T") or "").strip().upper()
        if not sym:
            continue
        c = r.get("c")
        v = r.get("v")
        if c is None or v is None:
            continue
        price = float(c)
        vol = float(v)
        if price < 1.0:
            continue
        scored.append((vol, sym))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:cap]]


def _one_ticker_minutes(
    ticker: str,
    session: date,
    from_ms: int,
    to_ms: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ticker": ticker}
    try:
        bars = fetch_minute_bars(ticker, from_ms, to_ms, caller=f"moc_min_{ticker}")
    except Exception as e:
        logger.warning("moc_movers %s: %s", ticker, e)
        out["error"] = str(e)
        return out
    if not bars:
        out["error"] = "no minute bars"
        return out

    c_350 = _pick_bar_close_by_start_hm(bars, 15, 50)
    c_359 = _pick_bar_close_by_start_hm(bars, 15, 59)
    if c_359 is None:
        _, c_alt, _ = _last_rth_minute_ohlc(bars)
        c_359 = c_alt
    if c_350 is not None and c_350 > 0 and c_359 is not None:
        out["pct_350_400"] = (c_359 - c_350) / c_350 * 100.0
        out["px_350"] = c_350
        out["px_400"] = c_359
    else:
        out["pct_350_400"] = None

    o_last, c_last, t_last = _last_rth_minute_ohlc(bars)
    if o_last is not None and c_last is not None and abs(o_last) > 1e-12:
        out["pct_1m_proxy"] = (c_last - o_last) / o_last * 100.0
        out["last_min_o"] = o_last
        out["last_min_c"] = c_last
    else:
        out["pct_1m_proxy"] = None
    return out


def _trade_refined_pct(ticker: str, session: date) -> dict[str, Any]:
    """Last trade at or before 3:59:45 vs last at or before ~4:00:00 ET (participant_timestamp ns)."""
    gte_ns = _et_to_ns(session, *_TRADE_PULL_LO)
    # Include prints through the end of the 4:00:00 second (RTH close).
    lte_dt = datetime(session.year, session.month, session.day, 16, 0, 0, 999999, tzinfo=ET)
    lte_ns = int(lte_dt.timestamp() * 1_000_000_000)
    cut_ns = _et_to_ns(session, *_CUT_HMS)

    try:
        trades = fetch_trades(ticker, gte_ns, lte_ns, caller=f"moc_tr_{ticker}")
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "pct_refined": None}

    if not trades:
        return {"ticker": ticker, "pct_refined": None, "error": "no trades"}

    ref_px: float | None = None
    end_px: float | None = None
    for tr in trades:
        ts = tr.get("participant_timestamp") or tr.get("sip_timestamp")
        if ts is None:
            continue
        px = tr.get("price")
        if px is None:
            continue
        ts = int(ts)
        if ts <= cut_ns:
            ref_px = float(px)
        if ts <= lte_ns:
            end_px = float(px)

    if ref_px is None or end_px is None or abs(ref_px) < 1e-12:
        return {"ticker": ticker, "pct_refined": None, "error": "insufficient trades in window"}

    return {
        "ticker": ticker,
        "pct_refined": (end_px - ref_px) / ref_px * 100.0,
        "px_ref": ref_px,
        "px_end": end_px,
    }


def build_moc_movers_report(
    session: date,
    *,
    top_n: int = 10,
    refine_top: int = _REFINE_COUNT,
) -> dict[str, Any]:
    """Return ranked movers + errors. Requires ``get_massive_api_key()``."""
    err = validate_session_date(session)
    if err:
        return {"error": err, "session": session.isoformat()}
    inc = session_incomplete_error(session)
    if inc:
        return {"error": inc, "session": session.isoformat()}

    if not get_massive_api_key():
        return {"error": "MASSIVE_API_KEY or POLYGON_API_KEY not set.", "session": session.isoformat()}

    cap = moc_max_tickers()
    tickers = _universe_tickers(session, cap)
    if not tickers:
        return {"error": "No universe from grouped daily (empty or API failure).", "session": session.isoformat()}

    from_ms, to_ms = _et_range_ms(session, _MOC_FETCH_LO, _MOC_FETCH_HI)
    workers = moc_workers()

    minute_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(_one_ticker_minutes, t, session, from_ms, to_ms): t for t in tickers
        }
        for fut in as_completed(futs):
            minute_rows.append(fut.result())

    def _abs350(r: dict[str, Any]) -> float:
        p = r.get("pct_350_400")
        if p is None:
            return -1.0
        return abs(float(p))

    def _abs1m(r: dict[str, Any]) -> float:
        p = r.get("pct_1m_proxy")
        if p is None:
            return -1.0
        return abs(float(p))

    with350 = [r for r in minute_rows if r.get("pct_350_400") is not None]
    with350.sort(key=lambda r: (-_abs350(r), r.get("ticker", "")))

    with1m = [r for r in minute_rows if r.get("pct_1m_proxy") is not None]
    with1m.sort(key=lambda r: (-_abs1m(r), r.get("ticker", "")))

    top350 = with350[: max(1, top_n)]
    refine_candidates = with1m[: max(1, refine_top)]

    refined: list[dict[str, Any]] = []
    for r in refine_candidates:
        tk = r.get("ticker") or ""
        if not tk:
            continue
        tr = _trade_refined_pct(tk, session)
        proxy = r.get("pct_1m_proxy")
        tr["pct_1m_proxy"] = proxy
        tr["last_min_o"] = r.get("last_min_o")
        tr["last_min_c"] = r.get("last_min_c")
        if tr.get("pct_refined") is not None:
            refined.append(tr)

    refined.sort(
        key=lambda x: (-abs(float(x["pct_refined"])) if x.get("pct_refined") is not None else -1.0, x.get("ticker", ""))
    )
    top_refined = refined[: max(1, top_n)]

    return {
        "session": session.isoformat(),
        "universe_size": len(tickers),
        "minute_ok": len(with350),
        "top_350_400": top350,
        "top_1m_proxy": with1m[: max(1, top_n)],
        "top_refined": top_refined,
        "refine_candidates": len(refine_candidates),
    }
