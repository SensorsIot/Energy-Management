"""
EV charging state machine — 4-state design.

States:
  1. NORMAL          — no EV charging, SUN2000 has full control
  2. SOLAR_CHARGING  — variable power from solar excess
  3. CHEAP           — max during cheap tariff, 0 during expensive
  4. MAX_CHARGING    — immediate mode, max power regardless

Infrastructure concerns (faults, disconnects) are handled by the OCPP
server.  This state machine only makes charging decisions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Minimum time (seconds) to stay in SOLAR_CHARGING before allowing
# battery-protection or low-excess exits.
MIN_STAY_S = 15 * 60


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class EVState(str, Enum):
    """EV charging states (str so it works as an HA sensor value)."""
    NORMAL = "normal"
    SOLAR_CHARGING = "solar_charging"
    CHEAP = "cheap"
    MAX_CHARGING = "max_charging"


# ---------------------------------------------------------------------------
# Input / Output dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EVInputs:
    """All inputs needed for one step() call."""
    wallbox_available: bool
    wallbox_power_w: float
    wallbox_status: str               # for OCPP status logging only
    battery_protection_passed: bool
    battery_soc: float
    ev_soc: float | None
    ev_target_soc: float
    charging_mode: str                # "solar" / "immediate" / "cheap"
    is_cheap_tariff: bool
    grid_power_w: float
    min_power_w: float                # default 1400W
    max_power_w: float                # default 11000W


@dataclass
class EVOutput:
    """Decision produced by step()."""
    state: EVState
    target_power_w: float
    reason: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_to_step(value: float, step: float = 100) -> float:
    """Round to nearest step (matches wallbox_power_limit step size)."""
    return round(value / step) * step


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def _solar_target(excess: float, min_power_w: float, max_power_w: float) -> float:
    """Compute target power for SOLAR_CHARGING state."""
    if excess >= min_power_w:
        return _round_to_step(_clamp(excess, min_power_w, max_power_w))
    return min_power_w  # hold minimum during low-excess periods


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class EVStateMachine:
    """4-state EV charging state machine.

    Call step() each cycle with current inputs.  Returns EVOutput with
    the decided state and target power.
    """

    def __init__(self, *, time_fn=None) -> None:
        self.state: EVState = EVState.NORMAL
        self._entered_solar_at: float | None = None
        self._time_fn = time_fn or time.monotonic

    def step(self, inputs: EVInputs) -> EVOutput:
        """One cycle: evaluate transitions from current state, return output."""
        handler = _DISPATCH[self.state]
        return handler(self, inputs)

    # -- helpers --

    def _set_state(self, new: EVState) -> None:
        if new == EVState.SOLAR_CHARGING and self.state != EVState.SOLAR_CHARGING:
            self._entered_solar_at = self._time_fn()
        if new != EVState.SOLAR_CHARGING:
            self._entered_solar_at = None
        self.state = new

    def _time_in_solar(self) -> float:
        if self._entered_solar_at is None:
            return 0.0
        return self._time_fn() - self._entered_solar_at

    # -------------------------------------------------------------------
    # NORMAL
    # -------------------------------------------------------------------

    def _step_normal(self, i: EVInputs) -> EVOutput:
        # N1: immediate mode
        if i.charging_mode == "immediate" and i.wallbox_available:
            self._set_state(EVState.MAX_CHARGING)
            return EVOutput(EVState.MAX_CHARGING, i.max_power_w,
                            "Immediate mode — charge at max power")

        # N2: cheap mode
        if i.charging_mode == "cheap" and i.wallbox_available:
            self._set_state(EVState.CHEAP)
            power = i.max_power_w if i.is_cheap_tariff else 0
            reason = ("Cheap mode — cheap tariff active, charge at max power"
                      if i.is_cheap_tariff
                      else "Cheap mode — waiting for cheap tariff")
            return EVOutput(EVState.CHEAP, power, reason)

        # N3: solar mode
        if (i.charging_mode == "solar"
                and i.wallbox_available
                and (i.battery_protection_passed or i.battery_soc >= 100)):
            excess = -i.grid_power_w + i.wallbox_power_w
            if excess >= i.min_power_w:
                self._set_state(EVState.SOLAR_CHARGING)
                target = _solar_target(excess, i.min_power_w, i.max_power_w)
                return EVOutput(EVState.SOLAR_CHARGING, target,
                                f"Solar charging {target:.0f}W "
                                f"(excess {excess:.0f}W)")

        # Stay NORMAL
        return EVOutput(EVState.NORMAL, 0, "No EV charging")

    # -------------------------------------------------------------------
    # SOLAR_CHARGING
    # -------------------------------------------------------------------

    def _step_solar(self, i: EVInputs) -> EVOutput:
        excess = -i.grid_power_w + i.wallbox_power_w
        time_in = self._time_in_solar()

        # S1: car full
        if i.ev_soc is not None and i.ev_soc >= i.ev_target_soc:
            self._set_state(EVState.NORMAL)
            soc_str = f"{i.ev_soc:.0f}"
            return EVOutput(EVState.NORMAL, 0,
                            f"Car full: SOC {soc_str}% >= "
                            f"target {i.ev_target_soc:.0f}%")

        # S2: user switched to immediate
        if i.charging_mode == "immediate":
            self._set_state(EVState.MAX_CHARGING)
            return EVOutput(EVState.MAX_CHARGING, i.max_power_w,
                            "Immediate mode — charge at max power")

        # S3: user switched to cheap
        if i.charging_mode == "cheap":
            self._set_state(EVState.CHEAP)
            power = i.max_power_w if i.is_cheap_tariff else 0
            reason = ("Cheap mode — cheap tariff active, charge at max power"
                      if i.is_cheap_tariff
                      else "Cheap mode — waiting for cheap tariff")
            return EVOutput(EVState.CHEAP, power, reason)

        # S4: battery protection kicks in (only after min-stay)
        if (time_in >= MIN_STAY_S
                and not i.battery_protection_passed
                and i.battery_soc < 100):
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0,
                            "Battery protection — pausing EV charging")

        # Stay in SOLAR_CHARGING — compute power
        target = _solar_target(excess, i.min_power_w, i.max_power_w)
        return EVOutput(EVState.SOLAR_CHARGING, target,
                        f"Solar charging {target:.0f}W "
                        f"(excess {excess:.0f}W)")

    # -------------------------------------------------------------------
    # CHEAP
    # -------------------------------------------------------------------

    def _step_cheap(self, i: EVInputs) -> EVOutput:
        # C1: car full
        if i.ev_soc is not None and i.ev_soc >= i.ev_target_soc:
            self._set_state(EVState.NORMAL)
            soc_str = f"{i.ev_soc:.0f}"
            return EVOutput(EVState.NORMAL, 0,
                            f"Car full: SOC {soc_str}% >= "
                            f"target {i.ev_target_soc:.0f}%")

        # C2: user deselected cheap mode
        if i.charging_mode != "cheap":
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0, "No EV charging")

        # Stay in CHEAP — toggle power based on tariff
        if i.is_cheap_tariff:
            return EVOutput(EVState.CHEAP, i.max_power_w,
                            "Cheap mode — cheap tariff active, charge at max power")
        return EVOutput(EVState.CHEAP, 0,
                        "Cheap mode — waiting for cheap tariff")

    # -------------------------------------------------------------------
    # MAX_CHARGING
    # -------------------------------------------------------------------

    def _step_max(self, i: EVInputs) -> EVOutput:
        # M1: car full
        if i.ev_soc is not None and i.ev_soc >= i.ev_target_soc:
            self._set_state(EVState.NORMAL)
            soc_str = f"{i.ev_soc:.0f}"
            return EVOutput(EVState.NORMAL, 0,
                            f"Car full: SOC {soc_str}% >= "
                            f"target {i.ev_target_soc:.0f}%")

        # M2: user deselected immediate mode
        if i.charging_mode != "immediate":
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0, "No EV charging")

        # Stay in MAX_CHARGING
        return EVOutput(EVState.MAX_CHARGING, i.max_power_w,
                        "Immediate mode — charge at max power")


# Dispatch table
_DISPATCH = {
    EVState.NORMAL: EVStateMachine._step_normal,
    EVState.SOLAR_CHARGING: EVStateMachine._step_solar,
    EVState.CHEAP: EVStateMachine._step_cheap,
    EVState.MAX_CHARGING: EVStateMachine._step_max,
}
