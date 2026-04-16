"""Fetch latest news articles for a ticker from Finviz (quote page).

The Elite CSV endpoint ``news_export.ashx?v=1&t=SYMBOL`` returns a broad news stream
that does not match the ticker-specific list on the stock quote page. To align with
what users see when they open ``quote.ashx?t=...``, we parse the ``#news-table`` block
from that page (same rows as the Finviz UI).

Legacy CSV export is used only as a fallback if the table cannot be parsed.
"""

import csv
import io
import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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


def _get_api_key() -> str | None:
    key = os.environ.get("FINVIZ_API_KEY", "").strip()
    return key or None


def fetch_news(symbol: str, limit: int = 5) -> list[NewsArticle]:
    """Fetch the latest news articles for *symbol* (newest-first, up to *limit*).

    Primary source is the quote page news table — the same list as on Finviz's
    ticker page. Falls back to Elite CSV export only if parsing yields no rows.
    """
    articles = _fetch_news_from_quote_page(symbol, limit)
    if articles:
        return articles

    logger.info("[news:%s] quote page parse empty; trying news_export CSV fallback", symbol)
    return _fetch_news_csv(symbol, limit)


def _fetch_news_from_quote_page(symbol: str, limit: int) -> list[NewsArticle]:
    """Parse ``#news-table`` from ``finviz.com/quote.ashx``."""
    t = quote(symbol.strip().upper(), safe="")
    url = f"https://finviz.com/quote.ashx?t={t}"
    logger.info("[news:%s] fetching quote page %s", symbol, url)

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2**attempt) * 15
                logger.warning(
                    "[news:%s] 429 rate limit, waiting %ds (attempt %d)",
                    symbol,
                    wait,
                    attempt + 1,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[news:%s] request failed: %s (attempt %d)", symbol, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2**attempt) * 5)
            continue
    else:
        logger.error("[news:%s] quote page failed after %d retries", symbol, _MAX_RETRIES)
        return []

    text = resp.text
    if not text.strip():
        return []

    soup = BeautifulSoup(text, "html.parser")
    table = soup.find("table", id="news-table")
    if not table:
        logger.warning("[news:%s] no #news-table in quote HTML", symbol)
        return []

    articles: list[NewsArticle] = []
    for row in table.find_all("tr"):
        link = row.find("a", class_="tab-link-news")
        if not link:
            continue
        href = (link.get("href") or "").strip()
        title = link.get_text(strip=True)
        if not href or not title:
            continue

        date_str = ""
        tds = row.find_all("td", recursive=False)
        if tds:
            date_str = tds[0].get_text(strip=True)

        source = ""
        right = row.find("div", class_="news-link-right")
        if right:
            sp = right.find("span")
            if sp:
                source = sp.get_text(strip=True).strip().strip("()")

        articles.append(
            NewsArticle(
                title=title,
                source=source,
                date=date_str,
                url=href,
                category="",
            )
        )
        if len(articles) >= limit:
            break

    logger.info("[news:%s] parsed %d articles from quote page", symbol, len(articles))
    return articles


def _fetch_news_csv(symbol: str, limit: int) -> list[NewsArticle]:
    """Elite ``news_export.ashx`` — fallback only; may not match quote page ordering."""
    api_key = _get_api_key()
    if not api_key:
        logger.error("FINVIZ_API_KEY not set. Add it in Railway → service → Variables.")
        return []

    url = f"https://elite.finviz.com/news_export.ashx?v=1&t={symbol}&auth={api_key}"
    logger.info("[news:%s] fetching %s", symbol, url.split("&auth=")[0])

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = (2**attempt) * 15
                logger.warning(
                    "[news:%s] 429 rate limit, waiting %ds (attempt %d)",
                    symbol,
                    wait,
                    attempt + 1,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning("[news:%s] request failed: %s (attempt %d)", symbol, e, attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep((2**attempt) * 5)
            continue
    else:
        logger.error("[news:%s] CSV failed after %d retries", symbol, _MAX_RETRIES)
        return []

    raw = resp.text.strip().lstrip("\ufeff")

    if not raw:
        logger.warning("[news:%s] empty response from FinViz CSV", symbol)
        return []

    if raw.startswith("<"):
        if "login" in raw[:2000].lower() or "sign in" in raw[:2000].lower():
            logger.error("[news:%s] FinViz returned login page — check FINVIZ_API_KEY", symbol)
        else:
            logger.warning("[news:%s] got HTML instead of CSV", symbol)
        return []

    return _parse_csv(raw, symbol, limit)


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

        articles.append(
            NewsArticle(
                title=title,
                source=mapped.get("source", ""),
                date=mapped.get("date", ""),
                url=article_url,
                category=mapped.get("category", ""),
            )
        )

    logger.info("[news:%s] parsed %d articles from CSV", symbol, len(articles))
    return articles[:limit]
