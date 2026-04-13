"""FinViz Elite after-hours movers: v=151 screeners in UI; v=152 CSV export for full columns (AH, price)."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlencode

from fetch_elite import _get_api_key, _parse_num, fetch_csv_export
from fetch_v152_universe import V152_EXPORT_COLUMNS
from finviz_earnings import _finviz_thousands_to_shares, _fmt_shares_compact

logger = logging.getLogger(__name__)

_DELAY_SEC = float(os.environ.get("FINVIZ_ELITE_DELAY_SEC", "1.5"))

AH_MOVERS_LIMIT = 5

# Matches user screener: AH ±3%, avg vol >1k (thousands), price >$1, sorted by after-hours change
_AH_UP_F = "ah_change_u3,sh_avgvol_o1000,sh_price_o1"
_AH_DN_F = "ah_change_d3,sh_avgvol_o1000,sh_price_o1"

_SCREENER_BASE = {"v": "151", "ft": "4", "o": "-afterchange"}


def ah_movers_screener_url_up() -> str:
    q = urlencode({**_SCREENER_BASE, "f": _AH_UP_F})
    return f"https://elite.finviz.com/screener.ashx?{q}"


def ah_movers_screener_url_down() -> str:
    q = urlencode({**_SCREENER_BASE, "f": _AH_DN_F})
    return f"https://elite.finviz.com/screener.ashx?{q}"


def _export_url(f: str, *, order: str = "-afterchange") -> str:
    """Same filters as the v=151 screeners; v=152 + full ``c=`` so CSV includes Price and After Hours.

    *order*: FinViz ``o=`` param. Gainers use ``-afterchange`` (largest positive AH first). Losers use
    ``afterchange`` so the most negative AH moves (biggest drops) sort first.
    """
    q = urlencode(
        {
            "v": "152",
            "f": f,
            "ft": "4",
            "o": order,
            "c": V152_EXPORT_COLUMNS,
        }
    )
    return f"https://elite.finviz.com/export.ashx?{q}"


def _cell(raw: dict[str, Any], *names: str) -> str:
    want = {n.lower() for n in names}
    for k, v in raw.items():
        if str(k).strip().lower() in want and v is not None:
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return ""


def _ah_change_cell(raw: dict[str, Any]) -> str:
    for nm in (
        "After Hours Change",
        "Afterhours Change",
        "AH Change",
        "Performance (After Hours)",
        "After Hours %",
        "After-Hours Change",
        "After Hours",
    ):
        v = _cell(raw, nm)
        if v:
            return v
    for k, v in raw.items():
        if v is None:
            continue
        kl = re.sub(r"\s+", " ", str(k).strip().lower())
        if ("after" in kl and "hour" in kl) or "afterhours" in kl.replace(" ", ""):
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
        if kl in ("ah change", "ah %", "ah chg", "extended hours change"):
            s = str(v).strip()
            if s and s not in ("-", "—"):
                return s
    return "—"


def _fmt_price_display(raw: str) -> str:
    n = _parse_num(raw)
    if n is not None:
        return f"${n:,.2f}"
    s = (raw or "").strip()
    return s if s else "—"


def _normalize_row(raw: dict[str, Any]) -> dict[str, str]:
    sym = _cell(raw, "Ticker", "ticker").upper() or "—"
    chg = _cell(raw, "Change") or "—"
    price_raw = _cell(raw, "Price", "price")
    price_disp = _fmt_price_display(price_raw)
    vol_raw = _cell(raw, "Volume", "volume")
    # v=152 Volume: same rules as /inplay and /earnings — usually thousands; bare values ≥1e6 are full shares.
    vs = _finviz_thousands_to_shares(vol_raw or "")
    if vs is not None:
        vol_disp = _fmt_shares_compact(vs)
    else:
        vol_disp = vol_raw or "—"
    ah = _ah_change_cell(raw)
    return {
        "ticker": sym,
        "price": price_disp,
        "change": chg,
        "volume": vol_disp,
        "ah_change": ah,
    }


def _rows_from_csv(raw_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in raw_rows:
        row = _normalize_row(r)
        if row["ticker"] and row["ticker"] != "—":
            out.append(row)
    return out


def fetch_ah_movers_pair() -> tuple[
    tuple[list[dict[str, str]], str],
    tuple[list[dict[str, str]], str],
]:
    """Fetch top AH gainers/losers (up to :data:`AH_MOVERS_LIMIT` each) and screener URLs for links.

    Returns ``((up_rows, up_screener_url), (down_rows, down_screener_url))``.
    Rows are dicts with keys: ticker, price, change, volume, ah_change.
    """
    url_up = _export_url(_AH_UP_F, order="-afterchange")
    url_dn = _export_url(_AH_DN_F, order="afterchange")
    scr_up = ah_movers_screener_url_up()
    scr_dn = ah_movers_screener_url_down()

    if not _get_api_key():
        return (([], scr_up), ([], scr_dn))

    raw_up = fetch_csv_export(url_up, caller="ah_movers_up", timeout=90)
    time.sleep(_DELAY_SEC)
    raw_dn = fetch_csv_export(url_dn, caller="ah_movers_down", timeout=90)

    if raw_up and _ah_change_cell(raw_up[0]) == "—":
        logger.info("ah_movers: AH column not matched; sample keys: %s", list(raw_up[0].keys())[:50])

    up = _rows_from_csv(raw_up)[:AH_MOVERS_LIMIT]
    down = _rows_from_csv(raw_dn)[:AH_MOVERS_LIMIT]

    logger.info("ah_movers: up=%d down=%d rows", len(up), len(down))
    return ((up, scr_up), (down, scr_dn))
