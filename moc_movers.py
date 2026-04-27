"""Market-on-close style movers from Massive: notional (last 10m), time-local RVOL, + trades."""

from __future__ import annotations

import logging
import math
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

# Last 10 RTH minutes before the closing print (3:50–3:59 bar starts ET)
_10M_LO = (15, 50)
_10M_HI = (16, 0)

# Reference instant for "before the last 15 seconds" (ET on session day)
_CUT_HMS = (15, 59, 45)
_TRADE_PULL_LO = (15, 57, 0)

_DEFAULT_MAX_TICKERS = 500
_DEFAULT_WORKERS = 10
_REFINE_COUNT = 20
_DEFAULT_RVOL_LOOKBACK = 7
_DEFAULT_RVOL_CANDIDATES = 150


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def moc_max_tickers() -> int:
    return max(50, min(5000, _env_int("MOC_MAX_TICKERS", _DEFAULT_MAX_TICKERS)))


def moc_workers() -> int:
    return max(4, min(32, _env_int("MOC_WORKERS", _DEFAULT_WORKERS)))


def moc_rvol_lookback() -> int:
    return max(2, min(30, _env_int("MOC_RVOL_LOOKBACK", _DEFAULT_RVOL_LOOKBACK)))


def moc_rvol_candidates() -> int:
    return max(20, min(500, _env_int("MOC_RVOL_CANDIDATES", _DEFAULT_RVOL_CANDIDATES)))


def moc_min_notional_10m() -> float:
    return max(0.0, _env_float("MOC_MIN_NOTIONAL_10M", 0.0))


def moc_min_rvol_10m() -> float:
    return max(0.0, _env_float("MOC_MIN_RVOL_10M", 0.0))


def moc_min_vol_10m() -> float:
    return max(0.0, _env_float("MOC_MIN_VOL_10M", 0.0))


def moc_min_price() -> float:
    return max(0.0, _env_float("MOC_MIN_PRICE", 1.0))


def moc_universe_sort() -> str:
    raw = (os.environ.get("MOC_UNIVERSE_SORT", "") or "dollar").strip().lower()
    return "shares" if raw == "shares" else "dollar"


def _ms_to_et_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=ET).date()


def _et_to_ns(d: date, h: int, m: int, s: int = 0) -> int:
    dt = datetime(d.year, d.month, d.day, h, m, s, tzinfo=ET)
    return int(dt.timestamp() * 1_000_000_000)


def _et_range_ms(
    d: date, lo: tuple[int, int], hi: tuple[int, int]
) -> tuple[int, int]:
    a = datetime(d.year, d.month, d.day, lo[0], lo[1], 0, tzinfo=ET)
    b = datetime(d.year, d.month, d.day, hi[0], hi[1], 0, tzinfo=ET)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def _bar_start_et_parts(t_ms: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(t_ms / 1000, tz=ET)
    return dt.hour, dt.minute


def _sum_last_10m_rth(
    bars: list[dict[str, Any]], session: date
) -> tuple[float, float]:
    """Sum share volume and notional (v*close) for minute bars 3:50–3:59 PM ET on *session*."""
    vol = 0.0
    notional = 0.0
    for b in bars:
        t = b.get("t")
        if t is None:
            continue
        t_ms = int(t)
        dt = datetime.fromtimestamp(t_ms / 1000, tz=ET)
        if dt.date() != session:
            continue
        h, m = dt.hour, dt.minute
        if h == 15 and 50 <= m <= 59:
            vv = b.get("v")
            cc = b.get("c")
            if vv is not None and cc is not None:
                vf, cf = float(vv), float(cc)
                vol += vf
                notional += vf * cf
    return vol, notional


def _pick_bar_close_by_start_hm(
    bars: list[dict[str, Any]], want_h: int, want_m: int
) -> float | None:
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


def _last_rth_minute_ohlc(
    bars: list[dict[str, Any]]
) -> tuple[float | None, float | None, int | None]:
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


def prior_rth_session_dates(session: date, k: int) -> list[date]:
    """
    *k* most recent RTH **session** dates before *session* (from SPY daily bar calendar).
    """
    if k <= 0:
        return []
    start = session - timedelta(days=120)
    bars = fetch_daily_bars("SPY", start.isoformat(), session.isoformat(), caller="moc_spy")
    if not bars:
        return []
    dates = sorted(
        {
            _ms_to_et_date(int(b["t"]))
            for b in bars
            if b.get("t") is not None
        }
    )
    before = [d for d in dates if d < session]
    return before[-k:] if len(before) >= k else before


def validate_session_date(d: date) -> str | None:
    if d.weekday() >= 5:
        return "Session date must be a weekday."
    return None


def session_incomplete_error(session: date) -> str | None:
    now = datetime.now(ET)
    if session == now.date() and now.time() < time(16, 0):
        return (
            "Today's regular session is not finished yet (before 4:00 PM ET). "
            "Pick an earlier **session_date** or run again after the close."
        )
    return None


def _universe_rows(session: date, cap: int) -> list[dict[str, Any]]:
    """
    One grouped-daily call; sort by *dollar* (c×v) or share volume, cap, min price.
    """
    rows_in = fetch_grouped_daily(session.isoformat(), caller="moc_grouped")
    if not rows_in:
        return []
    sort_dollar = moc_universe_sort() == "dollar"
    min_p = moc_min_price()
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows_in:
        sym = (r.get("T") or "").strip().upper()
        if not sym:
            continue
        c = r.get("c")
        v = r.get("v")
        if c is None or v is None:
            continue
        price = float(c)
        vol = float(v)
        if price < min_p:
            continue
        dollar_day = price * vol
        score = dollar_day if sort_dollar else vol
        scored.append(
            (
                score,
                {
                    "ticker": sym,
                    "dollar_day": dollar_day,
                    "close_s": price,
                    "day_vol_s": vol,
                },
            )
        )
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:cap]]


def _vol_10m_on_day(ticker: str, d: date) -> float:
    """Session share volume in 3:50–3:59 PM ET on *d*."""
    from_ms, to_ms = _et_range_ms(d, _10M_LO, _10M_HI)
    if from_ms >= to_ms:
        return 0.0
    try:
        bars = fetch_minute_bars(ticker, from_ms, to_ms, caller=f"moc_10h_{ticker}")
    except Exception as e:
        logger.warning("moc_10h %s %s: %s", ticker, d, e)
        return 0.0
    vol, _n = _sum_last_10m_rth(bars, d)
    return vol


def _compute_rvol_10m(
    ticker: str, session: date, vol_10m_today: float, prior_dates: list[date]
) -> tuple[float | None, float, int, int]:
    """
    rvol_10m = vol_today / mean(prior 10m vols). At least 2 non-zero prior windows required.
    Returns (rvol, prior_mean, n_used, n_looked).
    """
    if not prior_dates or vol_10m_today <= 0:
        return None, 0.0, 0, 0
    acc: list[float] = []
    for d in prior_dates:
        v = _vol_10m_on_day(ticker, d)
        if v > 0:
            acc.append(v)
    n_used = len(acc)
    if n_used < 2:
        return None, (sum(acc) / n_used) if n_used else 0.0, n_used, len(prior_dates)
    m = sum(acc) / n_used
    if m <= 0:
        return None, 0.0, n_used, len(prior_dates)
    return vol_10m_today / m, m, n_used, len(prior_dates)


def _moc_composite(
    move_pct: float | None, notional: float, rvol: float | None
) -> float:
    if move_pct is None:
        return -1.0
    n = max(float(notional), 0.0)
    w = max(float(rvol), 0.25) if rvol is not None else 1.0
    return abs(float(move_pct)) * (1.0 + math.log1p(n)) * w


def _one_ticker_minutes(
    meta: dict[str, Any],
    session: date,
    from_ms: int,
    to_ms: int,
) -> dict[str, Any]:
    ticker = (meta.get("ticker") or "").strip() or "?"
    out: dict[str, Any] = {
        "ticker": ticker,
        "dollar_day": float(meta.get("dollar_day") or 0.0),
    }
    try:
        bars = fetch_minute_bars(ticker, from_ms, to_ms, caller=f"moc_min_{ticker}")
    except Exception as e:
        logger.warning("moc_movers %s: %s", ticker, e)
        out["error"] = str(e)
        return out
    if not bars:
        out["error"] = "no minute bars"
        return out

    vol_10, notional_10 = _sum_last_10m_rth(bars, session)
    out["vol_10m"] = vol_10
    out["notional_10m"] = notional_10

    c_350 = _pick_bar_close_by_start_hm(bars, 15, 50)
    c_359 = _pick_bar_close_by_start_hm(bars, 15, 59)
    if c_359 is None:
        _, c_alt, _t = _last_rth_minute_ohlc(bars)
        c_359 = c_alt
    if c_350 is not None and c_350 > 0 and c_359 is not None:
        out["pct_350_400"] = (c_359 - c_350) / c_350 * 100.0
        out["px_350"] = c_350
        out["px_400"] = c_359
    else:
        out["pct_350_400"] = None

    o_last, c_last, _t_last = _last_rth_minute_ohlc(bars)
    if o_last is not None and c_last is not None and abs(o_last) > 1e-12:
        out["pct_1m_proxy"] = (c_last - o_last) / o_last * 100.0
        out["last_min_o"] = o_last
        out["last_min_c"] = c_last
    else:
        out["pct_1m_proxy"] = None
    return out


def _trade_refined_pct(ticker: str, session: date) -> dict[str, Any]:
    gte_ns = _et_to_ns(session, *_TRADE_PULL_LO)
    lte_dt = datetime(
        session.year, session.month, session.day, 16, 0, 0, 999999, tzinfo=ET
    )
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
        return {
            "ticker": ticker,
            "pct_refined": None,
            "error": "insufficient trades in window",
        }

    return {
        "ticker": ticker,
        "pct_refined": (end_px - ref_px) / ref_px * 100.0,
        "px_ref": ref_px,
        "px_end": end_px,
    }


def _attach_rvol_for_ticker(
    arg: tuple[str, date, float, list[date]],
) -> tuple[str, float | None, float, int]:
    ticker, session, v_today, prior_dates = arg
    r, mean, n_u, _n_l = _compute_rvol_10m(ticker, session, v_today, prior_dates)
    return ticker, r, mean, n_u


def build_moc_movers_report(
    session: date,
    *,
    top_n: int = 10,
    refine_top: int = _REFINE_COUNT,
) -> dict[str, Any]:
    err = validate_session_date(session)
    if err:
        return {"error": err, "session": session.isoformat()}
    inc = session_incomplete_error(session)
    if inc:
        return {"error": inc, "session": session.isoformat()}

    if not get_massive_api_key():
        return {
            "error": "MASSIVE_API_KEY or POLYGON_API_KEY not set.",
            "session": session.isoformat(),
        }

    cap = moc_max_tickers()
    uni = _universe_rows(session, cap)
    if not uni:
        return {
            "error": "No universe from grouped daily (empty or API failure).",
            "session": session.isoformat(),
        }
    tickers = [r["ticker"] for r in uni]
    meta_by_t = {r["ticker"]: r for r in uni}

    from_ms, to_ms = _et_range_ms(session, _MOC_FETCH_LO, _MOC_FETCH_HI)
    workers = moc_workers()

    minute_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(_one_ticker_minutes, meta_by_t[t], session, from_ms, to_ms): t
            for t in tickers
        }
        for fut in as_completed(futs):
            minute_rows.append(fut.result())

    min_n = moc_min_notional_10m()
    min_v10 = moc_min_vol_10m()
    min_rv = moc_min_rvol_10m()
    m_pool = moc_rvol_candidates()
    k_lb = moc_rvol_lookback()
    prior_dates = prior_rth_session_dates(session, k_lb)

    def _passes_gates(r: dict[str, Any]) -> bool:
        if r.get("error"):
            return False
        if min_n and float(r.get("notional_10m") or 0) < min_n:
            return False
        if min_v10 and float(r.get("vol_10m") or 0) < min_v10:
            return False
        return True

    gated = [r for r in minute_rows if _passes_gates(r)]
    gated.sort(key=lambda r: -float(r.get("notional_10m") or 0.0))
    rvol_pool = gated[:m_pool]
    tset = {r.get("ticker") for r in rvol_pool}

    rvol_map: dict[str, float | None] = {}
    prior_mean_map: dict[str, float] = {}
    if rvol_pool and prior_dates:
        args: list[tuple[str, date, float, list[date]]] = [
            (str(r.get("ticker") or ""), session, float(r.get("vol_10m") or 0.0), prior_dates)
            for r in rvol_pool
            if (r.get("vol_10m") or 0) > 0
        ]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs2 = {ex.submit(_attach_rvol_for_ticker, a): a[0] for a in args}
            for fut in as_completed(futs2):
                tk, rvol, pmean, _ = fut.result()
                rvol_map[tk] = rvol
                prior_mean_map[tk] = pmean
    for r in minute_rows:
        tk = str(r.get("ticker") or "")
        if tk in tset and tk in rvol_map:
            r["rvol_10m"] = rvol_map[tk]
            r["prior_10m_vol_mean"] = prior_mean_map.get(tk, 0.0)
        elif tk in tset:
            r["rvol_10m"] = None
            r["prior_10m_vol_mean"] = None
        else:
            r["rvol_10m"] = None
            r["prior_10m_vol_mean"] = None

    def _ok_for_leaderboard(r: dict[str, Any]) -> bool:
        if r.get("error") or r.get("ticker") not in tset:
            return False
        if not _passes_gates(r):
            return False
        rv = r.get("rvol_10m")
        if min_rv > 0 and (rv is None or float(rv) < min_rv):
            return False
        return True

    lb_only = [r for r in minute_rows if _ok_for_leaderboard(r)]

    def _abs350(r: dict[str, Any]) -> float:
        return _moc_composite(
            r.get("pct_350_400") if r.get("pct_350_400") is not None else None,
            float(r.get("notional_10m") or 0),
            r.get("rvol_10m"),
        )

    def _abs1m(r: dict[str, Any]) -> float:
        return _moc_composite(
            r.get("pct_1m_proxy") if r.get("pct_1m_proxy") is not None else None,
            float(r.get("notional_10m") or 0),
            r.get("rvol_10m"),
        )

    with350 = [r for r in lb_only if r.get("pct_350_400") is not None]
    with350.sort(key=lambda r: (-_abs350(r), r.get("ticker", "")))

    with1m = [r for r in lb_only if r.get("pct_1m_proxy") is not None]
    with1m.sort(key=lambda r: (-_abs1m(r), r.get("ticker", "")))

    top350 = with350[: max(1, top_n)]
    refine_candidates = with1m[: max(1, refine_top)]

    refined: list[dict[str, Any]] = []
    for r in refine_candidates:
        tk = r.get("ticker") or ""
        if not tk:
            continue
        tr = _trade_refined_pct(tk, session)
        tr["pct_1m_proxy"] = r.get("pct_1m_proxy")
        tr["last_min_o"] = r.get("last_min_o")
        tr["last_min_c"] = r.get("last_min_c")
        tr["notional_10m"] = r.get("notional_10m")
        tr["rvol_10m"] = r.get("rvol_10m")
        if tr.get("pct_refined") is not None:
            refined.append(tr)

    refined.sort(
        key=lambda x: (
            -abs(float(x["pct_refined"]))
            if x.get("pct_refined") is not None
            else -1.0,
            x.get("ticker", ""),
        )
    )
    top_refined = refined[: max(1, top_n)]

    with350ok = [r for r in minute_rows if r.get("pct_350_400") is not None and not r.get("error")]

    return {
        "session": session.isoformat(),
        "universe_size": len(tickers),
        "minute_ok": len(with350ok),
        "rvol_pool": len(tset),
        "rvol_lookback": k_lb,
        "prior_session_dates": [d.isoformat() for d in prior_dates],
        "top_350_400": top350,
        "top_1m_proxy": with1m[: max(1, top_n)],
        "top_refined": top_refined,
        "refine_candidates": len(refine_candidates),
        "gated_pre_rvol": len(gated),
    }
