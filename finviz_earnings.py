"""FinViz Elite earnings screener CSV — today / this week (v=152 custom columns)."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Literal

from fetch_elite import fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS

EarningsPeriod = Literal["today", "weekly"]

# Matches user-provided screener links; export uses same f= + ft=4 + v=152 columns.
SCREENER_URLS: dict[EarningsPeriod, str] = {
    "today": "https://elite.finviz.com/screener.ashx?v=152&f=earningsdate_today&ft=4",
    "weekly": "https://elite.finviz.com/screener.ashx?v=152&f=earningsdate_thisweek&ft=4",
}

_FILTER_PARAM: dict[EarningsPeriod, str] = {
    "today": "earningsdate_today",
    "weekly": "earningsdate_thisweek",
}


def _export_url(period: EarningsPeriod) -> str:
    f = _FILTER_PARAM[period]
    return (
        f"https://elite.finviz.com/export.ashx?v=152&ft=4&f={f}&o=-volume&c={V152_EXPORT_COLUMNS}"
    )


def _cell(raw: dict, *names: str) -> str:
    keys = {str(k).strip().lower(): k for k in raw}
    for n in names:
        k = keys.get(n.lower())
        if k is not None:
            v = raw.get(k)
            if v is not None and str(v).strip() not in ("", "-", "—"):
                return str(v).strip()
    return ""


def _format_earnings_time_only(raw: str) -> str:
    """Show only clock time / session token — no calendar date (today/weekly sections carry the day)."""
    if not raw:
        return "—"
    t = str(raw).strip()
    # H:MM:SS AM/PM → 4:30PM
    t = re.sub(
        r"\b(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)\b",
        lambda m: f"{int(m.group(1))}:{m.group(2)}{m.group(4).upper()}",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(\d{1,2}):(\d{2}):(\d{2})([AP]M)\b",
        lambda m: f"{int(m.group(1))}:{m.group(2)}{m.group(4).upper()}",
        t,
        flags=re.IGNORECASE,
    )
    # H:MM AM/PM (no seconds)
    t = re.sub(
        r"\b(\d{1,2}):(\d{2})\s*([AP]M)\b",
        lambda m: f"{int(m.group(1))}:{m.group(2)}{m.group(3).upper()}",
        t,
        flags=re.IGNORECASE,
    )
    # Strip calendar dates (keep times & tokens like AMC/BMO)
    t = re.sub(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", "", t)
    t = re.sub(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:\s*,?\s*20\d{2})?\b",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b\d{1,2}/\d{1,2}(?:/20\d{2})?\b", "", t)
    t = re.sub(r",?\s+20\d{2}\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if t else "—"


def _finviz_thousands_to_shares(raw: str) -> float | None:
    """Convert FinViz volume to shares: usually **thousands**; large bare integers may be full shares."""
    if not raw:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s in "-—":
        return None
    m = re.match(r"^([\d.]+)\s*([KMB])?\s*$", s, re.I)
    if not m:
        return None
    val = float(m.group(1))
    suf = (m.group(2) or "").upper()
    if suf == "K":
        return val * 1e3
    if suf == "M":
        return val * 1e6
    if suf == "B":
        return val * 1e9
    # Plain number: Elite usually uses thousands; very large values are often full share counts.
    if val >= 1_000_000:
        return val
    return val * 1000.0


def _fmt_shares_compact(shares: float) -> str:
    if shares >= 1e9:
        return f"{shares / 1e9:.2f}B"
    if shares >= 1e6:
        return f"{shares / 1e6:.2f}M"
    if shares >= 1e3:
        return f"{shares / 1e3:.1f}K"
    return f"{int(shares)}"


def _fmt_volume_cell(raw: str) -> str:
    sh = _finviz_thousands_to_shares(raw)
    if sh is None:
        return (raw or "—")[:10]
    return _fmt_shares_compact(sh)


def _format_weekly_header(d: date) -> str:
    """Section title without year: Apr 10"""
    return f"{d.strftime('%b')} {d.day}"


_MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_calendar_date(earnings_text: str) -> date | None:
    """Best-effort parse for grouping weekly rows by calendar day."""
    t = (earnings_text or "").strip()
    if not t:
        return None
    y = date.today().year
    m = re.search(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?",
        t,
        re.I,
    )
    if m:
        mon = _MONTH_MAP.get(m.group(1)[:3].lower())
        d = int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
        if mon:
            try:
                return date(y, mon, d)
            except ValueError:
                return None
    m2 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", t)
    if m2:
        mm, dd, yy = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if yy < 100:
            yy += 2000
        try:
            return date(yy, mm, dd)
        except ValueError:
            return None
    return None


def _normalize_earnings_row(raw: dict) -> dict:
    ed = _cell(raw, "Earnings Date", "Earnings")
    price = _cell(raw, "Price", "price")
    change = _cell(raw, "Change", "change")
    vol = _cell(raw, "Volume", "volume")
    avg = _cell(raw, "Average Volume", "Avg Volume", "Average volume", "Avg Vol")
    tick = _cell(raw, "Ticker", "ticker")
    sort_d = _parse_calendar_date(ed)
    return {
        "ticker": tick.upper() if tick else "",
        "earnings_raw": ed,
        "price": price,
        "change": change,
        "volume": vol,
        "avg_vol": avg,
        "_sort_date": sort_d,
    }


def fetch_earnings_rows(
    period: EarningsPeriod,
    *,
    limit: int = 50,
    timeout: int = 90,
) -> tuple[list[dict], str]:
    """Download Elite CSV for earnings screen; return normalized rows and screener URL."""
    url = _export_url(period)
    raw_rows = fetch_csv_export(url, caller=f"earnings_{period}", timeout=timeout)
    rows: list[dict] = []
    for r in raw_rows:
        n = _normalize_earnings_row(r)
        if n["ticker"]:
            rows.append(n)
    far = date(2099, 12, 31)
    rows.sort(key=lambda x: (x.get("_sort_date") or far, x.get("ticker") or ""))
    screener = SCREENER_URLS[period]
    return rows[:limit], screener


def _table_block(rows: list[dict]) -> str:
    """Single monospace block (header + rows)."""
    tw = 20  # time (BMO/AMC etc. stay in FinViz text)
    vw = 9  # e.g. 123.45M, 12.50B
    width = 6 + 1 + tw + 1 + 8 + 1 + vw + 1 + vw + 1 + 7
    lines = [
        f"{'Ticker':<6} {'Time':<{tw}} {'Price':>8} {'Volume':>{vw}} {'AvgVol':>{vw}} {'Chg%':>7}",
        "-" * width,
    ]
    for r in rows:
        tk = (r.get("ticker") or "")[:6].ljust(6)
        ed_raw = r.get("earnings_raw") or ""
        tm = _format_earnings_time_only(ed_raw)[:tw].ljust(tw)
        pr = (r.get("price") or "")[:8].rjust(8)
        vo = _fmt_volume_cell(r.get("volume") or "")[:vw].rjust(vw)
        av = _fmt_volume_cell(r.get("avg_vol") or "")[:vw].rjust(vw)
        ch = (r.get("change") or "")[:7].rjust(7)
        lines.append(f"{tk} {tm} {pr} {vo} {av} {ch}")
    return "\n".join(lines)


def _weekly_grouped_body(rows: list[dict]) -> str:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = r.get("_sort_date")
        key = d.isoformat() if d is not None else "__tbd__"
        groups[key].append(r)

    ordered = sorted((k for k in groups if k != "__tbd__"))
    if "__tbd__" in groups:
        ordered.append("__tbd__")

    parts: list[str] = []
    for key in ordered:
        sub = groups[key]
        if key == "__tbd__":
            label = "Date TBD"
        else:
            try:
                label = _format_weekly_header(date.fromisoformat(key))
            except ValueError:
                label = key
        parts.append(f"— {label} —")
        parts.append(_table_block(sub))
        parts.append("")
    return "\n".join(parts).strip()


def format_earnings_embed_description(
    rows: list[dict],
    *,
    period: EarningsPeriod,
    max_chars: int = 3900,
) -> str:
    """Monospace table; weekly groups by parsed earnings date."""
    if not rows:
        return "*No rows matched the FinViz earnings screen.*"

    def try_code_block(body: str) -> str | None:
        code = f"```\n{body}\n```"
        return code if len(code) <= max_chars else None

    working = list(rows)
    while working:
        if period == "today":
            body = _table_block(working)
        else:
            body = _weekly_grouped_body(working)
        block = try_code_block(body)
        if block is not None:
            return block
        if len(working) <= 1:
            body = body[: max(0, max_chars - 20)]
            return f"```\n{body}…\n```"
        working = working[:-1]
    return "*Table too large for Discord embed.*"
