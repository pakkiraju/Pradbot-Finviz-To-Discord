"""EV-based position sizing and grading (pure math, no Discord dependency).

Binary-outcome model:
  Win  → gain R per share (target hit)
  Lose → lose L per share (stop hit)

Kelly criterion for binary payoffs:
  b = R / L
  f* = (p * b - q) / b   where p = win prob, q = 1 - p

Sizing uses **fractional Kelly** (default **½ Kelly**) of the full Kelly fraction, then a per-trade cap.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Default when callers omit an explicit fraction (e.g. `/evsize` default = half Kelly).
DEFAULT_FRACTIONAL_KELLY: float = 0.5

ALLOWED_FRACTIONAL_KELLY: frozenset[float] = frozenset({0.25, 0.5, 1.0})
"""Supported fractional Kelly multipliers (¼, ½, full)."""

MAX_SINGLE_TRADE_FRACTION: float = 0.50
"""Hard cap: one trade can never risk more than this share of the daily budget."""

# Default (grade, min EV/R) pairs — EV/R = EV per share ÷ risk per share (L).
# Stricter than earlier builds so “A+” is rare: paper math often overstates realized edge.
DEFAULT_GRADE_EVR_THRESHOLDS: list[tuple[str, float]] = [
    ("A+", 0.38),
    ("A", 0.28),
    ("A-", 0.20),
    ("B+", 0.14),
    ("B", 0.09),
    ("B-", 0.04),
    ("C", 0.00),
]
"""Override any tier with env, e.g. EVSIZE_GRADE_A_PLUS_MIN_EVR=0.45 (see get_grade_evr_thresholds)."""


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_grade_evr_thresholds() -> list[tuple[str, float]]:
    """Return (grade, min_evr) with optional per-tier env overrides."""
    env_names = {
        "A+": "EVSIZE_GRADE_A_PLUS_MIN_EVR",
        "A": "EVSIZE_GRADE_A_MIN_EVR",
        "A-": "EVSIZE_GRADE_A_MINUS_MIN_EVR",
        "B+": "EVSIZE_GRADE_B_PLUS_MIN_EVR",
        "B": "EVSIZE_GRADE_B_MIN_EVR",
        "B-": "EVSIZE_GRADE_B_MINUS_MIN_EVR",
        "C": "EVSIZE_GRADE_C_MIN_EVR",
    }
    out: list[tuple[str, float]] = []
    for grade, default_min in DEFAULT_GRADE_EVR_THRESHOLDS:
        env_key = env_names.get(grade)
        v = _env_float(env_key, default_min) if env_key else default_min
        out.append((grade, v))
    return out


def get_grade_conservatism() -> float:
    """Multiply EV/R before letter grade only (1.0 = off).

    Use 0.85–0.95 to reflect fear, partial size, targets not playing out, or optimistic p.
    Env: EVSIZE_GRADE_CONSERVATISM
    """
    v = _env_float("EVSIZE_GRADE_CONSERVATISM", 1.0)
    if v <= 0:
        return 1.0
    return min(v, 2.0)


# Back-compat name for imports/tests
GRADE_EVR_THRESHOLDS = DEFAULT_GRADE_EVR_THRESHOLDS


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
    f_trade: float      # fraction of daily budget after fractional Kelly (capped)
    kelly_fraction: float  # multiplier on full Kelly (e.g. 0.25, 0.5, 1.0)
    suggested_risk: float  # USD risk for this trade
    shares: int         # floor(suggested_risk / L)
    grade: str
    evr_for_grade: float  # EV/R after conservatism; used for letter only
    grade_conservatism: float  # 1.0 = grade from raw EV/R


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

def grade_from_evr(evr: float, thresholds: list[tuple[str, float]] | None = None) -> str:
    th = thresholds if thresholds is not None else get_grade_evr_thresholds()
    for grade, threshold in th:
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
    *,
    fractional_kelly: float = DEFAULT_FRACTIONAL_KELLY,
) -> EVResult:
    """Run full EV / Kelly / grade computation. Raises SizingError on bad inputs.

    ``fractional_kelly`` is the fraction of **full** Kelly to use (¼, ½, or 1.0).
    """
    side = side.lower().strip()
    _validate(side, entry, target, stop, probability, daily_risk)

    fk = float(fractional_kelly)
    if fk not in ALLOWED_FRACTIONAL_KELLY:
        raise SizingError(
            "Fractional Kelly must be one of: **¼**, **½**, or **full** (use the command option)."
        )

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

    conserv = get_grade_conservatism()
    evr_for_grade = evr * conserv
    th = get_grade_evr_thresholds()
    letter = grade_from_evr(evr_for_grade, th)

    f_kelly = (p * b - q) / b if b > 0 else 0.0

    f_trade = max(0.0, f_kelly) * fk
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
        kelly_fraction=fk,
        suggested_risk=round(suggested, 2),
        shares=shares,
        grade=letter,
        evr_for_grade=evr_for_grade,
        grade_conservatism=conserv,
    )
