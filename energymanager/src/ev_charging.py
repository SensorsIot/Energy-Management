"""
EV charging power calculation for opportunistic solar mode.

Clamps excess power to wallbox min/max limits:
  excess >= min_power_w → charge at min(excess, max_power_w)
  excess <  min_power_w → pause (0W, transaction stays alive)

Phase-gap handling:
  The wallbox has a dead zone between 1φ max (3680 W) and 3φ min (4140 W).
  If the target lands in this gap it is snapped to the nearest achievable power.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_PHASE_GAP_LO = 3680  # single-phase maximum (W) — 16A × 230V
_PHASE_GAP_HI = 4140  # three-phase minimum (W)


@dataclass
class EVChargingResult:
    """Result of EV charging power calculation."""
    target_power_w: float       # Power to set on wallbox (0 = pause)
    available_excess_w: float   # Computed solar excess
    reason: str


def resolve_phase_gap(target_w: float, battery_full: bool) -> float:
    """Snap target out of the 1φ/3φ dead zone (3680–4140 W).

    Battery not full → prefer 1φ (3680) so surplus charges battery.
    Battery full     → prefer 3φ (4140) to use power in car.
    """
    if _PHASE_GAP_LO < target_w < _PHASE_GAP_HI:
        return _PHASE_GAP_HI if battery_full else _PHASE_GAP_LO
    return target_w


def calculate_ev_power(
    excess_w: float,
    min_power_w: float = 1400,
    max_power_w: float = 11000,
    battery_full: bool = False,
) -> EVChargingResult:
    """
    Calculate target EV charging power from solar excess.

    Args:
        excess_w: Available solar excess in watts
        min_power_w: Minimum wallbox power (below this → pause)
        max_power_w: Maximum wallbox power (clamp ceiling)

    Returns:
        EVChargingResult with target power, excess, and reason
    """
    if excess_w >= min_power_w:
        target = min(excess_w, max_power_w)
        target = _round_to_step(target)
        target = resolve_phase_gap(target, battery_full)
        return EVChargingResult(
            target_power_w=target,
            available_excess_w=excess_w,
            reason=f"Charging {target:.0f}W (excess {excess_w:.0f}W)",
        )

    return EVChargingResult(
        target_power_w=0,
        available_excess_w=excess_w,
        reason=f"Pause: excess {excess_w:.0f}W < {min_power_w:.0f}W minimum",
    )


def snap_to_amp_step(
    surplus_w: float,
    min_amps: int,
    max_amps: int,
    phases: int,
) -> int:
    """Snap surplus power to the next wallbox amp level.

    Returns amps clamped to [min_amps, max_amps].
    """
    step_w = 230 * phases
    raw_amps = int(-(-surplus_w // step_w))  # ceil without import
    return max(min_amps, min(raw_amps, max_amps))


def _round_to_step(value: float, step: float = 100) -> float:
    """Round to nearest step (matches number.wallbox_power_limit step size)."""
    return round(value / step) * step
