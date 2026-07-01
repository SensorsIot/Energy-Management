"""Tests for 4-state EV charging state machine."""

import pytest

from src.ev_state_machine import (
    EVInputs,
    EVState,
    EVStateMachine,
)


# ---------------------------------------------------------------------------
# Helper: build EVInputs with sane defaults
# ---------------------------------------------------------------------------

def make_inputs(**overrides) -> EVInputs:
    """Create EVInputs with sensible defaults for solar-mode happy path.

    ev_charging_power_w defaults to 5000 (pre-computed power says charge at 5000W).
    """
    defaults = dict(
        wallbox_available=True,
        wallbox_power_w=0,
        wallbox_status="Preparing",
        wallbox_idle=False,
        battery_soc=70.0,
        charging_mode="solar",
        is_cheap_tariff=False,
        grid_power_w=-5000.0,
        surplus_power_w=5000.0,
        pv_power_w=8000.0,
        load_power_w=3000.0,
        min_power_w=1400.0,
        manual_power_w=11000.0,
        ev_charging_power_w=5000.0,
    )
    defaults.update(overrides)
    return EVInputs(**defaults)


def make_sm(state: EVState = EVState.IDLE) -> EVStateMachine:
    """Create an EVStateMachine pre-set to the given state."""
    sm = EVStateMachine()
    sm.state = state
    return sm


# ===================================================================
# EVState enum
# ===================================================================

class TestEVStateEnum:
    def test_str_inheritance(self) -> None:
        assert isinstance(EVState.IDLE, str)
        assert EVState.IDLE == "idle"

    def test_four_states(self) -> None:
        assert len(EVState) == 4

    def test_snake_case_values(self) -> None:
        for member in EVState:
            assert member.value == member.value.lower()
            assert " " not in member.value


# ===================================================================
# EVStateMachine init
# ===================================================================

class TestInit:
    def test_initial_state_is_idle(self) -> None:
        sm = EVStateMachine()
        assert sm.state == EVState.IDLE


# ===================================================================
# SOLAR entry / stay (via ev_charging_power_w)
# ===================================================================

class TestSolarEntry:
    def test_power_positive_enters_solar(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=6900))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 6900

    def test_power_zero_stays_idle(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0


class TestSolarStay:
    def test_power_positive_stays_solar(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_charging_power_w=4140))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 4140

    def test_power_zero_exits_to_idle(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_power_updates(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_charging_power_w=6900))
        assert out.target_power_w == 6900
        out = sm.step(make_inputs(ev_charging_power_w=4140))
        assert out.target_power_w == 4140


# ===================================================================
# IDLE — stays
# ===================================================================

class TestIdleStays:
    def test_stays_when_no_conditions(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="solar",
            wallbox_available=False,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_stays_solar_no_wallbox(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_available=False))
        assert out.state == EVState.IDLE

    def test_stays_solar_no_power(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0


# ===================================================================
# IDLE → transitions (N1, N2, N3)
# ===================================================================

class TestIdleTransitions:
    def test_n1_immediate(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_n1_immediate_no_wallbox_stays_idle(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_n2_cheap(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

    def test_n2_cheap_no_wallbox_stays_idle(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_n3_solar_with_power(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_n1_has_priority_over_n2(self) -> None:
        """N1 fires before N2 when both could match."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE


# ===================================================================
# SOLAR → transitions (S1, S2, S3)
# ===================================================================

class TestSolarTransitions:
    def test_s2_switch_to_immediate(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_s3_switch_to_cheap(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=True))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 11000

    def test_s3_switch_to_cheap_expensive(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=False))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0


# ===================================================================
# CHEAP — stays
# ===================================================================

class TestCheapStays:
    def test_stays_expensive_tariff(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=False,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0

    def test_stays_cheap_tariff(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=True,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 11000


# ===================================================================
# CHEAP — transitions (C1, C2)
# ===================================================================

class TestCheapTransitions:
    def test_c2_mode_changed_to_solar(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

    def test_c2_mode_changed_to_immediate(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IDLE


# ===================================================================
# CHEAP — power toggles (no state change)
# ===================================================================

class TestCheapPowerToggle:
    def test_max_when_cheap(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=True,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 11000

    def test_zero_when_expensive(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=False,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0

    def test_toggle_back_and_forth(self) -> None:
        sm = make_sm(EVState.CHEAP)
        # expensive
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=False))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0
        # cheap
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=True))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 11000
        # expensive again
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=False))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0

    def test_custom_max_power(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=True, manual_power_w=7400,
        ))
        assert out.target_power_w == 7400


# ===================================================================
# IMMEDIATE — stays
# ===================================================================

class TestMaxStays:
    def test_stays_at_max(self) -> None:
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_custom_max(self) -> None:
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(
            charging_mode="immediate", manual_power_w=7400,
        ))
        assert out.target_power_w == 7400


# ===================================================================
# IMMEDIATE → transitions (M1, M2)
# ===================================================================

class TestMaxTransitions:
    def test_m2_mode_changed_to_solar(self) -> None:
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

    def test_m2_mode_changed_to_cheap(self) -> None:
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.IDLE


# ===================================================================
# wallbox_available
# ===================================================================

class TestWallboxAvailable:
    def test_immediate_blocked_without_wallbox(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_cheap_blocked_without_wallbox(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_solar_blocked_without_wallbox(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_available=False))
        assert out.state == EVState.IDLE

    def test_solar_exits_to_idle_without_wallbox(self) -> None:
        """NO-01 regression: SOLAR must exit to IDLE when wallbox disappears."""
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_available=False, ev_charging_power_w=5000))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_cheap_exits_to_idle_without_wallbox(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False, is_cheap_tariff=True,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_immediate_exits_to_idle_without_wallbox(self) -> None:
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0


# ===================================================================
# Idle detection (wallbox_idle exits to IDLE)
# ===================================================================

class TestIdleDetection:
    def test_solar_idle_exits_to_idle(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0
        assert "idle" in out.reason.lower()

    def test_cheap_idle_exits_to_idle(self) -> None:
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="cheap", wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_immediate_idle_exits_to_idle(self) -> None:
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="immediate", wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_solar_not_idle_stays(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=False, ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR

    def test_idle_ignores_idle_flag(self) -> None:
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_idle=True, wallbox_available=False))
        assert out.state == EVState.IDLE


# ===================================================================
# Multi-step sequences
# ===================================================================

class TestMultiStep:
    def test_idle_to_solar_to_idle_on_power_stop(self) -> None:
        sm = EVStateMachine()

        # Start IDLE — no power
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE

        # Power available → SOLAR
        out = sm.step(make_inputs(ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR

        # Power stops → IDLE
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE

    def test_full_mode_cycle(self) -> None:
        sm = EVStateMachine()

        # IDLE → IMMEDIATE
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

        # IMMEDIATE → IDLE (mode changed)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

        # IDLE → CHEAP
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

        # CHEAP → IDLE (mode changed)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

        # IDLE → SOLAR (power available)
        out = sm.step(make_inputs(ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR

    def test_solar_to_immediate_and_back(self) -> None:
        sm = EVStateMachine()

        # → SOLAR
        out = sm.step(make_inputs(ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR

        # → IMMEDIATE
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

        # → IDLE (mode back to solar)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

        # → SOLAR again (power active)
        out = sm.step(make_inputs(ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR


# ===================================================================
# Phase 3 — manual-charge SOC stop + kWh budget
# ===================================================================

def budget_inputs(**overrides) -> EVInputs:
    """EVInputs preset for a manual-charge (immediate) session.

    Defaults: car_soc=20%, target=25%, capacity=17.6 kWh (Smart EQ), η=0.88.
    Budget = (25-20)/100 * 17.6 * 1000 / 0.88 ≈ 1000 Wh.
    """
    defaults = dict(
        charging_mode="immediate",
        wallbox_status="Charging",
        target_soc=25.0,
        car_soc=20.0,
        session_energy_wh=0.0,
        capacity_kwh=17.6,
        efficiency=0.88,
        ev_charging_power_w=0.0,  # immediate ignores it
    )
    defaults.update(overrides)
    return make_inputs(**defaults)


class TestBudgetStop:
    def test_snapshot_taken_on_idle_to_immediate(self) -> None:
        sm = EVStateMachine()
        out = sm.step(budget_inputs())
        assert out.state == EVState.IMMEDIATE
        assert sm._budget_start_soc == 20.0
        assert sm._budget_start_session_wh == 0.0

    def test_snapshot_taken_on_idle_to_cheap(self) -> None:
        sm = EVStateMachine()
        out = sm.step(budget_inputs(charging_mode="cheap", is_cheap_tariff=True))
        assert out.state == EVState.CHEAP
        assert sm._budget_start_soc == 20.0

    def test_snapshot_taken_on_solar_to_immediate(self) -> None:
        sm = make_sm(EVState.SOLAR)
        out = sm.step(budget_inputs())
        assert out.state == EVState.IMMEDIATE
        assert sm._budget_start_soc == 20.0
        assert sm._budget_start_session_wh == 0.0

    def test_kwh_budget_stops_charging(self) -> None:
        sm = EVStateMachine()
        # Enter immediate, budget = ~1000 Wh
        sm.step(budget_inputs())
        # 999 Wh delivered — still under budget
        out = sm.step(budget_inputs(session_energy_wh=999.0))
        assert out.state == EVState.IMMEDIATE
        # 1001 Wh delivered — over budget → stop
        out = sm.step(budget_inputs(session_energy_wh=1001.0))
        assert out.state == EVState.IDLE
        assert "Budget reached" in out.reason
        # Budget cleared on stop
        assert sm._budget_start_soc is None

    def test_target_reached_stops_charging(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs())  # snapshot start_soc=20%, target=25%
        # Car SOC catches up to target exactly — stop, regardless of delivered Wh.
        out = sm.step(budget_inputs(session_energy_wh=100.0, car_soc=25.0))
        assert out.state == EVState.IDLE
        assert "Target reached" in out.reason

    def test_target_reached_logs_freshness(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs(car_soc_age_s=60.0))
        # Fresh reading hits target
        out = sm.step(budget_inputs(car_soc=25.0, car_soc_age_s=30.0))
        assert out.state == EVState.IDLE
        assert "age=30s" in out.reason
        # Reset and try with stale reading
        sm2 = EVStateMachine()
        sm2.step(budget_inputs(car_soc_age_s=3600.0))
        out = sm2.step(budget_inputs(car_soc=25.0, car_soc_age_s=3600.0))
        assert out.state == EVState.IDLE
        assert "age=3600s" in out.reason

    def test_already_at_target_on_entry(self) -> None:
        sm = EVStateMachine()
        # car_soc 30% already above target 25% — SOC stop fires on entry tick
        out = sm.step(budget_inputs(car_soc=30.0))
        assert out.state == EVState.IDLE
        assert "Target reached" in out.reason

    def test_slider_drag_mid_session_extends_budget(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs())  # target 25%, start 20%, budget ~1000 Wh
        # User drags slider up to 40% — new budget ~4000 Wh
        out = sm.step(budget_inputs(target_soc=40.0, session_energy_wh=999.0))
        assert out.state == EVState.IMMEDIATE
        # Still well under the new budget at 1500 Wh
        out = sm.step(budget_inputs(target_soc=40.0, session_energy_wh=1500.0))
        assert out.state == EVState.IMMEDIATE

    def test_slider_drag_down_mid_session_triggers_stop(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs(target_soc=80.0))  # generous target, big budget
        # 500 Wh delivered, user drags target down to 21% (just barely above start)
        # budget = (21-20)/100 * 17.6 * 1000 / 0.88 ≈ 200 Wh, already exceeded
        out = sm.step(budget_inputs(target_soc=21.0, session_energy_wh=500.0))
        assert out.state == EVState.IDLE
        assert "Budget reached" in out.reason

    def test_session_reset_resnapshots(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs())
        # Mid session — 500 Wh delivered, well under 1000 budget
        sm.step(budget_inputs(session_energy_wh=500.0))
        # Wallbox session resets (unplug/replug): energy goes back to 0
        # at this point car_soc has advanced from 20 to 23
        out = sm.step(budget_inputs(session_energy_wh=0.0, car_soc=23.0))
        assert out.state == EVState.IMMEDIATE
        # New snapshot recorded
        assert sm._budget_start_soc == 23.0
        assert sm._budget_start_session_wh == 0.0
        # New budget = (25-23)/100 * 17.6 * 1000 / 0.88 ≈ 400 Wh
        # 500 Wh delivered → over new budget
        out = sm.step(budget_inputs(session_energy_wh=500.0, car_soc=23.0))
        assert out.state == EVState.IDLE
        assert "Budget reached" in out.reason

    def test_budget_cleared_on_wallbox_idle(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs())
        assert sm._budget_start_soc is not None
        # Car finishes (wallbox_idle = True)
        out = sm.step(budget_inputs(wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert sm._budget_start_soc is None

    def test_budget_cleared_on_mode_switch(self) -> None:
        sm = EVStateMachine()
        sm.step(budget_inputs())
        assert sm._budget_start_soc is not None
        # User switches mode back to solar
        out = sm.step(budget_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE
        assert sm._budget_start_soc is None

    def test_car_soc_unknown_skips_kwh_budget(self) -> None:
        """If car_soc is None at entry, start_soc snapshot is None and the
        kWh budget can't be computed — fall back to no-cap behavior.
        Wallbox-idle path still works; once car_soc appears and meets
        target, the SOC stop fires."""
        sm = EVStateMachine()
        sm.step(budget_inputs(car_soc=None))
        # Snapshot exists, but start_soc is None
        assert sm._budget_start_soc is None
        assert sm._budget_start_session_wh == 0.0
        # Huge delivered, but kWh check is skipped — stay in IMMEDIATE
        out = sm.step(budget_inputs(car_soc=None, session_energy_wh=100_000.0))
        assert out.state == EVState.IMMEDIATE
        # Once car_soc reappears at/above target, SOC stop fires
        out = sm.step(budget_inputs(car_soc=25.0, session_energy_wh=100_000.0))
        assert out.state == EVState.IDLE
        assert "Target reached" in out.reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
