"""In-play earnings screen: FinViz Elite screen + Massive %EAVOL + fundamentals."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fetch_elite import _get_api_key, _normalize_row, _parse_num, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import (
    _classify_earnings_session,
    _fmt_shares_compact,
    _fmt_volume_cell,
    _finviz_thousands_to_shares,
)
from finviz_inplay import _fmt_float_shares_display
from massive_rest import (
    fetch_daily_bars,
    fetch_minute_bars,
    get_massive_api_key,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

EARNINGS_F = "earningsdate_yesterdayafter|todaybefore,sh_avgvol_o1000,sh_price_o1"

INPLAY_EARNINGS_CANDIDATE_CAP = 150
INPLAY_EARNINGS_MAX_DISPLAY = 10
_MAX_WORKERS = 8

# EAVOL tier thresholds (for emoji)
_EAVOL_HOT = 50.0
_EAVOL_WATCH = 20.0


def earnings_screener_url() -> str:
    q = urlencode({"v": "151", "f": EARNINGS_F, "ft": "4"})
    return f"https://elite.finviz.com/screener.ashx?{q}"


def _earnings_export_url() -> str:
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


def _et_bar_start_date_and_mins(t_ms: int) -> tuple[date, int]:
    """Bar *start* in America/New_York: calendar day and minutes since local midnight (0..1439)."""
    dt = datetime.fromtimestamp(t_ms / 1000, tz=ET)
    return dt.date(), dt.hour * 60 + dt.minute


def _eavol_premarket_includes(t_ms: int, current_d: date) -> bool:
    """
    4:00–9:29 AM ET on *current_d* only. Excludes the 9:30 print (first regular-session minute);
    we never add RTH volume to EAVOL.
    """
    d, mins = _et_bar_start_date_and_mins(t_ms)
    if d != current_d:
        return False
    return 4 * 60 <= mins < 9 * 60 + 30


def _eavol_afterhours_includes(t_ms: int, prior_d: date) -> bool:
    """
    4:00–7:59 PM ET on *prior_d* only. After-hours for overnight earnings, no regular session.
    """
    d, mins = _et_bar_start_date_and_mins(t_ms)
    if d != prior_d:
        return False
    return 16 * 60 <= mins < 20 * 60


def _sum_eavol_premarket(
    ticker: str,
    current_d: date,
    pm_lo: int,
    pm_hi: int,
    *,
    caller: str,
) -> float:
    """
    Pre-market for EAVOL. Sum grows until ~9:30 AM ET (missing future minutes) but never includes RTH.
    """
    if pm_lo >= pm_hi:
        return 0.0
    total = 0.0
    for b in fetch_minute_bars(ticker, pm_lo, pm_hi, caller=caller):
        t = b.get("t")
        if t is None:
            continue
        t_ms = int(t)
        if not _eavol_premarket_includes(t_ms, current_d):
            continue
        v = b.get("v")
        if v is not None:
            total += float(v)
    return total


def _sum_eavol_afterhours(
    ticker: str, prior_d: date, ah_lo: int, ah_hi: int, *, caller: str
) -> float:
    """After-hours for EAVOL: prior session date only, [16:00, 20:00) ET in clock terms."""
    if ah_lo >= ah_hi:
        return 0.0
    total = 0.0
    for b in fetch_minute_bars(ticker, ah_lo, ah_hi, caller=caller):
        t = b.get("t")
        if t is None:
            continue
        t_ms = int(t)
        if not _eavol_afterhours_includes(t_ms, prior_d):
            continue
        v = b.get("v")
        if v is not None:
            total += float(v)
    return total


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


def _cell_from_raw(raw: dict[str, Any], *names: str) -> str:
    want = {n.lower() for n in names}
    for k, v in raw.items():
        if str(k).strip().lower() in want and v is not None:
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return ""


def _cell_in_order(raw: dict[str, Any], *header_names: str) -> str:
    """First non-empty cell matching *header_names* in order (avoids ambiguous multi-match)."""
    for name in header_names:
        s = _cell_from_raw(raw, name)
        if s:
            return s
    return ""


def _cell_key_contains(raw: dict[str, Any], substr: str) -> str:
    sl = substr.lower()
    for k, v in raw.items():
        if sl in str(k).lower() and v is not None:
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return ""


def _format_usd_compact(usd: float) -> str:
    """Display USD notional as K / M / B / T (2 decimals except K)."""
    au = abs(usd)
    if au >= 1e12:
        return f"{usd / 1e12:.2f}T"
    if au >= 1e9:
        return f"{usd / 1e9:.2f}B"
    if au >= 1e6:
        return f"{usd / 1e6:.2f}M"
    if au >= 1e3:
        return f"{usd / 1e3:.1f}K"
    return f"{usd:.0f}"


def _mcap_cell_to_usd(raw: str) -> float | None:
    """Parse FinViz market cap to USD.

    Elite exports usually use **K / M / B** suffixes (handled by :func:`fetch_elite._parse_num`).

    Plain numbers without a suffix are typically **millions of USD** (e.g. ``846000`` → $846B,
    ``450`` → $450M). Very large plain integers (``>= 10_000_000``) are treated as **full USD**
    (e.g. ``45000000`` → $45M), matching FinViz when the CSV already expands to dollars.
    """
    o = (raw or "").strip().replace("\u00a0", " ")
    if not o or o in "-—":
        return None
    # Explicit trillion (fetch_elite._parse_num only knows K/M/B)
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
    # Plain numeric: default scale is **millions of USD** (FinViz screener-style).
    if n >= 10_000_000:
        return n
    return n * 1e6


def _format_market_cap_display(raw: str) -> str:
    """Normalize FinViz market cap to ``K`` / ``M`` / ``B`` / ``T`` labels."""
    s0 = (raw or "").strip()
    if not s0 or s0 in "-—":
        return "—"
    usd = _mcap_cell_to_usd(s0)
    if usd is not None and usd > 0:
        return _format_usd_compact(usd)
    return _short(s0, 22)


def _find_market_cap_raw(raw: dict[str, Any]) -> str:
    """Resolve Market Cap cell across common FinViz header spellings."""
    for key in (
        "Market Cap",
        "Market Cap.",
        "Mkt Cap",
        "MarketCap",
        "market_cap",
        "Market cap",
    ):
        v = _cell_from_raw(raw, key)
        if v:
            return v
    for k, v in raw.items():
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in ("-", "—"):
            continue
        lk = str(k).strip().lower()
        if "market" in lk and "cap" in lk:
            return s
    return ""


def _short(s: str, max_len: int = 36) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip()).replace("|", "/")
    if not t:
        return "—"
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def _gap_pct_number(raw: dict[str, Any]) -> float | None:
    g = _cell_from_raw(
        raw,
        "Gap",
        "Performance (Gap)",
        "Today Performance (Gap)",
        "Gap %",
    )
    if not g or g in "—-":
        return None
    return _parse_num(g)


def _fundamentals_from_raw(raw: dict[str, Any]) -> dict[str, str]:
    """Best-effort v=152 labels; missing columns show —."""
    company = _short(_cell_from_raw(raw, "Company", "company"), 42)
    sector = _short(_cell_from_raw(raw, "Sector", "sector"), 36)
    industry = _short(_cell_from_raw(raw, "Industry", "industry"), 36)
    # Theme = FinViz "Sector/Theme" (not ETF tags).
    theme = _short(
        _cell_from_raw(raw, "Sector/Theme", "Sector/Theme AscDesc"),
        40,
    )
    perf_1m = _short(_cell_from_raw(raw, "Performance (Month)", "Perf Month"), 10)
    perf_1y = _short(_cell_from_raw(raw, "Performance (Year)", "Performance (Year To Date)"), 10)
    pe = _short(_cell_from_raw(raw, "P/E", "P/E (TTM)", "PE"), 12)
    eps_ttm = _short(_cell_from_raw(raw, "EPS (TTM)", "EPS ttm"), 14)
    # Latest reported quarter: estimate vs actual (column names vary by FinViz export).
    eps_est_rq = _cell_from_raw(
        raw,
        "EPS Estimate Last Quarter",
        "EPS Estimate Current Quarter",
        "EPS Est Last Quarter",
        "EPS Est Current Quarter",
    )
    eps_act_rq = _cell_from_raw(
        raw,
        "EPS Actual Last Quarter",
        "EPS Last Quarter",
        "EPS Actual",
        "EPS Reported Last Quarter",
    )
    eps_surprise = _short(_cell_from_raw(raw, "EPS Surprise"), 14)
    rev_est_rq = _cell_from_raw(
        raw,
        "Revenue Estimate Last Quarter",
        "Revenue Estimate Current Quarter",
        "Sales Estimate Last Quarter",
        "Revenue Est Last Quarter",
    )
    rev_act_rq = _cell_from_raw(
        raw,
        "Revenue Actual Last Quarter",
        "Sales Actual Last Quarter",
        "Revenue Last Quarter",
        "Sales Last Quarter",
    )
    rev_surprise = _short(_cell_from_raw(raw, "Revenue Surprise", "Sales Surprise"), 14)
    guidance = _short(_cell_key_contains(raw, "guidance"), 72)
    if guidance == "—":
        guidance = _short(
            _cell_from_raw(
                raw,
                "EPS Growth Next Year",
                "EPS Estimate Next Quarter",
                "Sales Growth Next Year",
            ),
            72,
        )
    market_cap = _format_market_cap_display(_find_market_cap_raw(raw))
    shares_float = _fmt_float_shares_display(
        _cell_in_order(raw, "Shares Float", "Share Float", "Float", "shares float")
    )
    short_float = _short(
        _cell_in_order(raw, "Short Float", "Short Interest", "Short float"),
        12,
    )
    country = _short(_cell_from_raw(raw, "Country", "country"), 28)
    return {
        "company": company,
        "sector": sector,
        "industry": industry,
        "theme": theme,
        "perf_1m": perf_1m,
        "perf_1y": perf_1y,
        "pe": pe,
        "eps_ttm": eps_ttm,
        "eps_est_rq": eps_est_rq,
        "eps_act_rq": eps_act_rq,
        "eps_surprise": eps_surprise,
        "rev_est_rq": rev_est_rq,
        "rev_act_rq": rev_act_rq,
        "rev_surprise": rev_surprise,
        "guidance": guidance,
        "market_cap": market_cap,
        "shares_float": shares_float,
        "short_float": short_float,
        "country": country,
    }


def _fmt_delta_eps(delta: float) -> str:
    ad = abs(delta)
    if ad < 1e-9:
        return f"{delta:+.4f}"
    if ad < 1:
        s = f"{delta:+.4f}"
        return s.rstrip("0").rstrip(".")
    return f"{delta:+.2f}"


def _fmt_delta_rev(delta: float) -> str:
    ad = abs(delta)
    if ad >= 1e9:
        return f"{delta/1e9:+.2f}B"
    if ad >= 1e6:
        return f"{delta/1e6:+.2f}M"
    if ad >= 1e3:
        return f"{delta/1e3:+.2f}K"
    return f"{delta:+,.0f}"


def _est_act_delta_block(
    title: str,
    est: str,
    act: str,
    surprise_fb: str,
    *,
    scale: str = "eps",
) -> str:
    """One line: Est · Act · Δ, or surprise % if FinViz did not ship est/act columns."""
    e = (est or "").strip()
    a = (act or "").strip()
    pe = _parse_num(e) if e else None
    pa = _parse_num(a) if a else None
    if pe is not None and pa is not None:
        delta = pa - pe
        if scale == "rev":
            d_str = _fmt_delta_rev(delta)
        else:
            d_str = _fmt_delta_eps(delta)
        return f"**{title}** Est {e} · Act {a} · Δ {d_str}"
    if e or a:
        return f"**{title}** Est {e or '—'} · Act {a or '—'} · Δ —"
    sf = (surprise_fb or "").strip()
    if sf and sf != "—":
        return f"**{title}** Est/Act n/a · Surprise {sf}"
    return f"**{title}** Est — · Act — · Δ —"


def _build_gap_atr_fields(raw: dict[str, Any], normed: dict[str, Any]) -> dict[str, str]:
    gap_n = _gap_pct_number(raw)
    gap_str = f"{gap_n:+.2f}%" if gap_n is not None else "—"
    atr_pct = normed.get("atr_pct")
    atr_str = f"{float(atr_pct):.2f}%" if atr_pct is not None else "—"
    gap_atr_str = "—"
    if gap_n is not None and atr_pct is not None and abs(float(atr_pct)) > 1e-6:
        gap_atr_str = f"{gap_n / float(atr_pct):.2f}×"
    return {
        "gap_str": gap_str,
        "atr_str": atr_str,
        "gap_atr_str": gap_atr_str,
    }


def eavol_tier_emoji(pct: float | None) -> str:
    if pct is None:
        return "⬛"
    if pct >= _EAVOL_HOT:
        return "🔥"
    if pct >= _EAVOL_WATCH:
        return "🟡"
    return "⬜"


def _enrich_one_ticker(
    raw: dict[str, Any],
    normed: dict[str, Any],
    *,
    ticker: str,
    et_today: date,
    prior_d: date,
    current_d: date,
    ah_lo: int,
    ah_hi: int,
    pm_lo: int,
    pm_hi: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ticker": (normed.get("ticker") or "").upper(),
        "price": normed.get("price") or "—",
        "change": normed.get("change") or "—",
        "volume": _fmt_volume_cell_v152(str(normed.get("volume") or "")),
        "pct_eavol": None,
    }
    out.update(_fundamentals_from_raw(raw))
    out.update(_build_gap_atr_fields(raw, normed))

    try:
        avg = _avg_daily_volume_21(ticker, et_today)
        if avg is None or avg <= 0:
            return out
        ah = _sum_eavol_afterhours(
            ticker, prior_d, ah_lo, ah_hi, caller=f"eavol_ah_{ticker}"
        )
        pm = _sum_eavol_premarket(
            ticker, current_d, pm_lo, pm_hi, caller=f"eavol_pm_{ticker}"
        )
        ed_raw = (normed.get("earnings_date") or "").strip() or _cell_from_raw(
            raw, "Earnings Date", "Earnings"
        )
        sess_order, _ = _classify_earnings_session(ed_raw)
        # BMO (today pre-market): only today's pre-market vs 21d ADV — no prior AH.
        # AMC (yesterday after-hours) or unknown: prior day AH + current session pre-market; never RTH.
        ext = pm if sess_order == 0 else ah + pm
        out["pct_eavol"] = (ext / avg) * 100.0
    except Exception as e:
        logger.warning("inplay_earnings %s: %s", ticker, e)
    return out


def fetch_inplay_earnings_rows() -> tuple[list[dict[str, Any]], str]:
    """FinViz earnings + Massive %EAVOL; sorted by EAVOL desc."""
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

    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        normed = _normalize_row(raw)
        t = (normed.get("ticker") or "").strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        candidates.append((t, raw, normed))
        if len(candidates) >= INPLAY_EARNINGS_CANDIDATE_CAP:
            break

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = {
            ex.submit(
                _enrich_one_ticker,
                raw,
                normed,
                ticker=t,
                et_today=et_day,
                prior_d=prior_d,
                current_d=current_d,
                ah_lo=ah_lo,
                ah_hi=ah_hi,
                pm_lo=pm_lo,
                pm_hi=pm_hi,
            ): t
            for t, raw, normed in candidates
        }
        for fut in as_completed(futs):
            enriched.append(fut.result())

    def _sort_key(r: dict[str, Any]) -> tuple:
        p = r.get("pct_eavol")
        if p is None:
            return (1, 0.0, (r.get("ticker") or "").upper())
        return (0, -float(p), (r.get("ticker") or "").upper())

    enriched.sort(key=_sort_key)
    logger.info("inplay_earnings: %d rows", len(enriched))
    return enriched, screener


def build_inplay_earnings_embed_fields(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Discord embed fields: (name, value). One field per ticker; values capped for 1024 limit."""
    if not rows:
        return []
    out: list[tuple[str, str]] = []
    for r in rows[:INPLAY_EARNINGS_MAX_DISPLAY]:
        tk = (r.get("ticker") or "?").strip()
        emoji = eavol_tier_emoji(r.get("pct_eavol"))
        peav = r.get("pct_eavol")
        peav_s = f"{float(peav):.1f}%" if peav is not None else "—"
        name = f"{emoji} {tk} · EAVOL {peav_s}"[:256]

        co = r.get("company") or "—"
        mcap = r.get("market_cap") or "—"
        flt = r.get("shares_float") or "—"
        sflt = r.get("short_float") or "—"
        ctry = r.get("country") or "—"
        sec = r.get("sector") or "—"
        ind = r.get("industry") or "—"
        th = r.get("theme") or "—"
        p1m = r.get("perf_1m") or "—"
        p1y = r.get("perf_1y") or "—"
        pe = r.get("pe") or "—"
        eps_t = r.get("eps_ttm") or "—"
        eps_s = r.get("eps_surprise") or "—"
        rev_s = r.get("rev_surprise") or "—"
        gui = r.get("guidance") or "—"
        eps_rq = _est_act_delta_block(
            "EPS (report Q)",
            str(r.get("eps_est_rq") or ""),
            str(r.get("eps_act_rq") or ""),
            eps_s,
            scale="eps",
        )
        rev_rq = _est_act_delta_block(
            "Rev (report Q)",
            str(r.get("rev_est_rq") or ""),
            str(r.get("rev_act_rq") or ""),
            rev_s,
            scale="rev",
        )

        val = (
            f"**{co}**\n"
            f"**Mkt cap** {mcap} · **Float** {flt} · **Short float** {sflt} · **Country** {ctry}\n"
            f"**Sector** {sec} · **Industry** {ind} · **Sector/Theme** {th}\n"
            f"**1M** {p1m} · **1Y** {p1y} · **P/E** {pe}\n"
            f"**EPS (TTM)** {eps_t}\n"
            f"{eps_rq}\n"
            f"{rev_rq}\n"
            f"**Next Q / guidance** {gui}\n"
            f"Gap {r.get('gap_str')} · ATR {r.get('atr_str')} · Gap/ATR {r.get('gap_atr_str')}\n\n\n"
        )
        if len(val) > 1020:
            val = val[:1017] + "…"
        out.append((name, val))
    return out


def format_inplay_earnings_description(rows: list[dict[str, Any]], *, max_chars: int = 3800) -> str:
    """Legacy plain-text fallback (compact). Prefer :func:`build_inplay_earnings_embed_fields`."""
    if not rows:
        return "*No stocks matched this FinViz earnings screen.*"
    lines: list[str] = []
    n = 0
    for r in rows[:20]:
        tk = (r.get("ticker") or "").strip()
        em = eavol_tier_emoji(r.get("pct_eavol"))
        peav = r.get("pct_eavol")
        peav_s = f"{float(peav):.1f}%" if peav is not None else "—"
        line = f"{em} **{tk}** EAVOL {peav_s} · {r.get('company', '—')}"
        if n + len(line) > max_chars:
            break
        lines.append(line)
        n += len(line) + 1
    return "\n".join(lines)
