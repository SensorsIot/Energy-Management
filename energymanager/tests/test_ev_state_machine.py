"""
Tests for 4-state EV charging state machine.
"""

import pytest

from src.ev_state_machine import (
    EVInputs,
    EVState,
    EVStateMachine,
    MIN_STAY_S,
)


# ---------------------------------------------------------------------------
# Helper: build EVInputs with sane defaults
# ---------------------------------------------------------------------------

def make_inputs(**overrides) -> EVInputs:
    """Create EVInputs with sensible defaults for solar-mode happy path.

    ev_target_power_w defaults to 5000 (strategy says charge at 5000W).
    """
    defaults = dict(
        wallbox_available=True,
        wallbox_power_w=0,
        wallbox_status="Preparing",
        wallbox_idle=False,
        battery_protection_passed=True,
        battery_soc=70.0,
        charging_mode="solar",
        is_cheap_tariff=False,
        grid_power_w=-5000.0,
        surplus_power_w=5000.0,
        pv_power_w=8000.0,
        load_power_w=3000.0,
        min_power_w=1400.0,
        manual_power_w=11000.0,
        ev_target_power_w=5000.0,
    )
    defaults.update(overrides)
    return EVInputs(**defaults)


_T0 = 1000.0  # arbitrary start time


def make_sm(state: EVState = EVState.IDLE, *, now: float = _T0) -> EVStateMachine:
    """Create an EVStateMachine pre-set to the given state."""
    sm = EVStateMachine(time_fn=lambda: now)
    sm.state = state
    if state == EVState.SOLAR:
        sm._entered_solar_at = now
    return sm


# ===================================================================
# EVState enum
# ===================================================================

class TestEVStateEnum:
    def test_str_inheritance(self):
        assert isinstance(EVState.IDLE, str)
        assert EVState.IDLE == "idle"

    def test_four_states(self):
        assert len(EVState) == 4

    def test_snake_case_values(self):
        for member in EVState:
            assert member.value == member.value.lower()
            assert " " not in member.value


# ===================================================================
# EVStateMachine init
# ===================================================================

class TestInit:
    def test_initial_state_is_normal(self):
        sm = EVStateMachine()
        assert sm.state == EVState.IDLE


# ===================================================================
# Strategy-based SOLAR entry / stay
# ===================================================================

class TestStrategyEntry:
    def test_strategy_positive_enters_solar(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_target_power_w=6900))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 6900
        assert "strategy" in out.reason

    def test_strategy_zero_stays_normal(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_target_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_strategy_blocked_by_battery_protection(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            ev_target_power_w=5000,
            battery_protection_passed=False,
        ))
        assert out.state == EVState.IDLE
        assert "battery protection" in out.reason.lower()


class TestStrategySolarStay:
    def test_strategy_positive_stays_solar(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_target_power_w=4140))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 4140

    def test_strategy_zero_exits_solar(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_target_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0
        assert "no power source" in out.reason

    def test_strategy_updates_power(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_target_power_w=6900))
        assert out.target_power_w == 6900
        out = sm.step(make_inputs(ev_target_power_w=4140))
        assert out.target_power_w == 4140


# ===================================================================
# NORMAL — stays
# ===================================================================

class TestNormalStays:
    def test_stays_when_no_conditions(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="solar",
            wallbox_available=False,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_stays_solar_no_wallbox(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_available=False))
        assert out.state == EVState.IDLE

    def test_n3_solar_blocked_by_battery_protection(self):
        """Solar does NOT enter when battery_protection_passed=False."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            battery_protection_passed=False,
            battery_soc=70,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0
        assert "battery protection" in out.reason.lower()


# ===================================================================
# NORMAL → transitions (N1, N2, N3)
# ===================================================================

class TestNormalTransitions:
    def test_n1_immediate(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_n1_immediate_no_wallbox_stays_normal(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_n2_cheap(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

    def test_n2_cheap_no_wallbox_stays_normal(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_n3_solar_with_strategy(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_target_power_w=5000))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_n3_solar_blocked_by_battery_protection(self):
        """N3 does NOT enter SOLAR when battery_protection_passed=False."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            battery_protection_passed=False,
            battery_soc=50,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0
        assert "battery protection" in out.reason.lower()

    def test_n1_has_priority_over_n2(self):
        """N1 fires before N2 when both could match."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE


# ===================================================================
# SOLAR — stays
# ===================================================================

class TestSolarStays:
    def test_stays_with_strategy_power(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_target_power_w=5000))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_exits_solar_on_battery_protection_after_grace(self):
        """Exits SOLAR when battery_protection_passed=False after MIN_STAY_S."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + MIN_STAY_S + 1
        out = sm.step(make_inputs(
            battery_protection_passed=False, battery_soc=70,
        ))
        assert out.state == EVState.IDLE
        assert "battery protection" in out.reason.lower()

    def test_stays_solar_on_battery_protection_during_grace(self):
        """Stays in SOLAR when battery_protection_passed=False within MIN_STAY_S."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + MIN_STAY_S - 60  # still in grace period
        out = sm.step(make_inputs(
            battery_protection_passed=False, battery_soc=70,
            ev_target_power_w=5000,
        ))
        assert out.state == EVState.SOLAR


# ===================================================================
# SOLAR → transitions (S1, S2, S3)
# ===================================================================

class TestSolarTransitions:
    def test_s2_switch_to_immediate(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_s3_switch_to_cheap(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=True))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 11000

    def test_s3_switch_to_cheap_expensive(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(charging_mode="cheap", is_cheap_tariff=False))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0


# ===================================================================
# CHEAP — stays
# ===================================================================

class TestCheapStays:
    def test_stays_expensive_tariff(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=False,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0

    def test_stays_cheap_tariff(self):
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
    def test_c2_mode_changed_to_solar(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

    def test_c2_mode_changed_to_immediate(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IDLE


# ===================================================================
# CHEAP — power toggles (no state change)
# ===================================================================

class TestCheapPowerToggle:
    def test_max_when_cheap(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=True,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 11000

    def test_zero_when_expensive(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=False,
        ))
        assert out.state == EVState.CHEAP
        assert out.target_power_w == 0

    def test_toggle_back_and_forth(self):
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

    def test_custom_max_power(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", is_cheap_tariff=True, manual_power_w=7400,
        ))
        assert out.target_power_w == 7400


# ===================================================================
# IMMEDIATE — stays
# ===================================================================

class TestMaxStays:
    def test_stays_at_max(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_custom_max(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(
            charging_mode="immediate", manual_power_w=7400,
        ))
        assert out.target_power_w == 7400


# ===================================================================
# IMMEDIATE → transitions (M1, M2)
# ===================================================================

class TestMaxTransitions:
    def test_m2_mode_changed_to_solar(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

    def test_m2_mode_changed_to_cheap(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.IDLE


# ===================================================================
# Min-stay timer (SOLAR)
# ===================================================================

class TestMinStayTimer:
    def test_s2_fires_during_minstay(self):
        """Mode switch to immediate (S2) can fire during min-stay."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + 60
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

    def test_entered_at_set_on_entry(self):
        """Timestamp is recorded when entering SOLAR."""
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])
        sm.step(make_inputs(ev_target_power_w=5000))
        assert sm.state == EVState.SOLAR
        assert sm._entered_solar_at == _T0

    def test_entered_at_cleared_on_exit(self):
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm.step(make_inputs(charging_mode="immediate"))
        assert sm.state == EVState.IMMEDIATE
        assert sm._entered_solar_at is None


# ===================================================================
# wallbox_available
# ===================================================================

class TestWallboxAvailable:
    def test_immediate_blocked_without_wallbox(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_cheap_blocked_without_wallbox(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_solar_blocked_without_wallbox(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_available=False))
        assert out.state == EVState.IDLE


# ===================================================================
# Idle detection (wallbox_idle exits to NORMAL)
# ===================================================================

class TestIdleDetection:
    def test_solar_idle_exits_to_normal(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0
        assert "idle" in out.reason.lower()

    def test_cheap_idle_exits_to_normal(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="cheap", wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_immediate_idle_exits_to_normal(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="immediate", wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_solar_not_idle_stays(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=False, ev_target_power_w=5000))
        assert out.state == EVState.SOLAR

    def test_normal_ignores_idle(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_idle=True, wallbox_available=False))
        assert out.state == EVState.IDLE


# ===================================================================
# Multi-step sequences
# ===================================================================

class TestMultiStep:
    def test_normal_to_solar_to_normal_on_strategy_stop(self):
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])

        # Start NORMAL — strategy = 0
        out = sm.step(make_inputs(ev_target_power_w=0))
        assert out.state == EVState.IDLE

        # Strategy says charge → SOLAR
        out = sm.step(make_inputs(ev_target_power_w=5000))
        assert out.state == EVState.SOLAR

        # Strategy stops → NORMAL
        out = sm.step(make_inputs(ev_target_power_w=0))
        assert out.state == EVState.IDLE

    def test_full_mode_cycle(self):
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])

        # NORMAL → IMMEDIATE
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

        # IMMEDIATE → NORMAL (mode changed)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

        # NORMAL → CHEAP
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

        # CHEAP → NORMAL (mode changed)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

        # NORMAL → SOLAR (strategy says charge)
        out = sm.step(make_inputs(ev_target_power_w=5000))
        assert out.state == EVState.SOLAR

    def test_solar_to_immediate_and_back(self):
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])

        # → SOLAR
        out = sm.step(make_inputs(ev_target_power_w=5000))
        assert out.state == EVState.SOLAR

        # → IMMEDIATE
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

        # → NORMAL (mode back to solar)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.IDLE

        # → SOLAR again (strategy active)
        out = sm.step(make_inputs(ev_target_power_w=5000))
        assert out.state == EVState.SOLAR


# ===================================================================
# Surplus capture (grid export → 1-phase EV charging)
# ===================================================================

class TestSurplusCaptureEntry:
    """N3a: surplus capture enters SOLAR bypassing battery protection."""

    def test_surplus_enters_solar(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            ev_target_power_w=0,
            surplus_capture_power_w=1380,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1380
        assert "surplus capture" in out.reason.lower()

    def test_surplus_bypasses_battery_protection(self):
        """Surplus capture works even when battery protection blocks."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            ev_target_power_w=0,
            surplus_capture_power_w=1380,
            battery_protection_passed=False,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1380

    def test_strategy_takes_priority_over_surplus_at_entry(self):
        """When both strategy and surplus are available, surplus is checked first
        (it's before the protection gate), but in practice both won't be >0
        simultaneously since run.py sets surplus=0 when strategy>0."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            ev_target_power_w=5000,
            surplus_capture_power_w=1380,
        ))
        # surplus_capture is checked first in N3a
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1380


class TestSurplusCaptureSolarStay:
    """SOLAR stay: forecast > surplus > exit."""

    def test_forecast_priority_over_surplus(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(
            ev_target_power_w=6900,
            surplus_capture_power_w=1380,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 6900
        assert "strategy" in out.reason

    def test_surplus_fallback_when_no_forecast(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(
            ev_target_power_w=0,
            surplus_capture_power_w=1380,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1380
        assert "surplus capture" in out.reason.lower()

    def test_exit_when_no_forecast_no_surplus(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(
            ev_target_power_w=0,
            surplus_capture_power_w=0,
        ))
        assert out.state == EVState.IDLE
        assert "no power source" in out.reason.lower()


class TestSurplusCaptureProtectionBypass:
    """S2: protection failure doesn't exit when surplus capture is active."""

    def test_protection_fail_no_exit_with_surplus(self):
        """Stay in SOLAR despite protection failure when surplus is active."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + MIN_STAY_S + 1
        out = sm.step(make_inputs(
            battery_protection_passed=False,
            ev_target_power_w=0,
            surplus_capture_power_w=1380,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1380

    def test_protection_fail_exits_without_surplus(self):
        """Confirm S2 still exits when surplus is 0."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + MIN_STAY_S + 1
        out = sm.step(make_inputs(
            battery_protection_passed=False,
            ev_target_power_w=5000,
            surplus_capture_power_w=0,
        ))
        assert out.state == EVState.IDLE
        assert "battery protection" in out.reason.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
