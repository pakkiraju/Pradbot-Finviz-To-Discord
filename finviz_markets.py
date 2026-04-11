"""Multi-symbol futures / index chart snapshot for `/markets` (FinViz Elite chart.ashx)."""

from __future__ import annotations

import logging
import os
import time

from finviz_chart import fetch_chart

logger = logging.getLogger(__name__)

# Ordered display name and FinViz `t=` root (futures / index symbols).
MARKETS_SERIES: list[tuple[str, str]] = [
    ("Nasdaq 100", "NQ"),
    ("S&P 500", "ES"),
    ("DJIA", "YM"),
    ("Russell 2000", "ER2"),
    ("VIX", "VX"),
    ("Nikkei 225", "NKD"),
    ("Euro Stoxx 50", "EX"),
    ("DAX", "DY"),
]

_DELAY_SEC = float(os.environ.get("FINVIZ_ELITE_DELAY_SEC", "1.5"))


def fetch_markets_charts(timeframe: str) -> list[tuple[str, str, bytes | None]]:
    """Fetch candlestick PNGs for each market in order; returns (label, symbol, png_or_none).

    Spaces requests by ``FINVIZ_ELITE_DELAY_SEC`` (default 1.5s) between calls to reduce 429s.
    """
    out: list[tuple[str, str, bytes | None]] = []
    for i, (label, sym) in enumerate(MARKETS_SERIES):
        if i > 0:
            time.sleep(_DELAY_SEC)
        data = fetch_chart(sym, timeframe)
        if data is None:
            logger.warning("[markets] no chart for %s (%s)", label, sym)
        out.append((label, sym, data))
    return out
