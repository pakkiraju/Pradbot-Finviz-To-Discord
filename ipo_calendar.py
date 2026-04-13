"""Fetch and parse the IPOScoop IPO calendar table (public HTML).

No API key. Skips SCOOP Rating and Rating Change columns (paywalled / not useful in Discord).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

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


def _row_to_line(row: dict[str, str]) -> str:
    return "\t".join(row[k] for k in ROW_KEYS)


def build_ipo_calendar_embed_dicts(rows: list[dict[str, str]]) -> list[dict]:
    """Build one or more webhook-style embed dicts for Discord (chunked description)."""
    timestamp = datetime.now(timezone.utc).isoformat()

    if not rows:
        return [
            {
                "title": "IPO Calendar",
                "description": "*Could not load any IPO rows from IPOScoop.*",
                "color": _EMBED_COLOR,
                "timestamp": timestamp,
                "footer": {"text": "IPOScoop"},
                "url": IPO_CALENDAR_URL,
            }
        ]

    header = "\t".join(
        [
            "Company",
            "Symbol",
            "Lead Managers",
            "Shares (M)",
            "Price Low",
            "Price High",
            "Est. $ Volume",
            "Expected",
        ]
    )
    data_lines = [_row_to_line(r) for r in rows]

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    code_overhead = len("```\n") + len(header) + len("\n") + len("```")

    for line in data_lines:
        line_len = len(line) + 1
        if current_len + line_len + code_overhead > MAX_DESC_LEN and current:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append(current)

    embeds: list[dict] = []
    total_parts = len(chunks)
    for idx, chunk in enumerate(chunks):
        body = "\n".join(chunk)
        desc = f"```\n{header}\n{body}\n```"
        title = "IPO Calendar"
        if total_parts > 1:
            title = f"IPO Calendar ({idx + 1}/{total_parts})"
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
