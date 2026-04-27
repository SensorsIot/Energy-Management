"""EV charging mode calculation (FSD 4.5.4).

Modes (via input_select.ev_charging_mode):
  - solar: default, handled by ev_charging.py (this module returns idle)
  - immediate: charge at max power regardless of tariff
  - cheap: charge at max power during cheap tariff windows only

Auto-revert: mode switches back to "solar" after car stops drawing
current for a configurable timeout (default 5 minutes).
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChargingModeResult:
    """Result of charging mode calculation."""

    target_power_w: float       # Power to set on wallbox (0 = wait/inactive)
    charge_status: str          # idle / ready / waiting / charging / error
    status_text: str            # Human-readable status for dashboard
    reason: str                 # Log-level explanation
    revert_to_solar: bool = False  # True when mode should auto-revert to solar


def calculate_charging_mode(
    ev_charging_mode: str,
    is_cheap_tariff: bool,
    wallbox_status: str,
    wallbox_connected: bool,
    wallbox_power_w: float,
    idle_minutes: float,
    auto_reset_timeout_min: float = 5.0,
    max_power_w: float = 11000,
) -> ChargingModeResult:
    """Calculate charging power and status based on the active mode.

    Args:
        ev_charging_mode: Active mode ("solar", "immediate", "cheap")
        is_cheap_tariff: Whether current tariff is cheap
        wallbox_status: OCPP status string (Available, Preparing, Charging,
                        SuspendedEV, SuspendedEVSE, Finishing, Faulted)
        wallbox_connected: Whether wallbox is WebSocket-connected
        wallbox_power_w: Current wallbox charging power in watts
        idle_minutes: Minutes wallbox has been at 0W with Finishing/Available status
        auto_reset_timeout_min: Minutes of idle before auto-revert to solar
        max_power_w: Maximum charging power (3-phase max)

    Returns:
        ChargingModeResult with target power, status, reason, and revert flag

    """
    # Solar mode → handled by ev_charging.py, not here
    if ev_charging_mode not in ("immediate", "cheap"):
        return ChargingModeResult(
            target_power_w=0,
            charge_status="idle",
            status_text="Idle",
            reason="Solar mode — handled by solar excess logic",
        )

    # Error checks (mode active but cannot proceed)
    if not wallbox_connected:
        return ChargingModeResult(
            target_power_w=0,
            charge_status="error",
            status_text="Wallbox offline",
            reason="Wallbox not connected via WebSocket",
        )

    if wallbox_status == "Faulted":
        return ChargingModeResult(
            target_power_w=0,
            charge_status="error",
            status_text="Wallbox fault",
            reason=f"Wallbox status: {wallbox_status}",
        )

    if wallbox_status == "Available":
        return ChargingModeResult(
            target_power_w=0,
            charge_status="error",
            status_text="Car not connected",
            reason="Wallbox Available — no car plugged in",
        )

    # Auto-revert check: car finished charging → revert to solar
    if idle_minutes >= auto_reset_timeout_min and wallbox_status in (
        "Finishing", "SuspendedEV",
    ):
        return ChargingModeResult(
            target_power_w=0,
            charge_status="idle",
            status_text="Charging complete",
            reason=f"Auto-revert: idle {idle_minutes:.0f}min >= {auto_reset_timeout_min:.0f}min",
            revert_to_solar=True,
        )

    # Immediate mode: full power regardless of tariff
    if ev_charging_mode == "immediate":
        return ChargingModeResult(
            target_power_w=max_power_w,
            charge_status="charging" if wallbox_power_w > 0 else "ready",
            status_text=(
                "Charge Now" if wallbox_power_w == 0 else f"Charging at {wallbox_power_w:.0f}W"
            ),
            reason="Immediate mode — full power regardless of tariff",
        )

    # Cheap mode: tariff-dependent
    if is_cheap_tariff:
        return ChargingModeResult(
            target_power_w=max_power_w,
            charge_status="charging" if wallbox_power_w > 0 else "ready",
            status_text=(
                "Cheap tariff — charging"
                if wallbox_power_w > 0
                else "Ready — cheap tariff active"
            ),
            reason="Cheap mode + cheap tariff → full power",
        )

    # Cheap mode armed but waiting for cheap tariff
    return ChargingModeResult(
        target_power_w=0,
        charge_status="waiting",
        status_text="Waiting for cheap tariff",
        reason="Cheap mode armed, waiting for cheap tariff window",
    )
