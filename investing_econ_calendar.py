"""Investing.com economic calendar via official filtered-data POST (same endpoint as investpy).

Filters: United States + Canada, medium + high importance only, all categories.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from discord_payload import MAX_DESC_LEN

logger = logging.getLogger(__name__)

INVESTING_ECON_CALENDAR_URL = "https://www.investing.com/economic-calendar/"
_SERVICE_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"

# investpy COUNTRY_ID_FILTERS: united states = 5, canada = 6
_COUNTRY_US = 5
_COUNTRY_CA = 6
_IMPORTANCE_MEDIUM = 2
_IMPORTANCE_HIGH = 3
_TIMEZONE_US_EASTERN = 8

_EMBED_COLOR = 0x2962FF
_NY = ZoneInfo("America/New_York")

_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.investing.com",
    "Referer": "https://www.investing.com/economic-calendar/",
    "X-Requested-With": "XMLHttpRequest",
}

_IMPORTANCE_LABEL = {1: "low", 2: "medium", 3: "high"}

_MAX_RETRIES = 3
_MAX_PAGES = 40


def calendar_week_bounds_ny() -> tuple[date, date]:
    """Monday–Sunday of the current week in America/New_York."""
    today = datetime.now(_NY).date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def calendar_today_ny() -> date:
    """Today's calendar date in America/New_York."""
    return datetime.now(_NY).date()


def _build_form_data(date_from: date, date_to: date, limit_from: int) -> list[tuple[str, str | int]]:
    pairs: list[tuple[str, str | int]] = [
        ("dateFrom", date_from.isoformat()),
        ("dateTo", date_to.isoformat()),
        ("timeZone", _TIMEZONE_US_EASTERN),
        ("timeFilter", "timeOnly"),
        ("currentTab", "custom"),
        ("submitFilters", 1),
        ("limit_from", limit_from),
    ]
    for cid in (_COUNTRY_US, _COUNTRY_CA):
        pairs.append(("country[]", cid))
    for im in (_IMPORTANCE_MEDIUM, _IMPORTANCE_HIGH):
        pairs.append(("importance[]", im))
    return pairs


def _session_with_cookie() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS_BASE)
    try:
        s.get(INVESTING_ECON_CALENDAR_URL, timeout=30)
    except requests.RequestException as e:
        logger.warning("investing.com: initial GET failed (continuing): %s", e)
    return s


def _bottom_event_id_from_html(html_fragment: str) -> str | None:
    """Last event row id in document order (investpy uses bottom-of-table id for pagination)."""
    soup = BeautifulSoup(html_fragment, "html.parser")
    last: str | None = None
    for tr in soup.find_all("tr"):
        rid = tr.get("id") or ""
        if "eventRowId_" in rid:
            last = rid.replace("eventRowId_", "")
    return last


def _parse_rows_from_html(html_fragment: str) -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    curr_date = ""
    soup = BeautifulSoup(html_fragment, "html.parser")

    for tr in soup.find_all("tr"):
        rid = tr.get("id")
        if not rid:
            td = tr.find("td", id=re.compile(r"^theDay"))
            if td and td.get("id"):
                m = re.match(r"theDay(\d+)", td["id"])
                if m:
                    ts = int(m.group(1))
                    curr_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y")
            continue

        if "eventRowId_" not in rid:
            continue

        eid = rid.replace("eventRowId_", "")
        time_s = zone_s = currency_s = importance_s = event_s = actual_s = forecast_s = previous_s = ""

        for td in tr.find_all("td"):
            classes = td.get("class") or []
            cls = " ".join(classes)

            if "sentiment" in cls:
                ik = (td.get("data-img_key") or "").strip()
                if ik:
                    num = ik.replace("bull", "").strip()
                    if num.isdigit():
                        importance_s = _IMPORTANCE_LABEL.get(int(num), num)
                continue

            if "first" in classes and "left" in classes and "time" in cls:
                time_s = td.get_text(strip=True)
                continue

            if "flagCur" in cls:
                sp = td.find("span", title=True)
                if sp:
                    zone_s = (sp.get("title") or "").strip().lower()
                currency_s = td.get_text(strip=True)
                continue

            if "left" in classes and "event" in cls:
                event_s = td.get_text(" ", strip=True)
                continue

            tid = td.get("id") or ""
            if tid == f"eventActual_{eid}":
                actual_s = td.get_text(strip=True)
            elif tid == f"eventForecast_{eid}":
                forecast_s = td.get_text(strip=True)
            elif tid == f"eventPrevious_{eid}":
                previous_s = td.get_text(strip=True)

        if not event_s:
            continue

        rows_out.append(
            {
                "id": eid,
                "date": curr_date,
                "time": time_s,
                "zone": zone_s,
                "currency": currency_s,
                "importance": importance_s,
                "event": event_s,
                "actual": actual_s,
                "forecast": forecast_s,
                "previous": previous_s,
            }
        )

    return rows_out


def fetch_economic_calendar_rows(date_from: date, date_to: date) -> tuple[list[dict[str, str]], str | None]:
    """POST filtered calendar; paginate like investpy. Returns (rows, error_message_or_none)."""
    if date_to < date_from:
        return [], "Invalid date range (end before start)."

    session = _session_with_cookie()
    all_rows: list[dict[str, str]] = []
    last_id = "0"
    limit_from = 0

    while limit_from < _MAX_PAGES:
        data = _build_form_data(date_from, date_to, limit_from)
        payload: dict | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = session.post(_SERVICE_URL, data=data, timeout=60)
                if resp.status_code == 403:
                    return [], (
                        "Investing.com returned **403 Forbidden** (often Cloudflare). "
                        "Try again later or run from a different network."
                    )
                resp.raise_for_status()
                payload = resp.json()
                break
            except json.JSONDecodeError as e:
                logger.warning("investing.com: bad JSON (attempt %s): %s", attempt + 1, e)
                if attempt == _MAX_RETRIES - 1:
                    return [], "Could not parse Investing.com response (not JSON)."
            except requests.RequestException as e:
                logger.warning("investing.com: request failed (attempt %s): %s", attempt + 1, e)
                if attempt == _MAX_RETRIES - 1:
                    return [], f"Request failed: {e!s}."

        if not isinstance(payload, dict):
            return [], "Invalid response from Investing.com."

        html_fragment = payload.get("data")
        if not html_fragment or not isinstance(html_fragment, str):
            break

        bottom_id = _bottom_event_id_from_html(html_fragment)
        if bottom_id is not None and str(bottom_id) == str(last_id):
            break

        page_rows = _parse_rows_from_html(html_fragment)
        if not page_rows:
            break

        all_rows.extend(page_rows)
        last_id = page_rows[-1]["id"]
        limit_from += 1

    # Strip internal id from user-facing dicts if desired — keep for debugging; bot can omit in format
    for r in all_rows:
        r.pop("id", None)

    return all_rows, None


def _format_event_block(row: dict[str, str]) -> str:
    def _line(label: str, val: str) -> str:
        v = (val or "").strip()
        if not v or v == "&nbsp;":
            return ""
        return f"**{label}:** {v}"

    parts = [
        _line("Date", row.get("date", "")),
        _line("Time", row.get("time", "")),
        _line("Country", row.get("zone", "")),
        _line("Currency", row.get("currency", "")),
        _line("Importance", row.get("importance", "")),
        _line("Event", row.get("event", "")),
        _line("Actual", row.get("actual", "")),
        _line("Forecast", row.get("forecast", "")),
        _line("Previous", row.get("previous", "")),
    ]
    return "\n".join(p for p in parts if p)


def _chunk_event_descriptions(blocks: list[str]) -> list[str]:
    sep = "\n---\n"
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        if len(block) > MAX_DESC_LEN:
            block = block[: MAX_DESC_LEN - 3].rstrip() + "..."
        sep_len = len(sep) if current else 0
        trial_len = current_len + sep_len + len(block)
        if trial_len > MAX_DESC_LEN and current:
            chunks.append(sep.join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len = trial_len

    if current:
        chunks.append(sep.join(current))
    return chunks


def build_investing_econ_embed_dicts(
    rows: list[dict[str, str]],
    *,
    title_base: str,
    fetch_error: str | None = None,
    empty_description: str = "*No medium/high importance US or Canada events in this period.*",
) -> list[dict]:
    """Webhook-style embed dicts; chunk long descriptions."""
    timestamp = datetime.now(timezone.utc).isoformat()

    if fetch_error:
        return [
            {
                "title": title_base,
                "description": fetch_error,
                "color": _EMBED_COLOR,
                "timestamp": timestamp,
                "footer": {"text": "Investing.com"},
                "url": INVESTING_ECON_CALENDAR_URL,
            }
        ]

    if not rows:
        return [
            {
                "title": title_base,
                "description": empty_description,
                "color": _EMBED_COLOR,
                "timestamp": timestamp,
                "footer": {"text": "Investing.com"},
                "url": INVESTING_ECON_CALENDAR_URL,
            }
        ]

    blocks = [_format_event_block(r) for r in rows]
    blocks = [b for b in blocks if b]
    descriptions = _chunk_event_descriptions(blocks)
    embeds: list[dict] = []
    total_parts = len(descriptions)
    for idx, desc in enumerate(descriptions):
        title = title_base if total_parts == 1 else f"{title_base} ({idx + 1}/{total_parts})"
        embeds.append(
            {
                "title": title,
                "description": desc,
                "color": _EMBED_COLOR,
                "timestamp": timestamp,
                "footer": {"text": f"Investing.com • US+CA • medium/high • {len(rows)} events"},
                **({"url": INVESTING_ECON_CALENDAR_URL} if idx == 0 else {}),
            }
        )
    return embeds
