"""EV-based position sizing and grading (pure math, no Discord dependency).

Binary-outcome model:
  Win  → gain R per share (target hit)
  Lose → lose L per share (stop hit)

Kelly criterion for binary payoffs:
  b = R / L
  f* = (p * b - q) / b   where p = win prob, q = 1 - p

Conservative sizing uses fractional Kelly (default ¼) capped per trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

FRACTIONAL_KELLY: float = 0.25
"""Fraction of full Kelly to use (¼ Kelly is conservative)."""

MAX_SINGLE_TRADE_FRACTION: float = 0.50
"""Hard cap: one trade can never risk more than this share of the daily budget."""

GRADE_EVR_THRESHOLDS: list[tuple[str, float]] = [
    ("A+", 0.25),
    ("A",  0.18),
    ("A-", 0.12),
    ("B+", 0.08),
    ("B",  0.05),
    ("B-", 0.02),
    ("C",  0.00),
]
"""(grade, min_evr) pairs in descending order. Anything below the last threshold is D."""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class EVResult:
    side: str           # "long" or "short"
    entry: float
    target: float
    stop: float
    probability: float  # 0-100
    daily_risk: float   # USD

    reward: float       # R per share
    risk: float         # L per share (always positive)
    b: float            # R / L (payoff ratio)
    ev_per_share: float
    evr: float          # EV / L
    f_kelly: float      # full Kelly fraction
    f_trade: float      # fractional Kelly (capped)
    suggested_risk: float  # USD risk for this trade
    shares: int         # floor(suggested_risk / L)
    grade: str


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class SizingError(Exception):
    """Raised for invalid user inputs (message is user-facing)."""


def _validate(side: str, entry: float, target: float, stop: float,
              probability: float, daily_risk: float) -> None:
    if side not in ("long", "short"):
        raise SizingError("Side must be **long** or **short**.")
    if entry <= 0 or target <= 0 or stop <= 0:
        raise SizingError("Entry, target, and stop must all be positive.")
    if not (0 < probability < 100):
        raise SizingError("Probability must be between 0 and 100 (exclusive).")
    if daily_risk <= 0:
        raise SizingError("Daily risk budget must be a positive dollar amount.")

    if side == "long":
        if target <= entry:
            raise SizingError("For a **long**, target must be above entry.")
        if stop >= entry:
            raise SizingError("For a **long**, stop must be below entry.")
    else:
        if target >= entry:
            raise SizingError("For a **short**, target must be below entry.")
        if stop <= entry:
            raise SizingError("For a **short**, stop must be above entry.")


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def grade_from_evr(evr: float) -> str:
    for grade, threshold in GRADE_EVR_THRESHOLDS:
        if evr >= threshold:
            return grade
    return "D"


def compute(
    side: str,
    entry: float,
    target: float,
    stop: float,
    probability: float,
    daily_risk: float,
) -> EVResult:
    """Run full EV / Kelly / grade computation. Raises SizingError on bad inputs."""
    side = side.lower().strip()
    _validate(side, entry, target, stop, probability, daily_risk)

    p = probability / 100.0
    q = 1.0 - p

    if side == "long":
        R = target - entry
        L = entry - stop
    else:
        R = entry - target
        L = stop - entry

    b = R / L
    ev = p * R - q * L
    evr = ev / L

    f_kelly = (p * b - q) / b if b > 0 else 0.0

    f_trade = max(0.0, f_kelly) * FRACTIONAL_KELLY
    f_trade = min(f_trade, MAX_SINGLE_TRADE_FRACTION)

    suggested = daily_risk * f_trade
    shares = math.floor(suggested / L) if L > 0 and suggested > 0 else 0

    return EVResult(
        side=side,
        entry=entry,
        target=target,
        stop=stop,
        probability=probability,
        daily_risk=daily_risk,
        reward=R,
        risk=L,
        b=b,
        ev_per_share=ev,
        evr=evr,
        f_kelly=f_kelly,
        f_trade=f_trade,
        suggested_risk=round(suggested, 2),
        shares=shares,
        grade=grade_from_evr(evr),
    )
