"""
Tests for 4-state EV charging state machine.
"""

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
    def test_initial_state_is_idle(self):
        sm = EVStateMachine()
        assert sm.state == EVState.IDLE


# ===================================================================
# SOLAR entry / stay (via ev_charging_power_w)
# ===================================================================

class TestSolarEntry:
    def test_power_positive_enters_solar(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=6900))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 6900

    def test_power_zero_stays_idle(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0


class TestSolarStay:
    def test_power_positive_stays_solar(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_charging_power_w=4140))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 4140

    def test_power_zero_exits_to_idle(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_power_updates(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(ev_charging_power_w=6900))
        assert out.target_power_w == 6900
        out = sm.step(make_inputs(ev_charging_power_w=4140))
        assert out.target_power_w == 4140


# ===================================================================
# IDLE — stays
# ===================================================================

class TestIdleStays:
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

    def test_stays_solar_no_power(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=0))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0


# ===================================================================
# IDLE → transitions (N1, N2, N3)
# ===================================================================

class TestIdleTransitions:
    def test_n1_immediate(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_n1_immediate_no_wallbox_stays_idle(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_n2_cheap(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

    def test_n2_cheap_no_wallbox_stays_idle(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.IDLE

    def test_n3_solar_with_power(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_n1_has_priority_over_n2(self):
        """N1 fires before N2 when both could match."""
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE


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

    def test_solar_exits_to_idle_without_wallbox(self):
        """NO-01 regression: SOLAR must exit to IDLE when wallbox disappears."""
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_available=False, ev_charging_power_w=5000))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_cheap_exits_to_idle_without_wallbox(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False, is_cheap_tariff=True,
        ))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_immediate_exits_to_idle_without_wallbox(self):
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
    def test_solar_idle_exits_to_idle(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0
        assert "idle" in out.reason.lower()

    def test_cheap_idle_exits_to_idle(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="cheap", wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_immediate_idle_exits_to_idle(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="immediate", wallbox_idle=True))
        assert out.state == EVState.IDLE
        assert out.target_power_w == 0

    def test_solar_not_idle_stays(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=False, ev_charging_power_w=5000))
        assert out.state == EVState.SOLAR

    def test_idle_ignores_idle_flag(self):
        sm = make_sm(EVState.IDLE)
        out = sm.step(make_inputs(wallbox_idle=True, wallbox_available=False))
        assert out.state == EVState.IDLE


# ===================================================================
# Multi-step sequences
# ===================================================================

class TestMultiStep:
    def test_idle_to_solar_to_idle_on_power_stop(self):
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

    def test_full_mode_cycle(self):
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

    def test_solar_to_immediate_and_back(self):
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
