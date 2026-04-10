"""Fetch group/sector/industry aggregate data from FinViz Elite.

Uses the groups export endpoint:
  https://elite.finviz.com/grp_export.ashx?g=sector&v=152&o=name&auth=KEY

Returns rows as plain dicts keyed by CSV header names.
"""

import csv
import io
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ENV_LOADED = False
_MAX_RETRIES = 4

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

VALID_GROUPS = frozenset({"sector", "industry", "country", "cap"})

VIEW_PRESETS: dict[str, int] = {
    "overview": 112,
    "valuation": 122,
    "performance": 142,
    "custom": 152,
}


def _load_env():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass
    _ENV_LOADED = True


def _get_api_key() -> str | None:
    _load_env()
    key = os.environ.get("FINVIZ_API_KEY", "").strip()
    return key or None


def fetch_groups(
    group: str,
    view: int = 152,
    order: str = "name",
) -> tuple[list[str], list[dict[str, str]]]:
    """Fetch group export CSV and return (column_names, rows).

    Each row is a dict keyed by CSV header (e.g. "Name", "Market Cap", "Change").
    Returns ([], []) on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return [], []

    if group not in VALID_GROUPS:
        logger.error("[groups] invalid group %r — must be one of %s", group, sorted(VALID_GROUPS))
        return [], []

    url = (
        f"https://elite.finviz.com/grp_export.ashx"
        f"?g={group}&v={view}&o={order}&auth={api_key}"
    )
    logger.info("[groups:%s] fetching %s", group, url.split("&auth=")[0])

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[groups:%s] 429 rate limit, waiting %ds (attempt %d)", group, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[groups:%s] request failed: %s (attempt %d)", group, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[groups:%s] failed after %d retries", group, _MAX_RETRIES)
        return [], []

    text = resp.text.strip().lstrip("\ufeff")

    if not text:
        logger.warning("[groups:%s] empty response from FinViz", group)
        return [], []

    if text.startswith("<"):
        if "login" in text[:2000].lower() or "sign in" in text[:2000].lower():
            logger.error("[groups:%s] FinViz returned login page — check FINVIZ_API_KEY", group)
        else:
            logger.warning("[groups:%s] got HTML instead of CSV", group)
        return [], []

    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        logger.warning("[groups:%s] CSV parse failed: %s", group, e)
        return [], []

    columns = list(reader.fieldnames or [])
    rows = [{k: v.strip() for k, v in row.items()} for row in reader]
    logger.info("[groups:%s] parsed %d rows, %d columns", group, len(rows), len(columns))
    return columns, rows
