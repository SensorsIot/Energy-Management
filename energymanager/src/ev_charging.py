"""
EV charging power calculation for opportunistic solar mode.

Uses grid power as ground truth: excess = -grid_power + wallbox_power.
Phase selection per FSD 4.5.4.1:
  >= 4100W → 3-phase (up to 11000W)
  >= 1400W → 1-phase (up to 3700W)
  < 1400W  → pause (0W, transaction stays alive)
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EVChargingResult:
    """Result of EV charging power calculation."""
    target_power_w: float       # Power to set on wallbox (0 = pause)
    available_excess_w: float   # Computed solar excess
    reason: str


def calculate_ev_power(
    grid_power_w: float,
    wallbox_power_w: float,
    min_power_1phase_w: float = 1400,
    max_power_1phase_w: float = 3700,
    min_power_3phase_w: float = 4100,
    max_power_3phase_w: float = 11000,
) -> EVChargingResult:
    """
    Calculate target EV charging power from solar excess.

    Args:
        grid_power_w: Current grid power (positive = import, negative = export)
        wallbox_power_w: Current wallbox draw in watts
        min_power_1phase_w: Minimum 1-phase charging power
        max_power_1phase_w: Maximum 1-phase charging power
        min_power_3phase_w: Minimum 3-phase charging power
        max_power_3phase_w: Maximum 3-phase charging power

    Returns:
        EVChargingResult with target power, excess, and reason
    """
    excess = -grid_power_w + wallbox_power_w

    if excess >= min_power_3phase_w:
        target = min(excess, max_power_3phase_w)
        target = _round_to_step(target)
        return EVChargingResult(
            target_power_w=target,
            available_excess_w=excess,
            reason=f"3-phase {target:.0f}W (excess {excess:.0f}W)",
        )

    if excess >= min_power_1phase_w:
        target = min(excess, max_power_1phase_w)
        target = _round_to_step(target)
        return EVChargingResult(
            target_power_w=target,
            available_excess_w=excess,
            reason=f"1-phase {target:.0f}W (excess {excess:.0f}W)",
        )

    return EVChargingResult(
        target_power_w=0,
        available_excess_w=excess,
        reason=f"Pause: excess {excess:.0f}W < {min_power_1phase_w:.0f}W minimum",
    )


def _round_to_step(value: float, step: float = 100) -> float:
    """Round to nearest step (matches number.wallbox_power_limit step size)."""
    return round(value / step) * step
