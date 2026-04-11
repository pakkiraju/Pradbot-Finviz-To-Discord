"""Fetch FinViz Elite full v=152 custom export (all columns, full universe).

Duplicated query string matches Market Metrics ``FINVIZ_USA_FULL_V152_EXPORT`` so this
repo stays self-contained. Used for daily heatmap aggregation (Sector / Industry / Index).
"""

import logging
import os

from fetch_elite import _get_api_key, fetch_csv_export

logger = logging.getLogger(__name__)

# Same as Market Metrics Dashboard ``_USA_FULL_V152_QUERY`` (export.ashx host).
_V152_COLUMNS = (
    "0,1,2,79,3,4,5,129,6,7,8,9,10,11,12,13,73,74,75,14,130,131,147,148,149,15,16,77,17,18,142,19,20,143,21,23,22,132,133,82,78,127,128,144,145,146,24,25,85,26,27,28,29,30,31,84,32,33,34,35,36,37,38,39,40,41,90,91,92,93,94,95,96,97,98,99,42,43,44,45,47,46,138,139,140,48,49,50,51,52,53,54,55,56,57,58,134,125,126,59,68,70,80,83,76,60,61,62,63,64,67,89,69,81,86,87,88,65,66,71,72,141,135,136,137,150,103,100,101,104,102,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120,121,122,123,124,105"
)
_V152_QUERY = f"v=152&ft=3&o=-index&c={_V152_COLUMNS}"

# Same column list for other v=152 Elite exports (e.g. `/earnings` filtered screens).
V152_EXPORT_COLUMNS = _V152_COLUMNS

FINVIZ_V152_EXPORT_URL = "https://elite.finviz.com/export.ashx?" + _V152_QUERY
FINVIZ_V152_SCREENER_URL = "https://elite.finviz.com/screener.ashx?" + _V152_QUERY


def fetch_v152_full_universe(
    *,
    timeout: float | None = None,
) -> list[dict]:
    """Download the full v=152 Elite CSV (all rows FinViz returns in one response).

    Requires ``FINVIZ_API_KEY`` in ``.env``. Returns ``[]`` if unconfigured or on failure.

    Override timeout with env ``FINVIZ_V152_EXPORT_TIMEOUT_SEC`` (default 180).
    """
    if not _get_api_key():
        logger.error("FINVIZ_API_KEY not set; cannot fetch v=152 universe export.")
        return []
    if timeout is None:
        timeout = float(os.environ.get("FINVIZ_V152_EXPORT_TIMEOUT_SEC", "180"))
    rows = fetch_csv_export(FINVIZ_V152_EXPORT_URL, caller="v152_universe", timeout=timeout)
    logger.info("v152 universe export: %d rows", len(rows))
    return rows
