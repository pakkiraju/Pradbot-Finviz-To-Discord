"""Render matplotlib horizontal bar charts (diverging colormap) to PNG bytes."""

from __future__ import annotations

import io
import logging
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _figure_to_png_bytes(fig: plt.Figure, dpi: int = 120) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_group_bar_png(
    agg: pd.DataFrame,
    title: str,
    *,
    as_of: date | None = None,
    figsize: tuple[float, float] = (10, 7),
) -> bytes | None:
    """Horizontal bars: x = mean_change %, color from diverging colormap. Returns PNG bytes or None."""
    if agg is None or agg.empty:
        return None
    labels = agg["label"].astype(str).tolist()
    values = agg["mean_change"].astype(float).values
    counts = agg["count"].astype(int).values if "count" in agg.columns else None

    n = len(labels)
    y = np.arange(n)
    vmax = max(3.0, float(np.nanmax(np.abs(values))) if len(values) else 3.0)

    fig, ax = plt.subplots(figsize=figsize)
    norm = plt.Normalize(-vmax, vmax)
    cmap = plt.cm.RdYlGn
    colors = cmap(norm(values))

    ax.barh(y, values, color=colors, edgecolor="none", height=0.72)
    ax.set_yticks(y)
    if counts is not None:
        short_labels = [
            (lab[:36] + "…" if len(lab) > 38 else lab) + f" (n={counts[i]})"
            for i, lab in enumerate(labels)
        ]
    else:
        short_labels = [lab[:42] + "…" if len(lab) > 44 else lab for lab in labels]
    ax.set_yticklabels(short_labels, fontsize=8)
    ax.axvline(0, color="#333", linewidth=0.6)
    ax.set_xlabel("Mean daily change % (group)")
    subtitle = f"FinViz v=152 • {as_of.isoformat()}" if as_of else "FinViz v=152 (delayed)"
    ax.set_title(f"{title}\n{subtitle}", fontsize=11)
    ax.grid(axis="x", alpha=0.25)

    fig.patch.set_facecolor("#f4f4f5")
    ax.set_facecolor("#fafafa")
    return _figure_to_png_bytes(fig)


def render_all_heatmaps(
    aggregates: dict[str, pd.DataFrame],
    *,
    as_of: date | None = None,
) -> list[tuple[str, bytes]]:
    """Return list of (filename, png_bytes) for non-empty aggregates."""
    titles = {
        "sector": "Sector — mean daily change %",
        "industry": "Industry — mean daily change % (top by |move|)",
        "index": "Index — mean daily change %",
        "etf_sector": "ETFs — mean daily change % by sector",
    }
    out: list[tuple[str, bytes]] = []
    for key, title in titles.items():
        df = aggregates.get(key)
        if df is None or df.empty:
            logger.warning("Skipping empty heatmap: %s", key)
            continue
        png = render_group_bar_png(df, title, as_of=as_of)
        if png:
            out.append((f"heatmap_{key}.png", png))
    return out
