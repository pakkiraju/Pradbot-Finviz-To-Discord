"""FinViz-style nested treemap: sector → industry → stock (size = market cap, color = change %)."""

from __future__ import annotations

import io
import logging
from datetime import date
from typing import Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import squarify
from matplotlib.patches import Rectangle

logger = logging.getLogger(__name__)

UniverseKey = Literal["sp500", "ndx100", "dow", "russell2000"]

# Index-filtered universes for `/heatmap` (full-export “all names” mode removed).
SUPPORTED_HEATMAP_UNIVERSES: frozenset[str] = frozenset(
    {"sp500", "ndx100", "dow", "russell2000"}
)

UNIVERSE_TITLES: dict[str, str] = {
    "sp500": "S&P 500",
    "ndx100": "NASDAQ 100",
    "dow": "Dow Jones Industrial",
    "russell2000": "Russell 2000",
}


def apply_treemap_filters(
    df: pd.DataFrame,
    *,
    universe: str = "sp500",
) -> pd.DataFrame:
    """Filter v=152 treemap rows by benchmark index (stocks and ETFs included)."""
    if df.empty:
        return df

    universe = (universe or "sp500").strip().lower()
    if universe not in SUPPORTED_HEATMAP_UNIVERSES:
        universe = "sp500"

    d = df.copy()

    # Finviz Index column strings vary; align with Market Metrics Dashboard INDEX_BASE_FILTERS.
    idx = d["index_name"].fillna("").astype(str)
    idx_u = idx.str.upper()
    idx_nospace = idx.str.replace(" ", "", regex=False).str.upper()

    if universe == "sp500":
        m = (
            idx_nospace.str.contains("SP500", na=False, regex=False)
            | idx.str.contains("S&P 500", case=False, na=False, regex=False)
            | (
                idx.str.contains("S&P", case=False, na=False, regex=False)
                & idx.str.contains("500", na=False, regex=False)
            )
        )
        d = d[m]
    elif universe == "ndx100":
        # Elite export often uses "NDX" or "NASDAQ-100", not the word "NASDAQ" alone.
        m = (
            idx_u.str.contains("NDX", na=False, regex=False)
            | idx_u.str.contains("NASDAQ-100", na=False, regex=False)
            | idx.str.contains("NASDAQ 100", case=False, na=False, regex=False)
            | (
                idx.str.contains("NASDAQ", case=False, na=False, regex=False)
                & idx.str.contains("100", na=False, regex=False)
            )
        )
        d = d[m]
    elif universe == "dow":
        # Finviz typically uses "DJIA"; "DJIA" does not contain the substring "Dow".
        m = (
            idx_u.str.contains("DJIA", na=False, regex=False)
            | idx.str.contains("Dow", case=False, na=False, regex=False)
            | idx.str.contains("Dow Jones", case=False, na=False, regex=False)
        )
        d = d[m]
    elif universe == "russell2000":
        m = (
            idx_u.str.contains("RUT", na=False, regex=False)
            | idx_nospace.str.contains("RUSSELL2000", na=False, regex=False)
            | (
                idx.str.contains("Russell", case=False, na=False, regex=False)
                & idx.str.contains("2000", na=False, regex=False)
            )
        )
        d = d[m]

    return d.reset_index(drop=True)


def _mpl_y_bottom(sq_y: float, sq_dy: float, canvas_h: float) -> float:
    """Squarify uses top-origin y; matplotlib Rectangle uses bottom-origin."""
    return canvas_h - sq_y - sq_dy


def _diverging_finviz_cmap() -> mcolors.LinearSegmentedColormap:
    """Dark, saturated red → charcoal → green (no pale yellow/white mid-tones)."""
    return mcolors.LinearSegmentedColormap.from_list(
        "finvizheat",
        [
            "#5c1010",
            "#7a1818",
            "#8f2525",
            "#5a3030",
            "#353535",
            "#181818",
            "#181818",
            "#283628",
            "#2f5c2f",
            "#247024",
            "#0d5a0d",
            "#084208",
        ],
        N=256,
    )


def _stroked_text(
    ax: plt.Axes,
    x: float,
    y: float,
    s: str,
    *,
    ha: str = "center",
    va: str = "center",
    fontsize: float = 8,
    fontweight: str | int = "bold",
    color: str = "#f4f4f4",
    stroke: str = "#080808",
    strokew: float = 2.8,
    zorder: float = 15,
) -> None:
    t = ax.text(
        x,
        y,
        s,
        ha=ha,
        va=va,
        fontsize=fontsize,
        fontweight=fontweight,
        color=color,
        zorder=zorder,
        clip_on=False,
    )
    t.set_path_effects([pe.withStroke(linewidth=strokew, foreground=stroke)])


def _draw_stock_leaves(
    ax: plt.Axes,
    g: pd.DataFrame,
    x: float,
    y: float,
    w: float,
    h: float,
    canvas_h: float,
    cmap: mcolors.Colormap,
    norm: mcolors.Normalize,
    min_label_side: float,
) -> None:
    g2 = g.sort_values("market_cap_usd", ascending=False)
    sizes = g2["market_cap_usd"].astype(float).clip(lower=1e5).tolist()
    if not sizes or sum(sizes) <= 0:
        return
    # Raw USD weights must be normalized to container area or squarify breaks (negative dx, one blob).
    sizes_n = squarify.normalize_sizes(sizes, w, h)
    rects = squarify.squarify(sizes_n, x, y, w, h)
    for (_, row), r in zip(g2.iterrows(), rects):
        xi, yi, dxi, dyi = r["x"], r["y"], r["dx"], r["dy"]
        yb = _mpl_y_bottom(yi, dyi, canvas_h)
        chg = float(row["change_pct"])
        face = cmap(norm(chg))
        ax.add_patch(
            Rectangle(
                (xi, yb),
                dxi,
                dyi,
                facecolor=face,
                edgecolor="#030303",
                linewidth=0.9,
            )
        )
        side = min(dxi, dyi)
        if side >= min_label_side:
            lbl = f"{row['ticker']}\n{chg:+.2f}%"
            fs = min(11, max(6, side * 0.38))
            _stroked_text(
                ax,
                xi + dxi / 2,
                yb + dyi / 2,
                lbl,
                fontsize=fs,
                fontweight="bold",
                color="#f8f8f8",
                stroke="#050505",
                strokew=max(2.2, fs * 0.22),
            )


def _draw_industry_level(
    ax: plt.Axes,
    df: pd.DataFrame,
    x: float,
    y: float,
    w: float,
    h: float,
    canvas_h: float,
    cmap: mcolors.Colormap,
    norm: mcolors.Normalize,
    min_label_side: float,
) -> None:
    ind_sizes = (
        df.groupby("industry", sort=False)["market_cap_usd"].sum().sort_values(ascending=False)
    )
    industries = ind_sizes.index.tolist()
    sizes = ind_sizes.astype(float).clip(lower=1e5).tolist()
    if not sizes:
        return
    sizes_n = squarify.normalize_sizes(sizes, w, h)
    rects = squarify.squarify(sizes_n, x, y, w, h)
    for ind, r in zip(industries, rects):
        xi, yi, dxi, dyi = r["x"], r["y"], r["dx"], r["dy"]
        yb = _mpl_y_bottom(yi, dyi, canvas_h)
        sub = df[df["industry"] == ind]
        ax.add_patch(
            Rectangle(
                (xi, yb),
                dxi,
                dyi,
                facecolor="none",
                edgecolor="#555555",
                linewidth=1.1,
            )
        )
        side = min(dxi, dyi)
        if side >= min_label_side * 1.8 and len(str(ind)) < 48:
            short = str(ind)[:44] + "…" if len(str(ind)) > 45 else str(ind)
            ifs = min(9, max(5, side * 0.08))
            _stroked_text(
                ax,
                xi + 0.4,
                yb + dyi - 0.35,
                short,
                ha="left",
                va="top",
                fontsize=ifs,
                fontweight="bold",
                color="#eaeaea",
                stroke="#050505",
                strokew=max(2.0, ifs * 0.25),
            )
        _draw_stock_leaves(ax, sub, xi, yi, dxi, dyi, canvas_h, cmap, norm, min_label_side)


def _draw_sector_level(
    ax: plt.Axes,
    df: pd.DataFrame,
    canvas_w: float,
    canvas_h: float,
    cmap: mcolors.Colormap,
    norm: mcolors.Normalize,
    min_label_side: float,
) -> None:
    sec_sizes = (
        df.groupby("sector", sort=False)["market_cap_usd"].sum().sort_values(ascending=False)
    )
    sectors = sec_sizes.index.tolist()
    sizes = sec_sizes.astype(float).clip(lower=1e5).tolist()
    if not sizes:
        return
    sizes_n = squarify.normalize_sizes(sizes, canvas_w, canvas_h)
    rects = squarify.squarify(sizes_n, 0, 0, canvas_w, canvas_h)
    for sec, r in zip(sectors, rects):
        xi, yi, dxi, dyi = r["x"], r["y"], r["dx"], r["dy"]
        yb = _mpl_y_bottom(yi, dyi, canvas_h)
        sub = df[df["sector"] == sec]
        ax.add_patch(
            Rectangle(
                (xi, yb),
                dxi,
                dyi,
                facecolor="none",
                edgecolor="#777777",
                linewidth=1.5,
            )
        )
        side = min(dxi, dyi)
        if side >= min_label_side * 2.2:
            sfs = min(11, max(7, side * 0.06))
            _stroked_text(
                ax,
                xi + 0.5,
                yb + dyi - 0.5,
                str(sec).upper()[:40],
                ha="left",
                va="top",
                fontsize=sfs,
                fontweight="bold",
                color="#f0f0f0",
                stroke="#050505",
                strokew=max(2.2, sfs * 0.22),
            )
        _draw_industry_level(ax, sub, xi, yi, dxi, dyi, canvas_h, cmap, norm, min_label_side)


def render_nested_treemap_png(
    df: pd.DataFrame,
    *,
    title_suffix: str,
    as_of: date | None = None,
    figsize: tuple[float, float] | None = None,
    dpi: int = 160,
) -> bytes | None:
    """Render nested sector/industry/stock treemap to PNG bytes."""
    if df is None or len(df) < 3:
        logger.warning("treemap: need at least 3 rows, got %s", len(df) if df is not None else 0)
        return None

    d = df.copy()
    d["sector"] = d["sector"].replace("—", "Unclassified").fillna("Unclassified")
    d["industry"] = d["industry"].replace("—", "Unclassified").fillna("Unclassified")

    vmax = float(np.nanpercentile(np.abs(d["change_pct"].astype(float)), 98))
    vmax = max(3.0, min(vmax, 15.0))
    norm = mcolors.Normalize(-vmax, vmax)
    cmap = _diverging_finviz_cmap()

    canvas_w, canvas_h = 100.0, 62.0
    # Figure aspect matches data limits so tiles are not stretched; large fig + dpi = sharper zoom.
    if figsize is None:
        aspect = canvas_w / canvas_h
        fw = 20.0
        figsize = (fw, fw / aspect)

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor="#0c0c0c")
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_facecolor("#0c0c0c")
    ax.set_xlim(0, canvas_w)
    ax.set_ylim(0, canvas_h)
    ax.axis("off")
    ax.margins(0)

    min_label = 0.9 * (canvas_h / 62.0)  # scale with canvas

    _draw_sector_level(ax, d, canvas_w, canvas_h, cmap, norm, min_label_side=min_label)

    # Title/date are in the Discord embed — image is treemap-only for maximum resolution.
    logger.debug("treemap PNG: %s as_of=%s", title_suffix, as_of)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=dpi,
        facecolor=fig.get_facecolor(),
        edgecolor="none",
    )
    plt.close(fig)
    buf.seek(0)
    return buf.read()
