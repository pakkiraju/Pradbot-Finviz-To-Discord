"""Fetch latest news articles for a ticker from FinViz Elite.

Uses the news export endpoint:
  https://elite.finviz.com/news_export.ashx?v=1&t=SYMBOL&auth=KEY

Returns parsed NewsArticle objects sorted by date descending (newest first).
"""

import csv
import io
import logging
import os
import time
from dataclasses import dataclass
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


@dataclass
class NewsArticle:
    title: str
    source: str
    date: str
    url: str
    category: str


def _load_env():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    import env_setup

    env_setup.configure_environment()
    _ENV_LOADED = True


def _get_api_key() -> str | None:
    _load_env()
    key = os.environ.get("FINVIZ_API_KEY", "").strip()
    return key or None


def fetch_news(symbol: str, limit: int = 5) -> list[NewsArticle]:
    """Fetch the latest news articles for *symbol* from FinViz Elite.

    Returns up to *limit* articles sorted newest-first.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "FINVIZ_API_KEY not set. Create a .env file in %s with:\n"
            "  FINVIZ_API_KEY=your_key_here",
            Path(__file__).resolve().parent,
        )
        return []

    url = f"https://elite.finviz.com/news_export.ashx?v=1&t={symbol}&auth={api_key}"
    logger.info("[news:%s] fetching %s", symbol, url.split("&auth=")[0])

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2 ** attempt) * 15
                logger.warning("[news:%s] 429 rate limit, waiting %ds (attempt %d)", symbol, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[news:%s] request failed: %s (attempt %d)", symbol, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) * 5)
            continue
    else:
        logger.error("[news:%s] failed after %d retries", symbol, _MAX_RETRIES)
        return []

    text = resp.text.strip().lstrip("\ufeff")

    if not text:
        logger.warning("[news:%s] empty response from FinViz", symbol)
        return []

    if text.startswith("<"):
        if "login" in text[:2000].lower() or "sign in" in text[:2000].lower():
            logger.error("[news:%s] FinViz returned login page — check FINVIZ_API_KEY", symbol)
        else:
            logger.warning("[news:%s] got HTML instead of CSV", symbol)
        return []

    return _parse_csv(text, symbol, limit)


def _parse_csv(text: str, symbol: str, limit: int) -> list[NewsArticle]:
    """Parse raw CSV text into NewsArticle objects."""
    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        logger.warning("[news:%s] CSV parse init failed: %s", symbol, e)
        return []

    if not reader.fieldnames:
        logger.warning("[news:%s] CSV has no headers", symbol)
        return []

    header_map: dict[str, str] = {}
    for h in reader.fieldnames:
        key = h.strip().lower()
        if key == "title":
            header_map[h] = "title"
        elif key == "source":
            header_map[h] = "source"
        elif key == "date":
            header_map[h] = "date"
        elif key == "url":
            header_map[h] = "url"
        elif key == "category":
            header_map[h] = "category"

    articles: list[NewsArticle] = []
    for raw in reader:
        mapped = {canon: raw.get(csv_col, "").strip() for csv_col, canon in header_map.items()}

        title = mapped.get("title", "")
        article_url = mapped.get("url", "")
        if not title or not article_url:
            continue

        articles.append(NewsArticle(
            title=title,
            source=mapped.get("source", ""),
            date=mapped.get("date", ""),
            url=article_url,
            category=mapped.get("category", ""),
        ))

    logger.info("[news:%s] parsed %d articles", symbol, len(articles))
    return articles[:limit]
