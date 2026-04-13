"""Massive OHLC aggregates + mplfinance charts with levels and overlays for `/top_opps` execution study."""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import mplfinance as mpf
import numpy as np
import pandas as pd

from finviz_chart import CHART_TIMEFRAME_LABELS, CHART_TIMEFRAME_FILE_TAG
from massive_rest import fetch_aggregate_bars, fetch_daily_bars, get_massive_api_key

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_TOP_OPPS_TIMEFRAMES = ("i1", "i5", "h", "d")

# Dark chart style (no white background)
_DARK_MPF = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    gridcolor="#3d3d3d",
    gridstyle="--",
    facecolor="#1e1e1e",
    edgecolor="#444444",
    figcolor="#1e1e1e",
    rc={
        "axes.edgecolor": "#555555",
        "axes.labelcolor": "#e0e0e0",
        "xtick.color": "#b0b0b0",
        "ytick.color": "#b0b0b0",
        "text.color": "#e8e8e8",
        "font.size": 9,
    },
)

_LINE_COLORS = {
    "EMA9": "#26c6da",
    "EMA21": "#ffb74d",
    "SMA50": "#9ccc65",
    "SMA100": "#ce93d8",
    "SMA200": "#ef5350",
    "VWAP": "#eceff1",
}

_ENTRY_COLOR = "#66bb6a"
_STOP_COLOR = "#ef5350"
_TARGET_COLOR = "#42a5f5"


def _bar_open_time_utc_to_et(t_raw: Any) -> pd.Timestamp:
    """Polygon/Massive `t` is bar open time in UTC (ms or ns); normalize to America/New_York."""
    v = int(t_raw)
    if v >= 10**15:
        ts = pd.Timestamp(v, unit="ns", tz=timezone.utc)
    elif v >= 10**12:
        ts = pd.Timestamp(v, unit="ms", tz=timezone.utc)
    else:
        ts = pd.Timestamp(v, unit="s", tz=timezone.utc)
    return ts.tz_convert(ET)


def bars_to_ohlcv_df(bars: list[dict[str, Any]]) -> pd.DataFrame | None:
    """Massive aggregate results → mplfinance DataFrame (DatetimeIndex America/New_York)."""
    if not bars:
        return None
    rows: list[dict[str, Any]] = []
    for b in bars:
        t = b.get("t")
        if t is None:
            continue
        ts = _bar_open_time_utc_to_et(t)
        rows.append(
            {
                "Date": ts,
                "Open": float(b["o"]),
                "High": float(b["h"]),
                "Low": float(b["l"]),
                "Close": float(b["c"]),
                "Volume": float(b.get("v") or 0),
            }
        )
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.set_index("Date").sort_index()
    return df


def _add_ma_overlays(df: pd.DataFrame) -> pd.DataFrame:
    """EMA9/21, SMA 50/100/200 on *df* (extended history)."""
    out = df.copy()
    c = out["Close"]
    out["EMA9"] = c.ewm(span=9, adjust=False).mean()
    out["EMA21"] = c.ewm(span=21, adjust=False).mean()
    out["SMA50"] = c.rolling(50, min_periods=1).mean()
    out["SMA100"] = c.rolling(100, min_periods=1).mean()
    out["SMA200"] = c.rolling(200, min_periods=1).mean()
    return out


def _session_vwap(df_today: pd.DataFrame) -> pd.Series:
    """Anchored VWAP for *today* rows only."""
    tp = (df_today["High"] + df_today["Low"] + df_today["Close"]) / 3.0
    vol = df_today["Volume"].astype(float)
    pv = (tp * vol).cumsum()
    vc = vol.cumsum().replace(0, np.nan)
    return pv / vc


def _trading_session_slice_et(df: pd.DataFrame) -> pd.DataFrame:
    """Today's session (ET). If there are no bars yet for calendar today, use the latest date present (last session in range)."""
    today_d = datetime.now(ET).date()
    mask_today = df.index.map(lambda t: t.date() == today_d)
    out = df.loc[mask_today].copy()
    if len(out) > 0:
        return out
    dates = sorted({t.date() for t in df.index})
    if not dates:
        return df.iloc[0:0].copy()
    last_d = dates[-1]
    mask = df.index.map(lambda t: t.date() == last_d)
    return df.loc[mask].copy()


def _pad_single_bar_for_candles(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """mplfinance needs ≥2 rows for candlesticks; duplicate last bar with a small time offset."""
    if df is None or len(df) >= 2:
        return df
    if len(df) == 0:
        return df
    deltas = {
        "i1": timedelta(minutes=1),
        "i5": timedelta(minutes=5),
        "h": timedelta(hours=1),
        "d": timedelta(days=1),
    }
    delta = deltas.get(tf, timedelta(minutes=1))
    last = df.iloc[-1:].copy()
    new_idx = df.index[-1] + delta
    last.index = [new_idx]
    return pd.concat([df, last])


def _fmt_vol_compact(n: float) -> str:
    n = float(n)
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{int(n)}"


def compute_execution_metrics(entry: float, stop: float, target: float) -> dict[str, Any] | None:
    """Risk/reward and EV/share at 50% win rate. Picks long vs short from level ordering."""
    if stop < entry < target:
        risk = entry - stop
        reward = target - entry
        side = "long"
    elif target < entry < stop:
        risk = stop - entry
        reward = entry - target
        side = "short"
    else:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0:
            return None
        side = "mixed"
    if risk <= 0:
        return None
    rr = reward / risk
    ev50 = 0.5 * reward - 0.5 * risk
    return {"risk": risk, "reward": reward, "rr": rr, "ev50": ev50, "side": side}


def format_study_embed_lines(
    entry: float,
    stop: float,
    target: float,
    metrics: dict[str, Any] | None,
    *,
    exit_is_rth_default: bool = False,
) -> str:
    exit_note = " *(RTH ~4pm ET — default)*" if exit_is_rth_default else ""
    lines = [
        f"Entry **{entry:.2f}** · Stop **{stop:.2f}** · Exit **{target:.2f}**{exit_note}",
    ]
    if metrics:
        lines.append(
            f"R:R **{metrics['rr']:.2f}** · EV/Share=**${metrics['ev50']:.2f}** (at 50% probability)"
        )
        side_raw = str(metrics.get("side", "mixed")).lower()
        side_disp = {"long": "Long", "short": "Short", "mixed": "Mixed"}.get(side_raw, side_raw.title())
        lines.append(f"Side: **{side_disp}**")
    else:
        lines.append("R:R / EV — *ambiguous level order (set stop on loss side, target on profit side)*")
    return "\n".join(lines)


def _timestamp_to_et(ts: pd.Timestamp | datetime | None) -> datetime:
    """Normalize to timezone-aware America/New_York."""
    if ts is None:
        return datetime.now(ET)
    if isinstance(ts, pd.Timestamp):
        t = ts
        if t.tzinfo is None:
            return t.tz_localize(ET).to_pydatetime()
        return t.tz_convert(ET).to_pydatetime()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def _format_title_as_of_et(tf_key: str, last_bar_ts: pd.Timestamp | datetime | None) -> str:
    """Human-readable last-bar time in US market (Eastern) time for the chart header."""
    dt = _timestamp_to_et(last_bar_ts).astimezone(ET)
    if tf_key == "d":
        return f"As of {dt.strftime('%b %d, %Y')} ET"
    h12 = dt.hour % 12 or 12
    ap = "PM" if dt.hour >= 12 else "AM"
    tpart = f"{h12}:{dt.minute:02d} {ap}"
    return f"As of {dt.strftime('%b %d, %Y')} {tpart} ET"


def _chart_title(
    ticker: str,
    tf_key: str,
    last_px: float,
    pct_today: float | None,
    day_volume: float,
    last_bar_ts: pd.Timestamp | datetime | None,
) -> str:
    tf_label = CHART_TIMEFRAME_LABELS.get(tf_key, tf_key)
    pct_s = f"{pct_today:+.2f}%" if pct_today is not None else "—"
    vol_s = _fmt_vol_compact(day_volume)
    as_of = _format_title_as_of_et(tf_key, last_bar_ts)
    line1 = f"{ticker}  [{tf_label}]  {pct_s}  ${last_px:,.2f}  Vol {vol_s}"
    return f"{line1}\n{as_of}"


def _datetimeindex_naive_et_for_mpl(df: pd.DataFrame) -> pd.DataFrame:
    """mplfinance/matplotlib mishandle tz-aware axes; use naive datetimes = Eastern wall clock."""
    out = df.copy()
    idx = out.index
    if not isinstance(idx, pd.DatetimeIndex):
        return out
    if idx.tz is not None:
        out.index = idx.tz_convert(ET).tz_localize(None)
    return out


def _format_tick_label_from_row(tf: str, ts: pd.Timestamp | Any) -> str:
    """Label string from a bar timestamp (naive ET wall clock)."""
    if hasattr(ts, "to_pydatetime"):
        dt = ts.to_pydatetime()
    else:
        dt = ts
    if tf == "d":
        return dt.strftime("%b %Y")
    h12 = dt.hour % 12 or 12
    ap = "PM" if dt.hour >= 12 else "AM"
    if tf == "i1":
        return f"{h12}:{dt.minute:02d} {ap}"
    if tf in ("i5", "h"):
        return f"{dt.month}/{dt.day} {h12}:{dt.minute:02d} {ap}"
    return dt.strftime("%Y-%m-%d")


def _apply_xaxis_market_time(ax: Any, plot_df: pd.DataFrame, tf: str) -> None:
    """Label x-axis from DataFrame index (ET). mplfinance may use bar indices (0..n-1) or matplotlib date nums."""
    idx = plot_df.index
    if not isinstance(idx, pd.DatetimeIndex) or len(idx) < 1:
        return
    n = len(idx)
    nums = mdates.date2num(idx.to_pydatetime())

    nticks = min(8, n)
    pos_idx = np.unique(np.linspace(0, n - 1, nticks, dtype=int))

    raw = ax.get_xticks()
    try:
        tick_max = float(np.nanmax(raw)) if len(raw) else 0.0
    except Exception:
        tick_max = 0.0
    # Bar-index axis (small ints) vs mdates day numbers (~7e5); empty → assume date axis
    use_bar_index = len(raw) > 0 and tick_max < 1e5

    if use_bar_index:
        ax.set_xticks(pos_idx.astype(float))
    else:
        ax.set_xticks(nums[pos_idx])

    ax.set_xticklabels([_format_tick_label_from_row(tf, idx[i]) for i in pos_idx], rotation=20, ha="right")


_MA_LEGEND_LABEL = {
    "EMA9": "EMA 9",
    "EMA21": "EMA 21",
    "SMA50": "SMA 50",
    "SMA100": "SMA 100",
    "SMA200": "SMA 200",
}


def _add_study_legend(ax: Any, *, include_vwap: bool) -> None:
    """Top-left legend for MAs, optional VWAP, and level lines."""
    handles: list[Line2D] = []
    labels: list[str] = []
    for name in ("EMA9", "EMA21", "SMA50", "SMA100", "SMA200"):
        handles.append(Line2D([0], [0], color=_LINE_COLORS[name], linewidth=1.8))
        labels.append(_MA_LEGEND_LABEL[name])
    if include_vwap:
        handles.append(
            Line2D([0], [0], color=_LINE_COLORS["VWAP"], linewidth=1.8, linestyle="--")
        )
        labels.append("VWAP")
    handles.append(Line2D([0], [0], color=_ENTRY_COLOR, linewidth=1.5, linestyle="--"))
    labels.append("Entry")
    handles.append(Line2D([0], [0], color=_STOP_COLOR, linewidth=1.5, linestyle="--"))
    labels.append("Stop")
    handles.append(Line2D([0], [0], color=_TARGET_COLOR, linewidth=1.5, linestyle="--"))
    labels.append("Target")
    leg = ax.legend(
        handles,
        labels,
        loc="upper left",
        fontsize=7,
        framealpha=0.92,
        facecolor="#2b2b2b",
        edgecolor="#555555",
        labelcolor="#e8e8e8",
    )
    if leg is not None:
        for text in leg.get_texts():
            text.set_color("#e8e8e8")


def _build_addplots(
    df_plot: pd.DataFrame,
    *,
    include_vwap: bool,
) -> list[Any]:
    aps: list[Any] = []
    for name in ("EMA9", "EMA21", "SMA50", "SMA100", "SMA200"):
        if name in df_plot.columns and df_plot[name].notna().any():
            aps.append(
                mpf.make_addplot(
                    df_plot[name],
                    color=_LINE_COLORS[name],
                    width=0.9,
                )
            )
    if include_vwap and "VWAP" in df_plot.columns and df_plot["VWAP"].notna().any():
        aps.append(
            mpf.make_addplot(
                df_plot["VWAP"],
                color=_LINE_COLORS["VWAP"],
                width=1.0,
                linestyle="--",
            )
        )
    return aps


def render_execution_chart_png(
    df_plot: pd.DataFrame,
    *,
    entry: float,
    stop: float,
    target: float,
    title: str,
    tf: str,
    include_vwap: bool,
) -> bytes | None:
    """Candlestick PNG: dark theme, MA overlays, optional VWAP, entry/stop/target lines."""
    if df_plot is None or len(df_plot) < 1:
        return None
    try:
        buf = io.BytesIO()
        plot_df = _pad_single_bar_for_candles(df_plot.copy(), tf)
        plot_df = _datetimeindex_naive_et_for_mpl(plot_df)
        ap = _build_addplots(plot_df, include_vwap=include_vwap)
        plot_kw: dict[str, Any] = dict(
            type="candle",
            style=_DARK_MPF,
            volume=False,
            title="",
            returnfig=True,
            figsize=(10.5, 5.8),
            tight_layout=False,
            warn_too_much_data=50000,
        )
        if ap:
            plot_kw["addplot"] = ap
        fig, axes = mpf.plot(plot_df, **plot_kw)
        ax = axes[0]
        fig.subplots_adjust(left=0.07, right=0.98, top=0.78, bottom=0.14)
        fig.suptitle(
            title,
            color="#ececec",
            fontsize=9,
            y=0.97,
            fontweight="normal",
        )
        ax.axhline(entry, color=_ENTRY_COLOR, linewidth=1.1, linestyle="--", alpha=0.95)
        ax.axhline(stop, color=_STOP_COLOR, linewidth=1.1, linestyle="--", alpha=0.95)
        ax.axhline(target, color=_TARGET_COLOR, linewidth=1.1, linestyle="--", alpha=0.95)
        _add_study_legend(ax, include_vwap=include_vwap)
        _apply_xaxis_market_time(ax, plot_df, tf)
        fig.savefig(
            buf,
            format="png",
            dpi=120,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            pad_inches=0.35,
        )
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        logger.warning("top_opps chart render failed (%s): %s", tf, e)
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def _fetch_extended_intraday(
    ticker: str,
    mult: int,
    *,
    caller: str,
) -> list[dict[str, Any]]:
    now_et = datetime.now(ET)
    start_et = now_et - timedelta(days=14)
    start_ms = int(start_et.timestamp() * 1000)
    end_ms = int(now_et.timestamp() * 1000)
    return fetch_aggregate_bars(ticker, mult, "minute", start_ms, end_ms, caller=caller)


def default_exit_rth_close_et(ticker: str, *, caller: str = "top_opps_eod_exit") -> float | None:
    """Regular-session last print (~4:00pm ET): use today's close after 4pm ET when minute data exists, else prior session."""
    if not get_massive_api_key():
        return None
    raw = _fetch_extended_intraday(ticker, 1, caller=f"{caller}_1m")
    df = bars_to_ohlcv_df(raw)
    if df is None or len(df) < 1:
        return None

    by_day: dict[date, float] = {}
    for ts, row in df.iterrows():
        if ts.weekday() >= 5:
            continue
        tt = ts.time()
        if time(9, 30) <= tt <= time(16, 0):
            by_day[ts.date()] = float(row["Close"])

    if not by_day:
        return None

    sorted_days = sorted(by_day.keys())
    now = datetime.now(ET)
    today_d = now.date()
    tnow = now.time()

    if today_d in by_day and tnow >= time(16, 0):
        return by_day[today_d]

    prior = [d for d in sorted_days if d < today_d]
    if prior:
        return by_day[prior[-1]]

    return by_day[sorted_days[-1]]


def _fetch_extended_hourly(ticker: str, *, caller: str) -> list[dict[str, Any]]:
    now_et = datetime.now(ET)
    start_et = now_et - timedelta(days=60)
    start_ms = int(start_et.timestamp() * 1000)
    end_ms = int(now_et.timestamp() * 1000)
    return fetch_aggregate_bars(ticker, 1, "hour", start_ms, end_ms, caller=caller)


def _prepare_intraday_today(
    raw: list[dict[str, Any]],
) -> tuple[pd.DataFrame | None, float | None, float]:
    """Return (session OHLCV+indicators+VWAP, last_px, session_volume_sum).

    Session is calendar today (ET) when bars exist; otherwise the most recent day in the fetch window
    (e.g. last regular session). Single-bar sessions are allowed; rendering pads to two rows for candles.
    """
    df = bars_to_ohlcv_df(raw)
    if df is None or len(df) < 1:
        return None, None, 0.0
    df = _add_ma_overlays(df)
    session = _trading_session_slice_et(df)
    if session is None or len(session) < 1:
        return None, None, 0.0
    session = session.copy()
    session["VWAP"] = _session_vwap(session)
    last_px = float(session["Close"].iloc[-1])
    day_vol = float(session["Volume"].sum())
    return session, last_px, day_vol


def _prepare_daily_display(raw: list[dict[str, Any]]) -> tuple[pd.DataFrame | None, float | None, float]:
    """Last ~120 daily bars with overlays; last bar close and volume from display window."""
    df = bars_to_ohlcv_df(raw)
    if df is None or len(df) < 2:
        return None, None, 0.0
    df = _add_ma_overlays(df)
    if len(df) > 120:
        df = df.iloc[-120:].copy()
    last_px = float(df["Close"].iloc[-1])
    day_vol = float(df["Volume"].iloc[-1])
    return df, last_px, day_vol


def build_study_charts(
    ticker: str,
    entry: float,
    stop: float,
    target: float,
    *,
    quote_last_close: float | None = None,
    quote_pct_today: float | None = None,
    caller: str = "top_opps_study",
) -> tuple[list[tuple[str, bytes]], list[str], dict[str, Any] | None]:
    """Return ([(tf, png)], missing labels, execution metrics)."""
    if not get_massive_api_key():
        return [], ["(no Polygon/Massive API key)"], None

    metrics = compute_execution_metrics(entry, stop, target)

    out: list[tuple[str, bytes]] = []
    missing: list[str] = []

    # Unified title: last price + today's session volume from 1m data when possible
    anchor_px = float(quote_last_close) if quote_last_close is not None else 0.0
    anchor_vol = 0.0
    raw_1m_shared = _fetch_extended_intraday(ticker, 1, caller=f"{caller}_1m")
    td_1m, px_a, vol_a = _prepare_intraday_today(raw_1m_shared)
    if td_1m is not None and px_a is not None:
        anchor_px = float(px_a)
        anchor_vol = float(vol_a)

    for tf in _TOP_OPPS_TIMEFRAMES:
        label = CHART_TIMEFRAME_LABELS.get(tf, tf)
        png: bytes | None = None

        if tf == "i1":
            today_df = td_1m
            if today_df is not None:
                ttl = _chart_title(
                    ticker,
                    tf,
                    anchor_px,
                    quote_pct_today,
                    anchor_vol,
                    today_df.index[-1],
                )
                png = render_execution_chart_png(
                    today_df,
                    entry=entry,
                    stop=stop,
                    target=target,
                    title=ttl,
                    tf=tf,
                    include_vwap=True,
                )
        elif tf == "i5":
            raw = _fetch_extended_intraday(ticker, 5, caller=f"{caller}_i5")
            today_df, _, _ = _prepare_intraday_today(raw)
            if today_df is not None:
                ttl = _chart_title(
                    ticker,
                    tf,
                    anchor_px,
                    quote_pct_today,
                    anchor_vol,
                    today_df.index[-1],
                )
                png = render_execution_chart_png(
                    today_df,
                    entry=entry,
                    stop=stop,
                    target=target,
                    title=ttl,
                    tf=tf,
                    include_vwap=True,
                )
        elif tf == "h":
            raw = _fetch_extended_hourly(ticker, caller=f"{caller}_h")
            today_df, _, _ = _prepare_intraday_today(raw)
            if today_df is not None:
                ttl = _chart_title(
                    ticker,
                    tf,
                    anchor_px,
                    quote_pct_today,
                    anchor_vol,
                    today_df.index[-1],
                )
                png = render_execution_chart_png(
                    today_df,
                    entry=entry,
                    stop=stop,
                    target=target,
                    title=ttl,
                    tf=tf,
                    include_vwap=True,
                )
        elif tf == "d":
            now_et = datetime.now(ET)
            end_d = now_et.date()
            start_d = end_d - timedelta(days=420)
            raw = fetch_daily_bars(
                ticker,
                start_d.isoformat(),
                end_d.isoformat(),
                caller=f"{caller}_d",
            )
            ddf, last_px, day_vol = _prepare_daily_display(raw)
            if ddf is not None:
                if td_1m is not None:
                    title_px, title_vol = anchor_px, anchor_vol
                else:
                    title_vol = day_vol
                    if quote_last_close is not None:
                        title_px = float(quote_last_close)
                    else:
                        title_px = float(last_px) if last_px is not None else anchor_px
                ttl = _chart_title(
                    ticker,
                    tf,
                    title_px,
                    quote_pct_today,
                    title_vol,
                    ddf.index[-1],
                )
                png = render_execution_chart_png(
                    ddf,
                    entry=entry,
                    stop=stop,
                    target=target,
                    title=ttl,
                    tf=tf,
                    include_vwap=False,
                )

        if png:
            out.append((tf, png))
        else:
            missing.append(label)

    return out, missing, metrics
