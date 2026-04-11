"""Aggregate FinViz v=152 CSV rows into group-level mean daily change % (pandas)."""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def industry_is_exchange_traded_fund(ind: pd.Series) -> pd.Series:
    """True where **Industry** is *Exchange Traded Fund(s)*, including Finviz typo *Exchage*.

    Used for ``is_etf`` industry signal — excludes closed-end and other fund labels.
    """
    s = ind.fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    return s.str.contains(
        r"(?i)(?:exchange|exchage)\s+traded\s+funds?",
        na=False,
        regex=True,
    )


def _find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    """Map canonical names to CSV headers (case-insensitive; strip header whitespace)."""
    for name in candidates:
        if name in df.columns:
            return name
    cols = {c.lower(): c for c in df.columns}
    stripped_map = {str(c).strip().lower(): c for c in df.columns}
    for name in candidates:
        low = name.lower()
        if low in cols:
            return cols[low]
        sl = name.strip().lower()
        if sl in stripped_map:
            return stripped_map[sl]
    return None


def _parse_change_pct(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s in ("-", "—", "nan"):
        return None
    s = s.replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        m = re.match(r"^([\d.-]+)", s)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    """Normalize raw CSV dict rows into a DataFrame with canonical columns."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    tcol = _find_col(df, "Ticker", "ticker")
    scol = _find_col(df, "Sector", "sector")
    icol = _find_col(df, "Industry", "industry")
    idxcol = _find_col(df, "Index", "index")
    chcol = _find_col(df, "Change", "change", "Change %")
    if not chcol:
        logger.error("Could not find Change column; headers: %s", list(df.columns)[:30])
        return pd.DataFrame()
    out = pd.DataFrame()
    out["ticker"] = df[tcol].astype(str).str.strip() if tcol else ""
    out["sector"] = df[scol].astype(str).str.strip() if scol else ""
    out["industry"] = df[icol].astype(str).str.strip() if icol else ""
    out["index_name"] = df[idxcol].astype(str).str.strip() if idxcol else ""
    out["change_pct"] = df[chcol].map(_parse_change_pct)
    out = out[out["change_pct"].notna()]
    out = out[out["ticker"].str.len() > 0]
    # Drop empty group labels for aggregation views
    return out


def _parse_market_cap_usd(val: Any) -> float | None:
    """Parse FinViz Market Cap cell (e.g. ``1.2B``, ``450M``) to USD float."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace(",", "").replace("$", "")
    if not s or s in ("-", "—", "nan"):
        return None
    m = re.match(r"([\d.-]+)\s*([KMBT])?", s, re.I)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    v = float(m.group(1))
    suf = (m.group(2) or "").upper()
    if suf == "K":
        v *= 1e3
    elif suf == "M":
        v *= 1e6
    elif suf == "B":
        v *= 1e9
    elif suf == "T":
        v *= 1e12
    return v


def _normalize_df_columns(df: pd.DataFrame) -> None:
    """Strip BOM / stray whitespace from CSV headers (breaks _find_col for Asset Type, etc.)."""
    df.columns = pd.Index(str(c).replace("\ufeff", "").strip() for c in df.columns)


def _resolve_asset_type_column(df: pd.DataFrame) -> str | None:
    """Locate Finviz Asset Type column; fall back to scanning for ETF/STOCK value distribution."""
    acol = _find_col(df, "Asset Type", "asset_type")
    n = len(df)
    if n == 0:
        return acol

    def _etf_count(col: str | None) -> int:
        if not col or col not in df.columns:
            return 0
        s = df[col].astype(str).str.strip().str.upper()
        return int((s == "ETF").sum())

    if acol is not None and _etf_count(acol) >= max(30, n // 500):
        return acol

    best_col, best_n = None, 0
    for col in df.columns:
        s = df[col].astype(str).str.strip().str.upper()
        c = int((s == "ETF").sum())
        if c > best_n:
            best_col, best_n = col, c

    if best_n >= max(50, n // 200) and best_col is not None:
        logger.warning(
            "treemap: Asset Type from header had %d ETF rows; using column %r (%d ETF rows)",
            _etf_count(acol),
            best_col,
            best_n,
        )
        return best_col

    if acol is None and best_n > 0:
        logger.warning("treemap: no 'Asset Type' header; using column %r (%d ETF rows)", best_col, best_n)
        return best_col

    return acol


def rows_to_treemap_dataframe(rows: list[dict]) -> pd.DataFrame:
    """Parse v=152 rows for stock treemap: change %, market cap, sector, industry, index, ETF/theme."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    _normalize_df_columns(df)
    tcol = _find_col(df, "Ticker", "ticker")
    scol = _find_col(df, "Sector", "sector")
    icol = _find_col(df, "Industry", "industry", "Industry ")
    idxcol = _find_col(df, "Index", "index")
    chcol = _find_col(df, "Change", "change", "Change %")
    mcol = _find_col(df, "Market Cap", "market_cap")
    acol = _resolve_asset_type_column(df)
    etf_tcol = _find_col(df, "ETF Type", "etf_type", "ETF type")
    theme_col = _find_col(df, "Sector/Theme", "sector_theme")
    if not chcol:
        logger.error("treemap: no Change column; headers: %s", list(df.columns)[:30])
        return pd.DataFrame()

    out = pd.DataFrame()
    out["ticker"] = df[tcol].astype(str).str.strip() if tcol else ""
    out["sector"] = df[scol].astype(str).str.strip() if scol else ""
    out["industry"] = df[icol].astype(str).str.strip() if icol else ""
    out["index_name"] = df[idxcol].astype(str).str.strip() if idxcol else ""
    out["change_pct"] = df[chcol].map(_parse_change_pct)
    out["market_cap_usd"] = df[mcol].map(_parse_market_cap_usd) if mcol else None
    out["market_cap_usd"] = pd.to_numeric(out["market_cap_usd"], errors="coerce")

    out["_asset_type"] = df[acol].astype(str).str.strip() if acol else ""
    out["_etf_type"] = df[etf_tcol].astype(str).str.strip() if etf_tcol else ""

    out["sector_theme"] = df[theme_col].astype(str).str.strip() if theme_col else ""

    if not icol:
        logger.warning(
            "treemap: no Industry column in v=152 export (headers sample): %s",
            list(df.columns)[:45],
        )

    out = out[out["change_pct"].notna()]
    out = out[out["ticker"].str.len() > 0]
    out = out[out["market_cap_usd"].notna() & (out["market_cap_usd"] > 0)]

    ind = out["industry"].fillna("")
    at = out["_asset_type"].fillna("").astype(str).str.strip()
    etf_t = out["_etf_type"].fillna("").astype(str).str.strip()

    ind_etf = industry_is_exchange_traded_fund(ind)
    at_upper = at.str.upper()
    # Official screener column: Stock vs ETF (exact).
    at_etf = at_upper.eq("ETF") | at_upper.eq("ETFS")
    etf_type_nonempty = etf_t.str.len() > 0
    out["is_etf"] = at_etf | etf_type_nonempty | ind_etf
    out = out.drop(columns=["_asset_type", "_etf_type"])

    out["sector"] = out["sector"].replace("", "—").fillna("—")
    out["industry"] = out["industry"].replace("", "—").fillna("—")

    out["market_cap_usd"] = out["market_cap_usd"].clip(lower=1e5)
    return out.reset_index(drop=True)


def aggregate_mean_change(
    df: pd.DataFrame,
    by: str,
    *,
    min_names: int = 1,
    top_n: int | None = None,
) -> pd.DataFrame:
    """Group by column *by* and compute mean change % and count.

    *min_names* drops groups with fewer than that many tickers (for noisy Index).
    *top_n* keeps the N groups with largest absolute mean change (after filtering).
    """
    if df.empty or by not in df.columns:
        return pd.DataFrame(columns=["label", "mean_change", "count"])
    g = (
        df.groupby(by, dropna=False)["change_pct"]
        .agg(["mean", "count"])
        .reset_index()
        .rename(columns={by: "label", "mean": "mean_change", "count": "count"})
    )
    g = g[g["label"].notna()]
    lab = g["label"].astype(str).str.strip()
    g = g.assign(_lab=lab)
    g = g[~g["_lab"].isin(("", "nan", "-", "—"))].drop(columns=["_lab"])
    g = g[g["count"] >= min_names]
    if top_n is not None and len(g) > top_n:
        idx = g["mean_change"].abs().nlargest(top_n).index
        g = g.loc[idx].sort_values("mean_change", ascending=False)
    else:
        g = g.sort_values("mean_change", ascending=False)
    return g.reset_index(drop=True)


def build_all_aggregates(
    df: pd.DataFrame,
    *,
    industry_top_n: int = 40,
    index_min_names: int = 3,
    index_top_n: int = 35,
) -> dict[str, pd.DataFrame]:
    """Return labeled DataFrames for sector, industry, index, and ETF-only sector rollup."""
    out: dict[str, pd.DataFrame] = {}
    if df.empty:
        return {k: pd.DataFrame() for k in ("sector", "industry", "index", "etf_sector")}

    d = df.copy()
    d["sector"] = d["sector"].replace("", pd.NA).fillna("—")
    d["industry"] = d["industry"].replace("", pd.NA).fillna("—")
    d["index_name"] = d["index_name"].replace("", pd.NA).fillna("—")

    out["sector"] = aggregate_mean_change(
        d[d["sector"] != "—"], "sector", min_names=1, top_n=None
    )

    out["industry"] = aggregate_mean_change(
        d[d["industry"] != "—"], "industry", min_names=1, top_n=industry_top_n
    )

    idx_df = d[d["index_name"] != "—"]
    out["index"] = aggregate_mean_change(
        idx_df, "index_name", min_names=index_min_names, top_n=index_top_n
    )

    etf_mask = d["industry"].str.contains("ETF", case=False, na=False) | d["sector"].str.contains(
        "ETF", case=False, na=False
    )
    etf_df = d[etf_mask]
    if etf_df.empty:
        out["etf_sector"] = pd.DataFrame(columns=["label", "mean_change", "count"])
    else:
        out["etf_sector"] = aggregate_mean_change(etf_df, "sector", min_names=1, top_n=25)

    return out
