"""
EV goal mode charging calculation (FSD 4.5.4.2).

Goal mode charges at max power during cheap tariff windows.
Two modes:
  - ev_goal_charge: arms charging for next cheap tariff window
  - ev_charge_now: override, charge immediately regardless of tariff

Auto-reset: both buttons reset after car stops drawing current for
a configurable timeout (default 5 minutes).
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GoalModeResult:
    """Result of goal mode calculation."""
    target_power_w: float       # Power to set on wallbox (0 = wait/inactive)
    charge_status: str          # idle / ready / waiting / charging / error
    status_text: str            # Human-readable status for dashboard
    reason: str                 # Log-level explanation


def calculate_goal_mode(
    ev_goal_charge: bool,
    ev_charge_now: bool,
    is_cheap_tariff: bool,
    wallbox_status: str,
    wallbox_connected: bool,
    wallbox_power_w: float,
    idle_minutes: float,
    auto_reset_timeout_min: float = 5.0,
    goal_max_power_w: float = 11000,
) -> GoalModeResult:
    """
    Calculate goal mode charging power and status.

    Args:
        ev_goal_charge: "Charge Car" button state
        ev_charge_now: "Charge Now" override button state
        is_cheap_tariff: Whether current tariff is cheap
        wallbox_status: OCPP status string (Available, Preparing, Charging,
                        SuspendedEV, SuspendedEVSE, Finishing, Faulted)
        wallbox_connected: Whether wallbox is WebSocket-connected
        wallbox_power_w: Current wallbox charging power in watts
        idle_minutes: Minutes wallbox has been at 0W with Finishing/Available status
        auto_reset_timeout_min: Minutes of idle before auto-reset
        goal_max_power_w: Maximum charging power (3-phase max)

    Returns:
        GoalModeResult with target power, status, and reason
    """
    # Neither button active → idle
    if not ev_goal_charge and not ev_charge_now:
        return GoalModeResult(
            target_power_w=0,
            charge_status="idle",
            status_text="Idle",
            reason="Goal mode inactive",
        )

    # Error checks (armed but cannot proceed)
    if not wallbox_connected:
        return GoalModeResult(
            target_power_w=0,
            charge_status="error",
            status_text="Wallbox offline",
            reason="Wallbox not connected via WebSocket",
        )

    if wallbox_status == "Faulted":
        return GoalModeResult(
            target_power_w=0,
            charge_status="error",
            status_text="Wallbox fault",
            reason=f"Wallbox status: {wallbox_status}",
        )

    if wallbox_status == "Available":
        return GoalModeResult(
            target_power_w=0,
            charge_status="error",
            status_text="Car not connected",
            reason="Wallbox Available — no car plugged in",
        )

    # Auto-reset check: car finished charging
    if idle_minutes >= auto_reset_timeout_min and wallbox_status in (
        "Finishing", "SuspendedEV",
    ):
        return GoalModeResult(
            target_power_w=0,
            charge_status="idle",
            status_text="Charging complete",
            reason=f"Auto-reset: idle {idle_minutes:.0f}min >= {auto_reset_timeout_min:.0f}min",
        )

    # Actively charging
    if wallbox_power_w > 0 and wallbox_status == "Charging":
        return GoalModeResult(
            target_power_w=goal_max_power_w,
            charge_status="charging",
            status_text=f"Charging at {wallbox_power_w:.0f}W",
            reason=f"Active charging at {wallbox_power_w:.0f}W",
        )

    # Charge Now: immediate override
    if ev_charge_now:
        return GoalModeResult(
            target_power_w=goal_max_power_w,
            charge_status="charging" if wallbox_power_w > 0 else "ready",
            status_text="Charge Now" if wallbox_power_w == 0 else f"Charging at {wallbox_power_w:.0f}W",
            reason="Charge Now override — full power regardless of tariff",
        )

    # Goal charge: tariff-dependent
    if is_cheap_tariff:
        return GoalModeResult(
            target_power_w=goal_max_power_w,
            charge_status="charging" if wallbox_power_w > 0 else "ready",
            status_text="Cheap tariff — charging" if wallbox_power_w > 0 else "Ready — cheap tariff active",
            reason="Goal charge + cheap tariff → full power",
        )

    # Goal charge armed but waiting for cheap tariff
    return GoalModeResult(
        target_power_w=0,
        charge_status="waiting",
        status_text="Waiting for cheap tariff",
        reason="Goal charge armed, waiting for cheap tariff window",
    )
