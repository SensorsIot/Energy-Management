"""
Tests for 4-state EV charging state machine.
"""

import pytest

from src.ev_state_machine import (
    EVInputs,
    EVState,
    EVStateMachine,
    MIN_STAY_S,
    _round_to_step,
    _solar_target,
)


# ---------------------------------------------------------------------------
# Helper: build EVInputs with sane defaults
# ---------------------------------------------------------------------------

def make_inputs(**overrides) -> EVInputs:
    """Create EVInputs with sensible defaults for solar-mode happy path."""
    defaults = dict(
        wallbox_available=True,
        wallbox_power_w=0,
        wallbox_status="Preparing",
        wallbox_idle=False,
        battery_protection_passed=True,
        battery_soc=70.0,
        charging_mode="solar",
        is_cheap_tariff=False,
        grid_power_w=-5000.0,          # excess = -(-5000) + 0 = 5000
        min_power_w=1400.0,
        max_power_w=11000.0,
    )
    defaults.update(overrides)
    return EVInputs(**defaults)


_T0 = 1000.0  # arbitrary start time


def make_sm(state: EVState = EVState.NORMAL, *, now: float = _T0) -> EVStateMachine:
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
        assert isinstance(EVState.NORMAL, str)
        assert EVState.NORMAL == "normal"

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
        assert sm.state == EVState.NORMAL


# ===================================================================
# NORMAL — stays
# ===================================================================

class TestNormalStays:
    def test_stays_when_no_conditions(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            charging_mode="solar",
            wallbox_available=False,
        ))
        assert out.state == EVState.NORMAL
        assert out.target_power_w == 0

    def test_stays_solar_no_wallbox(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(wallbox_available=False))
        assert out.state == EVState.NORMAL

    def test_n3_solar_enters_without_battery_protection(self):
        """Solar enters even when battery_protection_passed=False."""
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            battery_protection_passed=False,
            battery_soc=70,
            grid_power_w=-5000,
        ))
        assert out.state == EVState.SOLAR

    def test_stays_solar_excess_below_min(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(grid_power_w=0, wallbox_power_w=0))
        assert out.state == EVState.NORMAL


# ===================================================================
# NORMAL → transitions (N1, N2, N3)
# ===================================================================

class TestNormalTransitions:
    def test_n1_immediate(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE
        assert out.target_power_w == 11000

    def test_n1_immediate_no_wallbox_stays_normal(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.NORMAL

    def test_n2_cheap(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

    def test_n2_cheap_no_wallbox_stays_normal(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.NORMAL

    def test_n3_solar_with_excess(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            grid_power_w=-5000, wallbox_power_w=0,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_n3_solar_ignores_battery_protection(self):
        """N3 enters SOLAR regardless of battery_protection_passed."""
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            battery_protection_passed=False,
            battery_soc=50,
            grid_power_w=-5000,
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_n1_has_priority_over_n2(self):
        """N1 fires before N2 when both could match."""
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE


# ===================================================================
# SOLAR — stays
# ===================================================================

class TestSolarStays:
    def test_stays_with_excess(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(grid_power_w=-5000))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 5000

    def test_stays_with_low_excess_holds_minimum(self):
        """When excess < min_power_w, output min_power_w (hold minimum)."""
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(grid_power_w=-500, wallbox_power_w=0))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1400  # min_power_w

    def test_stays_solar_without_battery_protection(self):
        """Stays in SOLAR indefinitely regardless of protection status."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + MIN_STAY_S + 3600  # well past min-stay
        out = sm.step(make_inputs(
            battery_protection_passed=False, battery_soc=70,
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
        assert out.state == EVState.NORMAL

    def test_c2_mode_changed_to_immediate(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.NORMAL


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
            charging_mode="cheap", is_cheap_tariff=True, max_power_w=7400,
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
            charging_mode="immediate", max_power_w=7400,
        ))
        assert out.target_power_w == 7400


# ===================================================================
# IMMEDIATE → transitions (M1, M2)
# ===================================================================

class TestMaxTransitions:
    def test_m2_mode_changed_to_solar(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.NORMAL

    def test_m2_mode_changed_to_cheap(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.NORMAL


# ===================================================================
# Min-stay timer (SOLAR)
# ===================================================================

class TestMinStayTimer:
    def test_hold_minimum_during_low_excess(self):
        """During min-stay with low excess, hold min_power_w."""
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm._time_fn = lambda: _T0 + 60  # 1 min in
        out = sm.step(make_inputs(
            grid_power_w=-500, wallbox_power_w=0,  # excess = 500 < 1400
        ))
        assert out.state == EVState.SOLAR
        assert out.target_power_w == 1400

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
        sm.step(make_inputs(grid_power_w=-5000))
        assert sm.state == EVState.SOLAR
        assert sm._entered_solar_at == _T0

    def test_entered_at_cleared_on_exit(self):
        sm = make_sm(EVState.SOLAR, now=_T0)
        sm.step(make_inputs(charging_mode="immediate"))
        assert sm.state == EVState.IMMEDIATE
        assert sm._entered_solar_at is None


# ===================================================================
# Power clamping & rounding (SOLAR)
# ===================================================================

class TestSolarPower:
    def test_clamp_to_min(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(grid_power_w=-1400, wallbox_power_w=0))
        assert out.target_power_w == 1400

    def test_clamp_to_max(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(grid_power_w=-15000, wallbox_power_w=0))
        assert out.target_power_w == 11000

    def test_round_to_step(self):
        sm = make_sm(EVState.SOLAR)
        # excess = 1680 → round(1680/100)*100 = 1700
        out = sm.step(make_inputs(grid_power_w=-1680, wallbox_power_w=0))
        assert out.target_power_w == 1700

    def test_excess_includes_wallbox_power(self):
        """excess = -grid_power_w + wallbox_power_w."""
        sm = make_sm(EVState.SOLAR)
        # grid=-2000, wallbox=3000 → excess = 2000+3000 = 5000
        out = sm.step(make_inputs(grid_power_w=-2000, wallbox_power_w=3000))
        assert out.target_power_w == 5000

    def test_low_excess_holds_minimum(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(grid_power_w=-500, wallbox_power_w=0))
        assert out.target_power_w == 1400

    def test_custom_min(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(
            grid_power_w=-2000, wallbox_power_w=0, min_power_w=2500,
        ))
        # excess=2000 < min=2500 → hold minimum
        assert out.target_power_w == 2500

    def test_custom_max(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(
            grid_power_w=-10000, wallbox_power_w=0, max_power_w=8000,
        ))
        assert out.target_power_w == 8000


# ===================================================================
# _round_to_step helper
# ===================================================================

class TestRoundToStep:
    def test_round_down(self):
        assert _round_to_step(1449) == 1400

    def test_round_up(self):
        assert _round_to_step(1451) == 1500

    def test_exact(self):
        assert _round_to_step(1500) == 1500

    def test_mid(self):
        assert _round_to_step(1550) == 1600


# ===================================================================
# _solar_target helper
# ===================================================================

class TestSolarTarget:
    def test_above_min(self):
        assert _solar_target(5000, 1400, 11000) == 5000

    def test_below_min_returns_min(self):
        assert _solar_target(500, 1400, 11000) == 1400

    def test_above_max_clamps(self):
        assert _solar_target(15000, 1400, 11000) == 11000


# ===================================================================
# Multi-step sequences
# ===================================================================

class TestMultiStep:
    def test_normal_to_solar_to_normal_on_mode_change(self):
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])

        # Start NORMAL
        out = sm.step(make_inputs(grid_power_w=0))  # no excess
        assert out.state == EVState.NORMAL

        # Excess appears → SOLAR
        out = sm.step(make_inputs(grid_power_w=-5000))
        assert out.state == EVState.SOLAR

        # Mode change to cheap → CHEAP → mode back to solar → NORMAL
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP
        out = sm.step(make_inputs(charging_mode="solar", grid_power_w=0))
        assert out.state == EVState.NORMAL

    def test_full_mode_cycle(self):
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])

        # NORMAL → IMMEDIATE
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

        # IMMEDIATE → NORMAL (mode changed)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.NORMAL

        # NORMAL → CHEAP
        out = sm.step(make_inputs(charging_mode="cheap"))
        assert out.state == EVState.CHEAP

        # CHEAP → NORMAL (mode changed)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.NORMAL

        # NORMAL → SOLAR
        out = sm.step(make_inputs(grid_power_w=-5000))
        assert out.state == EVState.SOLAR

    def test_solar_to_immediate_and_back(self):
        t = [_T0]
        sm = EVStateMachine(time_fn=lambda: t[0])

        # → SOLAR
        out = sm.step(make_inputs(grid_power_w=-5000))
        assert out.state == EVState.SOLAR

        # → IMMEDIATE
        out = sm.step(make_inputs(charging_mode="immediate"))
        assert out.state == EVState.IMMEDIATE

        # → NORMAL (mode back to solar)
        out = sm.step(make_inputs(charging_mode="solar"))
        assert out.state == EVState.NORMAL

        # → SOLAR again (excess available)
        out = sm.step(make_inputs(grid_power_w=-5000))
        assert out.state == EVState.SOLAR


# ===================================================================
# wallbox_available
# ===================================================================

class TestWallboxAvailable:
    def test_immediate_blocked_without_wallbox(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            charging_mode="immediate", wallbox_available=False,
        ))
        assert out.state == EVState.NORMAL

    def test_cheap_blocked_without_wallbox(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(
            charging_mode="cheap", wallbox_available=False,
        ))
        assert out.state == EVState.NORMAL

    def test_solar_blocked_without_wallbox(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(wallbox_available=False))
        assert out.state == EVState.NORMAL


# ===================================================================
# Idle detection (wallbox_idle exits to NORMAL)
# ===================================================================

class TestIdleDetection:
    def test_solar_idle_exits_to_normal(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=True))
        assert out.state == EVState.NORMAL
        assert out.target_power_w == 0
        assert "idle" in out.reason.lower()

    def test_cheap_idle_exits_to_normal(self):
        sm = make_sm(EVState.CHEAP)
        out = sm.step(make_inputs(charging_mode="cheap", wallbox_idle=True))
        assert out.state == EVState.NORMAL
        assert out.target_power_w == 0

    def test_immediate_idle_exits_to_normal(self):
        sm = make_sm(EVState.IMMEDIATE)
        out = sm.step(make_inputs(charging_mode="immediate", wallbox_idle=True))
        assert out.state == EVState.NORMAL
        assert out.target_power_w == 0

    def test_solar_not_idle_stays(self):
        sm = make_sm(EVState.SOLAR)
        out = sm.step(make_inputs(wallbox_idle=False))
        assert out.state == EVState.SOLAR

    def test_normal_ignores_idle(self):
        sm = make_sm(EVState.NORMAL)
        out = sm.step(make_inputs(wallbox_idle=True, wallbox_available=False))
        assert out.state == EVState.NORMAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
