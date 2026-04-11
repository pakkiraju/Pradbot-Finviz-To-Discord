"""Shared heatmap pipeline: FinViz v=152 full export → treemap PNG (FinViz-style).

Used by **PradBot** `/heatmap` and **`post_heatmaps_elite.py`** (scheduled webhook).
"""

from __future__ import annotations

import logging
from datetime import date

from fetch_v152_universe import fetch_v152_full_universe
from heatmap_aggregate import rows_to_treemap_dataframe
from heatmap_treemap import (
    SUPPORTED_HEATMAP_UNIVERSES,
    UNIVERSE_TITLES,
    apply_treemap_filters,
    render_nested_treemap_png,
)

logger = logging.getLogger(__name__)

# Minimum tickers after filters to draw a readable treemap
_MIN_TICKERS = 5


def build_daily_heatmaps(
    *,
    universe: str = "sp500",
) -> tuple[list[tuple[str, bytes]], date] | tuple[None, None]:
    """Fetch v=152 CSV, filter by index universe, render nested sector/industry/stock treemap PNG.

    Default **universe** is ``sp500`` (S&P 500 constituents in the FinViz Index column).

    Returns ``([(filename, png_bytes)], as_of_date)``, or ``(None, None)`` on failure.
    """
    u = (universe or "sp500").strip().lower()
    if u not in SUPPORTED_HEATMAP_UNIVERSES:
        u = "sp500"
    rows = fetch_v152_full_universe()
    if not rows:
        logger.warning("heatmap_pipeline: empty export")
        return None, None

    df = rows_to_treemap_dataframe(rows)
    if df.empty:
        logger.warning("heatmap_pipeline: no rows after treemap parse")
        return None, None
    try:
        filtered = apply_treemap_filters(df, universe=u)
    except Exception as e:
        logger.exception("heatmap_pipeline: filter failed: %s", e)
        return None, None

    logger.info(
        "heatmap_pipeline: rows in=%d out=%d universe=%r",
        len(df),
        len(filtered),
        u,
    )

    if len(filtered) < _MIN_TICKERS:
        logger.warning(
            "heatmap_pipeline: only %d rows after filters (need >= %d) (check index universe)",
            len(filtered),
            _MIN_TICKERS,
        )
        return None, None

    title = UNIVERSE_TITLES.get(u, u)

    as_of = date.today()
    png = render_nested_treemap_png(filtered, title_suffix=title, as_of=as_of)
    if not png:
        logger.warning("heatmap_pipeline: render returned empty")
        return None, None

    fname = f"heatmap_{u}_{as_of.isoformat()}.png".replace(":", "-")
    return [(fname, png)], as_of
