"""Fetch and parse the IPOScoop IPO calendar table (public HTML).

No API key. Skips SCOOP Rating and Rating Change columns (paywalled / not useful in Discord).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from discord_payload import MAX_DESC_LEN

logger = logging.getLogger(__name__)

IPO_CALENDAR_URL = "https://www.iposcoop.com/ipo-calendar/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MAX_RETRIES = 3
_EMBED_COLOR = 0x0D5C5C  # dark teal (IPOScoop table header)
# US Eastern calendar day for “today” (IPOScoop lists US listings).
_US_EAST = ZoneInfo("America/New_York")

# Keys for each row dict
ROW_KEYS = (
    "company",
    "symbol",
    "lead_managers",
    "shares_millions",
    "price_low",
    "price_high",
    "est_volume",
    "expected_trade",
)


def _cell_text(td: Any) -> str:
    s = td.get_text("\n", strip=True)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _find_calendar_table(soup: BeautifulSoup) -> Any | None:
    t = soup.select_one("table.standard-table.ipolist")
    if t is not None:
        return t
    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue
        th_texts = [th.get_text(strip=True) for th in thead.find_all("th")]
        if "Company" in th_texts and "Symbol proposed" in th_texts:
            return table
    return None


def fetch_ipo_calendar_rows() -> list[dict[str, str]]:
    """GET the IPO calendar page and return table rows (8 public columns).

    Returns an empty list on HTTP or parse failure (errors are logged).
    """
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(IPO_CALENDAR_URL, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("IPO calendar request failed (attempt %d): %s", attempt + 1, e)
            if attempt == _MAX_RETRIES - 1:
                return []
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    table = _find_calendar_table(soup)
    if table is None:
        logger.error("IPO calendar: no matching table found in HTML")
        return []

    tbody = table.find("tbody")
    if not tbody:
        logger.error("IPO calendar: table has no tbody")
        return []

    rows_out: list[dict[str, str]] = []
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 8:
            continue
        tds = tds[:8]
        values = [_cell_text(td) for td in tds]
        rows_out.append(dict(zip(ROW_KEYS, values)))

    if not rows_out:
        logger.warning("IPO calendar: table parsed but zero data rows")
    return rows_out


def _format_ipo_block(row: dict[str, str]) -> str:
    """One IPO: label + value on the same line; single newlines only (spacing between companies is added by caller)."""
    lines = [
        f"**Company:** {row['company']}",
        f"**Symbol:** {row['symbol']}",
        f"**Lead Managers:** {row['lead_managers']}",
        f"**Shares (millions):** {row['shares_millions']}",
        f"**Price low:** {row['price_low']}",
        f"**Price high:** {row['price_high']}",
        f"**Est. $ volume:** {row['est_volume']}",
        f"**Date:** {row['expected_trade']}",
    ]
    return "\n".join(lines)


def parse_expected_trade_date(expected_trade: str) -> date | None:
    """First M/D/YYYY in IPOScoop 'Expected to Trade' (e.g. '4/14/2026 Tuesday', '4/17/2026 Week of')."""
    s = (expected_trade or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def trading_date_today_us_east() -> date:
    """Calendar date in America/New_York (matches typical US IPO listing day)."""
    return datetime.now(_US_EAST).date()


def filter_ipo_rows_for_today(
    rows: list[dict[str, str]],
    as_of: date | None = None,
) -> list[dict[str, str]]:
    """Keep rows whose leading expected-trade date equals *as_of* (default: today US Eastern)."""
    target = as_of if as_of is not None else trading_date_today_us_east()
    out: list[dict[str, str]] = []
    for r in rows:
        d = parse_expected_trade_date(r.get("expected_trade", ""))
        if d == target:
            out.append(r)
    return out


def _chunk_ipo_descriptions(blocks: list[str]) -> list[str]:
    """Split IPO blocks across multiple descriptions within Discord length limits."""
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


def build_ipo_calendar_embed_dicts(
    rows: list[dict[str, str]],
    *,
    title_base: str = "IPO Calendar",
    empty_description: str = "*Could not load any IPO rows from IPOScoop.*",
) -> list[dict]:
    """Build one or more webhook-style embed dicts for Discord (chunked description)."""
    timestamp = datetime.now(timezone.utc).isoformat()

    if not rows:
        return [
            {
                "title": title_base,
                "description": empty_description,
                "color": _EMBED_COLOR,
                "timestamp": timestamp,
                "footer": {"text": "IPOScoop"},
                "url": IPO_CALENDAR_URL,
            }
        ]

    blocks = [_format_ipo_block(r) for r in rows]
    descriptions = _chunk_ipo_descriptions(blocks)
    embeds: list[dict] = []
    total_parts = len(descriptions)
    for idx, desc in enumerate(descriptions):
        title = title_base
        if total_parts > 1:
            title = f"{title_base} ({idx + 1}/{total_parts})"
        embeds.append(
            {
                "title": title,
                "description": desc,
                "color": _EMBED_COLOR,
                "timestamp": timestamp,
                "footer": {"text": f"IPOScoop • {len(rows)} IPOs"},
                **({"url": IPO_CALENDAR_URL} if idx == 0 else {}),
            }
        )
    return embeds
