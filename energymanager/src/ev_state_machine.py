"""
EV charging state machine — 4-state design.

States:
  1. NORMAL          — no EV charging, SUN2000 has full control
  2. SOLAR           — variable power from solar excess
  3. CHEAP           — max during cheap tariff, 0 during expensive
  4. IMMEDIATE       — immediate mode, max power regardless

Infrastructure concerns (faults, disconnects) are handled by the OCPP
server.  This state machine only makes charging decisions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

from src.ev_charging import resolve_phase_gap

logger = logging.getLogger(__name__)

# Minimum time (seconds) to stay in SOLAR before allowing
# battery-protection or low-excess exits.
MIN_STAY_S = 15 * 60

# When battery is full, allow solar charging to start at this power
# even without enough PV excess — the battery covers the gap.
BATTERY_FULL_START_W = 3500


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class EVState(str, Enum):
    """EV charging states (str so it works as an HA sensor value)."""
    NORMAL = "normal"
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
    battery_protection_passed: bool
    battery_soc: float
    charging_mode: str                # "solar" / "immediate" / "cheap"
    is_cheap_tariff: bool
    grid_power_w: float
    pv_power_w: float                 # current PV production (W)
    load_power_w: float               # current household load (W)
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


def _compute_excess(i: EVInputs) -> float:
    """Dual excess formula: closed-loop when battery full, open-loop otherwise."""
    if i.battery_soc >= 100:
        return -i.grid_power_w + i.wallbox_power_w      # closed-loop
    return i.pv_power_w - i.load_power_w                 # open-loop


def _solar_target(
    excess: float, min_power_w: float, max_power_w: float,
    battery_full: bool = False,
) -> float:
    """Compute target power for SOLAR state."""
    if excess >= min_power_w:
        target = _round_to_step(_clamp(excess, min_power_w, max_power_w))
        return resolve_phase_gap(target, battery_full)
    # Battery full: hold BATTERY_FULL_START_W instead of min_power_w
    if battery_full:
        return BATTERY_FULL_START_W
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
        if new == EVState.SOLAR and self.state != EVState.SOLAR:
            self._entered_solar_at = self._time_fn()
        if new != EVState.SOLAR:
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
            self._set_state(EVState.IMMEDIATE)
            return EVOutput(EVState.IMMEDIATE, i.max_power_w,
                            "Immediate mode — charge at max power")

        # N2: cheap mode
        if i.charging_mode == "cheap" and i.wallbox_available:
            self._set_state(EVState.CHEAP)
            power = i.max_power_w if i.is_cheap_tariff else 0
            reason = ("Cheap mode — cheap tariff active, charge at max power"
                      if i.is_cheap_tariff
                      else "Cheap mode — waiting for cheap tariff")
            return EVOutput(EVState.CHEAP, power, reason)

        # N3: solar mode — only enter if battery protection allows it
        if i.charging_mode == "solar" and i.wallbox_available:
            if not i.battery_protection_passed:
                return EVOutput(EVState.NORMAL, 0,
                                "Solar — battery protection blocks EV")
            excess = _compute_excess(i)
            battery_full = i.battery_soc >= 100

            # Battery full: start at BATTERY_FULL_START_W even without
            # enough PV excess — the battery discharges to cover the gap.
            if battery_full and excess < i.min_power_w:
                self._set_state(EVState.SOLAR)
                return EVOutput(EVState.SOLAR, BATTERY_FULL_START_W,
                                f"Solar+battery charging {BATTERY_FULL_START_W}W "
                                f"(excess {excess:.0f}W, battery full)")

            if excess >= i.min_power_w:
                self._set_state(EVState.SOLAR)
                target = _solar_target(excess, i.min_power_w, i.max_power_w,
                                       battery_full=battery_full)
                return EVOutput(EVState.SOLAR, target,
                                f"Solar charging {target:.0f}W "
                                f"(excess {excess:.0f}W)")

        # Stay NORMAL
        return EVOutput(EVState.NORMAL, 0, "No EV charging")

    # -------------------------------------------------------------------
    # SOLAR
    # -------------------------------------------------------------------

    def _step_solar(self, i: EVInputs) -> EVOutput:
        # S1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0,
                            "Car finished — wallbox idle")

        # S2: battery protection failed — exit after grace period
        if not i.battery_protection_passed and self._time_in_solar() >= MIN_STAY_S:
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0,
                            "Solar — battery protection blocks EV")

        excess = _compute_excess(i)

        # S3: user switched to immediate
        if i.charging_mode == "immediate":
            self._set_state(EVState.IMMEDIATE)
            return EVOutput(EVState.IMMEDIATE, i.max_power_w,
                            "Immediate mode — charge at max power")

        # S4: user switched to cheap
        if i.charging_mode == "cheap":
            self._set_state(EVState.CHEAP)
            power = i.max_power_w if i.is_cheap_tariff else 0
            reason = ("Cheap mode — cheap tariff active, charge at max power"
                      if i.is_cheap_tariff
                      else "Cheap mode — waiting for cheap tariff")
            return EVOutput(EVState.CHEAP, power, reason)

        # Stay in SOLAR — compute power
        target = _solar_target(excess, i.min_power_w, i.max_power_w,
                               battery_full=(i.battery_soc >= 100))
        return EVOutput(EVState.SOLAR, target,
                        f"Solar charging {target:.0f}W "
                        f"(excess {excess:.0f}W)")

    # -------------------------------------------------------------------
    # CHEAP
    # -------------------------------------------------------------------

    def _step_cheap(self, i: EVInputs) -> EVOutput:
        # C1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0,
                            "Car finished — wallbox idle")

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
    # IMMEDIATE
    # -------------------------------------------------------------------

    def _step_max(self, i: EVInputs) -> EVOutput:
        # M1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0,
                            "Car finished — wallbox idle")

        # M2: user deselected immediate mode
        if i.charging_mode != "immediate":
            self._set_state(EVState.NORMAL)
            return EVOutput(EVState.NORMAL, 0, "No EV charging")

        # Stay in IMMEDIATE
        return EVOutput(EVState.IMMEDIATE, i.max_power_w,
                        "Immediate mode — charge at max power")


# Dispatch table
_DISPATCH = {
    EVState.NORMAL: EVStateMachine._step_normal,
    EVState.SOLAR: EVStateMachine._step_solar,
    EVState.CHEAP: EVStateMachine._step_cheap,
    EVState.IMMEDIATE: EVStateMachine._step_max,
}
