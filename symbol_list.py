"""Comma-separated ticker strings for Discord copy/paste (TradingView, etc.)."""

from __future__ import annotations

from typing import Any, Iterable

_SKIP_TICKERS = frozenset({"", "—", "-", "?", "TBA", "N/A"})


def format_tickers_csv(rows: Iterable[dict[str, Any]], *, key: str = "ticker") -> str:
    """Join ``key`` from each row in order (FinViz-normalized rows use ``ticker``)."""
    parts: list[str] = []
    for r in rows:
        t = (r.get(key) or "").strip().upper()
        if not t or t in _SKIP_TICKERS:
            continue
        parts.append(t)
    return ",".join(parts)
