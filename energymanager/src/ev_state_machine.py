"""EV charging state machine — 4-state design.

States:
  1. IDLE            — no EV charging, SUN2000 has full control
  2. SOLAR           — variable power from solar excess
  3. CHEAP           — max during cheap tariff, 0 during expensive
  4. IMMEDIATE       — immediate mode, max power regardless

Infrastructure concerns (faults, disconnects) are handled by the OCPP
server.  This state machine only makes charging decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class EVState(str, Enum):
    """EV charging states (str so it works as an HA sensor value)."""

    IDLE = "idle"
    SOLAR = "solar"
    CHEAP = "cheap"
    IMMEDIATE = "immediate"


# ---------------------------------------------------------------------------
# Input / Output dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EVInputs:
    """All inputs needed for one step() call."""

    wallbox_available: bool
    wallbox_power_w: float
    wallbox_status: str               # for OCPP status logging only
    wallbox_idle: bool                 # wallbox idle >= timeout (car finished)
    battery_soc: float
    charging_mode: str                # "solar" / "immediate" / "cheap"
    is_cheap_tariff: bool
    grid_power_w: float
    surplus_power_w: float             # solar surplus (PV - house load) (W)
    pv_power_w: float                 # current PV production (W)
    load_power_w: float               # current household load (W)
    min_power_w: float                # default 1400W
    manual_power_w: float                # default 11000W
    ev_charging_power_w: float = 0.0   # pre-computed charging power (FSD 4.5.6)


@dataclass
class EVOutput:
    """Decision produced by step()."""

    state: EVState
    target_power_w: float
    reason: str


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class EVStateMachine:
    """4-state EV charging state machine.

    Call step() each cycle with current inputs.  Returns EVOutput with
    the decided state and target power.
    """

    def __init__(self) -> None:
        self.state: EVState = EVState.IDLE

    def step(self, inputs: EVInputs) -> EVOutput:
        """One cycle: evaluate transitions from current state, return output."""
        handler = _DISPATCH[self.state]
        return handler(self, inputs)

    # -- helpers --

    def _set_state(self, new: EVState) -> None:
        self.state = new

    # -------------------------------------------------------------------
    # IDLE
    # -------------------------------------------------------------------

    def _step_idle(self, i: EVInputs) -> EVOutput:
        # N1: immediate mode
        if i.charging_mode == "immediate" and i.wallbox_available:
            self._set_state(EVState.IMMEDIATE)
            return EVOutput(EVState.IMMEDIATE, i.manual_power_w,
                            "Immediate mode — charge at max power")

        # N2: cheap mode
        if i.charging_mode == "cheap" and i.wallbox_available:
            self._set_state(EVState.CHEAP)
            power = i.manual_power_w if i.is_cheap_tariff else 0
            reason = ("Cheap mode — cheap tariff active, charge at max power"
                      if i.is_cheap_tariff
                      else "Cheap mode — waiting for cheap tariff")
            return EVOutput(EVState.CHEAP, power, reason)

        # N3: solar mode — uses pre-computed ev_charging_power_w
        if i.charging_mode == "solar" and i.wallbox_available:
            if i.ev_charging_power_w > 0:
                self._set_state(EVState.SOLAR)
                return EVOutput(EVState.SOLAR, i.ev_charging_power_w,
                                f"Solar charging {i.ev_charging_power_w:.0f}W")

        # Stay IDLE
        return EVOutput(EVState.IDLE, 0, "No EV charging")

    # -------------------------------------------------------------------
    # SOLAR
    # -------------------------------------------------------------------

    def _step_solar(self, i: EVInputs) -> EVOutput:
        # S0: wallbox unavailable — back to IDLE (NO-01)
        if not i.wallbox_available:
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0,
                            "Wallbox unavailable — back to IDLE")

        # S1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0,
                            "Car finished — wallbox idle")

        # S2: user switched to immediate
        if i.charging_mode == "immediate":
            self._set_state(EVState.IMMEDIATE)
            return EVOutput(EVState.IMMEDIATE, i.manual_power_w,
                            "Immediate mode — charge at max power")

        # S3: user switched to cheap
        if i.charging_mode == "cheap":
            self._set_state(EVState.CHEAP)
            power = i.manual_power_w if i.is_cheap_tariff else 0
            reason = ("Cheap mode — cheap tariff active, charge at max power"
                      if i.is_cheap_tariff
                      else "Cheap mode — waiting for cheap tariff")
            return EVOutput(EVState.CHEAP, power, reason)

        # Stay in SOLAR — use pre-computed power
        if i.ev_charging_power_w > 0:
            return EVOutput(EVState.SOLAR, i.ev_charging_power_w,
                            f"Solar charging {i.ev_charging_power_w:.0f}W")

        # No power — exit to IDLE
        self._set_state(EVState.IDLE)
        return EVOutput(EVState.IDLE, 0, "Solar — no power available")

    # -------------------------------------------------------------------
    # CHEAP
    # -------------------------------------------------------------------

    def _step_cheap(self, i: EVInputs) -> EVOutput:
        # C0: wallbox unavailable — back to IDLE
        if not i.wallbox_available:
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0,
                            "Wallbox unavailable — back to IDLE")

        # C1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0,
                            "Car finished — wallbox idle")

        # C2: user deselected cheap mode
        if i.charging_mode != "cheap":
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0, "No EV charging")

        # Stay in CHEAP — toggle power based on tariff
        if i.is_cheap_tariff:
            return EVOutput(EVState.CHEAP, i.manual_power_w,
                            "Cheap mode — cheap tariff active, charge at max power")
        return EVOutput(EVState.CHEAP, 0,
                        "Cheap mode — waiting for cheap tariff")

    # -------------------------------------------------------------------
    # IMMEDIATE
    # -------------------------------------------------------------------

    def _step_max(self, i: EVInputs) -> EVOutput:
        # M0: wallbox unavailable — back to IDLE
        if not i.wallbox_available:
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0,
                            "Wallbox unavailable — back to IDLE")

        # M1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0,
                            "Car finished — wallbox idle")

        # M2: user deselected immediate mode
        if i.charging_mode != "immediate":
            self._set_state(EVState.IDLE)
            return EVOutput(EVState.IDLE, 0, "No EV charging")

        # Stay in IMMEDIATE
        return EVOutput(EVState.IMMEDIATE, i.manual_power_w,
                        "Immediate mode — charge at max power")


# Dispatch table
_DISPATCH = {
    EVState.IDLE: EVStateMachine._step_idle,
    EVState.SOLAR: EVStateMachine._step_solar,
    EVState.CHEAP: EVStateMachine._step_cheap,
    EVState.IMMEDIATE: EVStateMachine._step_max,
}
