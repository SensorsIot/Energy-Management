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
from enum import StrEnum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class EVState(StrEnum):
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
    # Phase 3 — manual-charge kWh budget (immediate/cheap only; solar ignores)
    target_soc: float = 100.0          # input_number.ev_target_soc (% car SOC)
    car_soc: float | None = None       # sensor.smart_battery_last_known; None if unknown
    car_soc_age_s: float | None = None # seconds since sensor.smart_battery last update
    session_energy_wh: float = 0.0     # sensor.wallbox_energy (OCPP session, Wh)
    capacity_kwh: float = 100.0        # car battery usable capacity (kWh)
    efficiency: float = 0.88           # AC→battery charging efficiency


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
        # Manual-charge kWh budget (set on entry to IMMEDIATE/CHEAP)
        self._budget_start_soc: float | None = None
        self._budget_start_session_wh: float | None = None
        # Last observed session energy — used to detect OCPP session resets
        # (sensor.wallbox_energy goes down when a new transaction begins).
        self._last_session_wh: float | None = None

    def step(self, inputs: EVInputs) -> EVOutput:
        """One cycle: evaluate transitions from current state, return output."""
        handler = _DISPATCH[self.state]
        return handler(self, inputs)

    # -- helpers --

    def _set_state(self, new: EVState) -> None:
        self.state = new

    def _snapshot_budget(self, i: EVInputs) -> None:
        self._budget_start_soc = i.car_soc
        self._budget_start_session_wh = i.session_energy_wh
        self._last_session_wh = i.session_energy_wh
        logger.info(
            "Manual-charge budget snapshot: start_soc=%s target=%.0f%% "
            "capacity=%.1fkWh η=%.2f start_session_wh=%.0f",
            self._budget_start_soc, i.target_soc,
            i.capacity_kwh, i.efficiency, i.session_energy_wh,
        )

    def _clear_budget(self) -> None:
        self._budget_start_soc = None
        self._budget_start_session_wh = None
        self._last_session_wh = None

    def _budget_check(self, i: EVInputs) -> str | None:
        """Return a stop-reason if charging should end, else None.

        Re-snapshots when the OCPP session energy regresses (new session).
        Safety stop: car SOC reached target + 10%.
        Primary stop: delivered Wh ≥ (target − start_soc) × capacity / η.
        If start_soc was unknown at snapshot (car SOC unavailable), the kWh
        budget is not enforced — wallbox-idle and the safety stop still are.
        """
        # No snapshot yet (shouldn't normally happen here) — snap and skip.
        if self._budget_start_session_wh is None:
            self._snapshot_budget(i)
            return None

        # New OCPP session — re-snapshot and continue (no stop yet).
        # Detect against the *last seen* value, not the original snapshot,
        # so a counter that climbs then resets to anything below the last
        # reading triggers a re-snap.
        if (
            self._last_session_wh is not None
            and i.session_energy_wh < self._last_session_wh
        ):
            logger.info(
                "Wallbox session energy regressed (%.0f → %.0f Wh); "
                "re-snapshotting budget",
                self._last_session_wh, i.session_energy_wh,
            )
            self._snapshot_budget(i)
            return None
        self._last_session_wh = i.session_energy_wh

        # SOC-based stop — symmetric, no buffer. If we're early, the user
        # presses the button again and a fresh budget is computed from
        # the new lower start_soc.  If we're late, no buffer would help.
        if i.car_soc is not None and i.car_soc >= i.target_soc:
            age = (f"age={i.car_soc_age_s:.0f}s"
                   if i.car_soc_age_s is not None else "age=unknown")
            return (
                f"Target reached — car SOC {i.car_soc:.0f}% "
                f"≥ target {i.target_soc:.0f}% ({age})"
            )

        # kWh budget requires a known start_soc; otherwise skip (no-cap).
        if self._budget_start_soc is None:
            return None

        # Already at/past target on entry → instant stop.
        delta_soc = i.target_soc - self._budget_start_soc
        if delta_soc <= 0:
            return (
                f"Already at target — start SOC {self._budget_start_soc:.0f}% "
                f"≥ target {i.target_soc:.0f}%"
            )

        # Primary: delivered Wh meets kWh budget.
        budget_wh = delta_soc / 100.0 * i.capacity_kwh * 1000.0 / i.efficiency
        delivered_wh = i.session_energy_wh - self._budget_start_session_wh
        if delivered_wh >= budget_wh:
            return (
                f"Budget reached — delivered {delivered_wh:.0f}Wh "
                f"≥ {budget_wh:.0f}Wh "
                f"({delta_soc:.0f}% × {i.capacity_kwh:.1f}kWh / η={i.efficiency:.2f})"
            )
        return None

    def _enter_immediate(self, i: EVInputs) -> EVOutput:
        """Transition into IMMEDIATE: snapshot budget and short-circuit if exhausted."""
        self._set_state(EVState.IMMEDIATE)
        self._snapshot_budget(i)
        stop = self._budget_check(i)
        if stop:
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0, stop)
        return EVOutput(EVState.IMMEDIATE, i.manual_power_w,
                        "Immediate mode — charge at max power")

    def _enter_cheap(self, i: EVInputs) -> EVOutput:
        """Transition into CHEAP: snapshot budget and short-circuit if exhausted."""
        self._set_state(EVState.CHEAP)
        self._snapshot_budget(i)
        stop = self._budget_check(i)
        if stop:
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0, stop)
        power = i.manual_power_w if i.is_cheap_tariff else 0
        reason = ("Cheap mode — cheap tariff active, charge at max power"
                  if i.is_cheap_tariff
                  else "Cheap mode — waiting for cheap tariff")
        return EVOutput(EVState.CHEAP, power, reason)

    # -------------------------------------------------------------------
    # IDLE
    # -------------------------------------------------------------------

    def _step_idle(self, i: EVInputs) -> EVOutput:
        # N1: immediate mode
        if i.charging_mode == "immediate" and i.wallbox_available:
            return self._enter_immediate(i)

        # N2: cheap mode
        if i.charging_mode == "cheap" and i.wallbox_available:
            return self._enter_cheap(i)

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
            return self._enter_immediate(i)

        # S3: user switched to cheap
        if i.charging_mode == "cheap":
            return self._enter_cheap(i)

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
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0,
                            "Wallbox unavailable — back to IDLE")

        # C1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0,
                            "Car finished — wallbox idle")

        # C2: user deselected cheap mode
        if i.charging_mode != "cheap":
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0, "No EV charging")

        # C3: kWh budget reached (Phase 3)
        stop = self._budget_check(i)
        if stop:
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0, stop)

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
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0,
                            "Wallbox unavailable — back to IDLE")

        # M1: car finished — wallbox idle
        if i.wallbox_idle:
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0,
                            "Car finished — wallbox idle")

        # M2: user deselected immediate mode
        if i.charging_mode != "immediate":
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0, "No EV charging")

        # M3: kWh budget reached (Phase 3)
        stop = self._budget_check(i)
        if stop:
            self._set_state(EVState.IDLE)
            self._clear_budget()
            return EVOutput(EVState.IDLE, 0, stop)

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
