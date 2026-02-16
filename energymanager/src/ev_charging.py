"""
EV charging power calculation for opportunistic solar mode.

Clamps excess power to wallbox min/max limits:
  excess >= min_power_w → charge at min(excess, max_power_w)
  excess <  min_power_w → pause (0W, transaction stays alive)
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
    excess_w: float,
    min_power_w: float = 1400,
    max_power_w: float = 11000,
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


def _round_to_step(value: float, step: float = 100) -> float:
    """Round to nearest step (matches number.wallbox_power_limit step size)."""
    return round(value / step) * step
