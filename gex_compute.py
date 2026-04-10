"""Compute Gamma Exposure (GEX) metrics from options-chain data.

Dealer GEX convention used here:
  - Dealers are assumed short calls and long puts (standard market-maker
    hedging assumption).
  - Call gamma contributes *positive* GEX (dealer must buy shares as price
    rises past the strike).
  - Put gamma contributes *negative* GEX (dealer must sell shares as price
    falls past the strike).
  - Per-strike GEX = gamma * OI * 100  (100 shares per contract)
    with sign: +1 for calls, -1 for puts.

When gamma values are not available in the CSV the module falls back to
open-interest walls and volume concentration — clearly labeled as OI-based,
not true GEX.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from finviz_options import OptionsRow

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100


@dataclass
class StrikeGex:
    """Aggregated GEX at a single strike price."""
    strike: float
    call_gex: float = 0.0
    put_gex: float = 0.0
    call_oi: int = 0
    put_oi: int = 0
    call_vol: int = 0
    put_vol: int = 0

    @property
    def net_gex(self) -> float:
        return self.call_gex + self.put_gex

    @property
    def total_oi(self) -> int:
        return self.call_oi + self.put_oi


@dataclass
class GexSummary:
    """Full GEX analysis result for one symbol/expiry."""
    symbol: str
    expiry: str
    has_gamma: bool
    total_net_gex: float
    call_wall: float | None       # strike with largest positive GEX (or call OI)
    call_wall_value: float         # the GEX (or OI) value at that strike
    put_wall: float | None        # strike with largest negative GEX (or put OI)
    put_wall_value: float          # the GEX (or OI) value at that strike
    gamma_flip: float | None      # strike where cumulative net GEX crosses zero
    top_strikes: list[StrikeGex]  # top N strikes by |net_gex| or total_oi
    total_call_oi: int = 0
    total_put_oi: int = 0
    put_call_ratio: float | None = None


def _aggregate_strikes(rows: list[OptionsRow]) -> dict[float, StrikeGex]:
    """Group options rows by strike and accumulate GEX / OI / volume."""
    strikes: dict[float, StrikeGex] = defaultdict(lambda: StrikeGex(strike=0.0))

    for r in rows:
        sg = strikes[r.strike]
        sg.strike = r.strike
        gex = (r.gamma or 0.0) * r.oi * CONTRACT_MULTIPLIER

        if r.opt_type == "call":
            sg.call_gex += gex
            sg.call_oi += r.oi
            sg.call_vol += r.volume
        else:
            sg.put_gex -= gex     # negative sign for puts (dealer convention)
            sg.put_oi += r.oi
            sg.put_vol += r.volume

    return dict(strikes)


def _find_gamma_flip(sorted_strikes: list[StrikeGex]) -> float | None:
    """Find the strike where cumulative net GEX crosses zero.

    Walks strikes from lowest to highest, accumulating net GEX. When the
    running total changes sign, interpolates linearly between the two
    bracketing strikes.
    """
    if not sorted_strikes:
        return None

    cumulative = 0.0
    prev_strike = sorted_strikes[0].strike
    prev_cum = 0.0

    for sg in sorted_strikes:
        cumulative += sg.net_gex
        if prev_cum != 0.0 and cumulative != 0.0:
            if (prev_cum > 0) != (cumulative > 0):
                # Linear interpolation between the two strikes
                frac = abs(prev_cum) / (abs(prev_cum) + abs(cumulative))
                return round(prev_strike + frac * (sg.strike - prev_strike), 2)
        prev_strike = sg.strike
        prev_cum = cumulative

    return None


def compute_gex(symbol: str, expiry: str, rows: list[OptionsRow]) -> GexSummary | None:
    """Compute GEX metrics from parsed options rows.

    Returns None only if *rows* is empty. When gamma data is missing the
    summary is still produced using OI/volume with ``has_gamma=False``.
    """
    if not rows:
        return None

    has_gamma = any(r.gamma is not None and r.gamma != 0.0 for r in rows)
    strike_map = _aggregate_strikes(rows)
    sorted_strikes = sorted(strike_map.values(), key=lambda s: s.strike)

    total_call_oi = sum(s.call_oi for s in sorted_strikes)
    total_put_oi = sum(s.put_oi for s in sorted_strikes)
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None

    if has_gamma:
        total_net = sum(s.net_gex for s in sorted_strikes)

        # Call wall = strike with max positive net GEX
        call_candidates = [s for s in sorted_strikes if s.call_gex > 0]
        if call_candidates:
            cw = max(call_candidates, key=lambda s: s.call_gex)
            call_wall, call_wall_val = cw.strike, cw.call_gex
        else:
            call_wall, call_wall_val = None, 0.0

        # Put wall = strike with most negative net GEX (largest magnitude)
        put_candidates = [s for s in sorted_strikes if s.put_gex < 0]
        if put_candidates:
            pw = min(put_candidates, key=lambda s: s.put_gex)
            put_wall, put_wall_val = pw.strike, pw.put_gex
        else:
            put_wall, put_wall_val = None, 0.0

        gamma_flip = _find_gamma_flip(sorted_strikes)

        top = sorted(sorted_strikes, key=lambda s: abs(s.net_gex), reverse=True)[:10]
    else:
        total_net = 0.0

        # Fallback: OI-based walls
        call_candidates = [s for s in sorted_strikes if s.call_oi > 0]
        if call_candidates:
            cw = max(call_candidates, key=lambda s: s.call_oi)
            call_wall, call_wall_val = cw.strike, float(cw.call_oi)
        else:
            call_wall, call_wall_val = None, 0.0

        put_candidates = [s for s in sorted_strikes if s.put_oi > 0]
        if put_candidates:
            pw = max(put_candidates, key=lambda s: s.put_oi)
            put_wall, put_wall_val = pw.strike, float(pw.put_oi)
        else:
            put_wall, put_wall_val = None, 0.0

        gamma_flip = None

        top = sorted(sorted_strikes, key=lambda s: s.total_oi, reverse=True)[:10]

    return GexSummary(
        symbol=symbol,
        expiry=expiry,
        has_gamma=has_gamma,
        total_net_gex=total_net,
        call_wall=call_wall,
        call_wall_value=call_wall_val,
        put_wall=put_wall,
        put_wall_value=put_wall_val,
        gamma_flip=gamma_flip,
        top_strikes=top,
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        put_call_ratio=pcr,
    )
