"""Build Discord webhook payloads (embeds) from scan rows and POST them.

Discord limits:
  - Embed description: 4096 chars
  - Total embed chars (title+desc+fields+footer): 6000
  - Up to 10 embeds per message
  - Webhook rate limit: ~30 req/60s per webhook URL

This module chunks rows into multiple messages when the table overflows the
description limit, and handles rate-limit (429) responses with retry.
"""

import logging
import re
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

EMBED_COLOR_ACCENT = 0x06B6D4  # Pradly cyan
EMBED_COLOR_EMPTY = 0x4B5563   # muted gray
MAX_DESC_LEN = 3900  # leave headroom inside the 4096 limit
MAX_RETRIES = 3

_SCREENER_BASE = "https://finviz.com/screener.ashx"


def _fmt_change(val) -> str:
    """Format change like +5.23% or -2.10%."""
    if not val:
        return ""
    s = str(val).strip().replace("%", "")
    try:
        n = float(s)
        return f"{n:+.2f}%"
    except (ValueError, TypeError):
        return str(val)


def _fmt_vol(val) -> str:
    """Format volume to human-readable (e.g. 1.2M, 350K)."""
    if not val:
        return ""
    s = str(val).strip().replace(",", "")
    try:
        n = float(s)
    except (ValueError, TypeError):
        return str(val)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def _fmt_price(val) -> str:
    if not val:
        return ""
    s = str(val).strip().replace("$", "").replace(",", "")
    try:
        return f"${float(s):.2f}"
    except (ValueError, TypeError):
        return str(val)


def _parse_num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "").replace("%", "")
    if not s or s in ("-", "—"):
        return None
    m = re.match(r"([\d.-]+)\s*([KMB])?", s, re.I)
    if not m:
        return None
    val = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    if suffix == "K":
        val *= 1e3
    elif suffix == "M":
        val *= 1e6
    elif suffix == "B":
        val *= 1e9
    return val


def _build_table_block(rows: list[dict]) -> list[str]:
    """Build monospace table lines from scan rows.

    Returns a list of strings, each line representing one ticker row.
    """
    lines: list[str] = []
    for r in rows:
        ticker = (r.get("ticker") or "").ljust(6)
        price = _fmt_price(r.get("price")).rjust(9)
        change = _fmt_change(r.get("change")).rjust(8)
        vol = _fmt_vol(r.get("volume")).rjust(7)
        rel = str(r.get("rel_vol") or "").rjust(5)
        lines.append(f"{ticker} {price} {change} {vol} {rel}")
    return lines


def _build_header_line() -> str:
    ticker = "Ticker".ljust(6)
    price = "Price".rjust(9)
    change = "Chg%".rjust(8)
    vol = "Vol".rjust(7)
    rel = "RVol".rjust(5)
    return f"{ticker} {price} {change} {vol} {rel}"


def build_embeds(scan_title: str, rows: list[dict], screener_url: str = "") -> list[dict]:
    """Build one or more Discord embed dicts for a scan.

    Returns a list of embed objects.  Each stays within Discord's character limits.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    if not rows:
        return [{
            "title": scan_title,
            "description": "*No results today.*",
            "color": EMBED_COLOR_EMPTY,
            "timestamp": timestamp,
            "footer": {"text": "Pradly Portal \u2022 FinViz"},
        }]

    header = _build_header_line()
    data_lines = _build_table_block(rows)

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0

    code_overhead = len("```\n") + len(header) + len("\n") + len("```")

    for line in data_lines:
        line_len = len(line) + 1  # +1 for newline
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

        title = scan_title
        if total_parts > 1:
            title = f"{scan_title} ({idx + 1}/{total_parts})"

        embed: dict = {
            "title": title,
            "description": desc,
            "color": EMBED_COLOR_ACCENT,
            "timestamp": timestamp,
            "footer": {"text": f"Pradly Portal \u2022 FinViz \u2022 {len(rows)} tickers"},
        }

        if screener_url and idx == 0:
            embed["url"] = screener_url

        embeds.append(embed)

    return embeds


def post_to_webhook(webhook_url: str, embeds: list[dict]) -> bool:
    """POST embed(s) to a Discord webhook URL.  Returns True on success.

    Sends one embed per message to stay safely within Discord's 6000 char
    total limit per message.
    """
    for idx, embed in enumerate(embeds):
        payload = {"embeds": [embed]}

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(webhook_url, json=payload, timeout=15)
                if resp.status_code == 204:
                    break
                if resp.status_code == 429:
                    retry_after = resp.json().get("retry_after", 5)
                    logger.warning("Discord 429, retrying after %.1fs", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text[:300]
                logger.error("Discord %d: %s", resp.status_code, body)
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                logger.error("Webhook POST failed (attempt %d): %s", attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
        else:
            logger.error("Webhook POST failed after %d retries for %s", MAX_RETRIES, webhook_url[:60])
            return False

        if idx < len(embeds) - 1:
            time.sleep(0.5)

    return True
