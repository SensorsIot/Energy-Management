"""
Tests for EV Charging Power Calculation (FSD 4.5.6).

The power calculation lives in run.py control_ev_charging() and selects
between Rule 1 (battery full) and Rule 2 (solar surplus with battery
protection). Both rules use surplus_power (PV - house load) as input.
These tests verify the logic in isolation.
"""

from __future__ import annotations

from typing import Callable

from src.ev_charging import snap_to_power_step, POWER_STEPS_3P


def compute_ev_charging_power(
    *,
    ev_mode: str,
    surplus_power_w: float,
    threshold: float,
    min_power_w: float = 3962,
    max_power_w: float = 7624,
    battery_soc: float = 50.0,
    battery_will_be_full: bool = False,
    battery_check_fn: Callable[[float], tuple[bool, bool]] | None = None,
) -> tuple[float, str]:
    """Replicate the FSD 4.6.6 power calculation from run.py.

    Two rules based on battery state:
      Rule 1 (Battery Full): battery_soc >= 100 AND surplus >= threshold
      Rule 2 (Solar Surplus): battery still charging — battery protection gate

    Both rules use surplus_power (PV - house load) as the input signal.

    battery_check_fn(ev_load_wh) -> (reaches_target, battery_will_hit_min)
    If None, defaults to (True, False) for all loads (always passes).
    """
    def _always_pass(_: float) -> tuple[bool, bool]:
        return True, False

    if battery_check_fn is None:
        battery_check_fn = _always_pass

    ev_charging_power_w = 0.0
    ev_charging_source = "none"
    if ev_mode == "solar":
        # Rule 1: Battery Full — surplus capture (no battery check)
        if battery_soc >= 100 and surplus_power_w >= threshold:
            ev_charging_power_w = snap_to_power_step(
                surplus_power_w, min_power_w, max_power_w
            )
            ev_charging_source = "battery_full"
        # Rule 2 candidate: surplus above threshold (needs battery check)
        elif surplus_power_w >= threshold:
            ev_charging_source = "solar_surplus"

        # Battery gate: Rule 2 only (Rule 1 already set power above)
        if ev_charging_source == "solar_surplus":
            # Rule 2: Solar Surplus Charging — battery protection gate
            candidate_power = snap_to_power_step(
                surplus_power_w, min_power_w, max_power_w
            )
            if battery_will_be_full:
                # Battery will reach 100% today — use candidate directly
                ev_charging_power_w = candidate_power
            else:
                # Try snap-up first (next step above candidate), then
                # step down through discrete power steps
                snap_up = [
                    s for s in POWER_STEPS_3P
                    if s > candidate_power and s >= threshold
                ]
                snap_up_step = [snap_up[0]] if snap_up else []
                snap_down = [
                    s for s in reversed(POWER_STEPS_3P)
                    if s <= candidate_power and s >= threshold
                ]
                candidates = snap_up_step + snap_down
                for try_power in candidates:
                    ev_load_wh = try_power * 0.25
                    reaches_target, battery_will_hit_min = battery_check_fn(ev_load_wh)
                    if reaches_target and not battery_will_hit_min:
                        ev_charging_power_w = try_power
                        break

        if ev_charging_power_w == 0.0:
            ev_charging_source = "none"

    return ev_charging_power_w, ev_charging_source


class TestRule1BatteryFull:
    """Rule 1: battery_soc >= 100 AND surplus >= threshold → snap, no protection."""

    def test_battery_full_surplus_above_threshold(self):
        """Battery 100%, surplus above threshold → Rule 1 charges."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        # 5000W → snap to 4354W (7A)
        assert power == 4354
        assert source == "battery_full"

    def test_battery_full_surplus_below_threshold(self):
        """Battery 100%, surplus below threshold → no charging."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=800,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_battery_full_ignores_protection(self):
        """Rule 1 doesn't need battery protection — it's free energy."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=100,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        assert power == 4354
        assert source == "battery_full"


class TestRule2SolarSurplus:
    """Rule 2: surplus >= threshold, battery not full — needs battery protection."""

    def test_surplus_above_threshold_protection_passes(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            threshold=1400,
        )
        # 4000W → snap-down 3962W (6A), snap-up 4354W (7A) passes → 4354
        assert power == 4354
        assert source == "solar_surplus"

    def test_protection_blocks(self):
        """Battery protection blocks snap → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_surplus_below_threshold(self):
        """Surplus power below threshold → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=1000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestBelowThreshold:
    def test_both_below_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=800,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestNonSolarMode:
    def test_immediate_mode_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="immediate",
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_cheap_mode_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="cheap",
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestStepDown:
    """Step-down loop: if candidate power step fails battery checks, try lower steps."""

    def test_step_down_finds_lower_level(self):
        """Candidate 5117W (8A) fails, but 4354W (7A) passes."""
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5000:
                return False, False  # 5727W and 5117W fail
            return True, False  # 4354W passes

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 4354  # 7A
        assert source == "solar_surplus"

    def test_battery_full_skips_step_down(self):
        """Battery will be full → snapped level wins regardless of protection."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_will_be_full=True,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        # 5200W → snap = 5117W (8A), battery full → use directly
        assert power == 5117
        assert source == "solar_surplus"

    def test_all_levels_fail(self):
        """All power steps fail battery checks → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=lambda _: (False, True),  # always hits minimum
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_floor_at_threshold(self):
        """Step-down finds lowest valid step that passes battery check."""
        # surplus 4400W → snap = 4354W (7A)
        # 4354W fails, but 3962W (6A) passes
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 4000:
                return False, False  # 4354W fails
            return True, False  # 3962W passes

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4400,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 3962  # 6A
        assert source == "solar_surplus"

    def test_floor_prevents_charging_below_threshold(self):
        """All valid steps fail → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_step_down_multiple_levels(self):
        """Step down skips several levels before finding one that passes."""
        # surplus 7100W → snap = 7034W (11A)
        # Only 5117W (8A) and below pass
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5200:
                return False, False
            return True, False

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=7100,
            battery_check_fn=check,
            threshold=1400,
        )
        # 7624 fail, 7034 fail, 6288 fail, 5727 fail, 5117 pass
        assert power == 5117  # 8A
        assert source == "solar_surplus"

    def test_battery_will_hit_min_blocks_even_if_reaches_target(self):
        """will_battery_hit_minimum blocks charging even when reaches_target is True."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_check_fn=lambda _: (True, True),  # reaches target but hits min
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestSnapUp:
    """Snap-up: try one step above surplus before stepping down.

    If battery protection passes at the higher step, use it — the battery
    covers the small gap between surplus and the next amp step.
    """

    def test_snap_up_when_protection_passes(self):
        """Surplus 5200W → snap-down 5117W, but snap-up 5727W passes → use 5727W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=lambda _: (True, False),  # all pass
            threshold=1400,
        )
        assert power == 5727  # snap-up: next step above 5117
        assert source == "solar_surplus"

    def test_snap_up_fails_falls_back_to_snap_down(self):
        """Snap-up 5727W fails protection, falls back to snap-down 5117W."""
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5200:
                return False, False  # 5727W fails
            return True, False  # 5117W passes

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 5117  # fell back to snap-down
        assert source == "solar_surplus"

    def test_snap_up_at_max_step_no_higher(self):
        """Surplus above highest step — no snap-up possible, use highest."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=8000,
            battery_check_fn=lambda _: (True, False),
            threshold=1400,
        )
        assert power == 7624  # already at max, no higher step
        assert source == "solar_surplus"

    def test_snap_up_hit_min_falls_back(self):
        """Snap-up fails because battery would hit minimum, falls back."""
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5200:
                return True, True  # reaches target but hits min
            return True, False

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 5117  # snap-up blocked, fell back
        assert source == "solar_surplus"

    def test_snap_up_skipped_when_battery_will_be_full(self):
        """Battery will be full → uses snap-down directly (no snap-up loop)."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_will_be_full=True,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        # battery_will_be_full path uses candidate directly, no snap-up
        assert power == 5117
        assert source == "solar_surplus"


class TestBatteryFullSurplusPath:
    """Rule 1: Battery full — both rules use surplus_power."""

    def test_battery_full_charges(self):
        """Battery 100%, surplus=5000W → Rule 1 charges at snapped level."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        # 5000W → snap to 4354W (7A)
        assert power == 4354
        assert source == "battery_full"

    def test_battery_full_below_threshold(self):
        """Battery 100% but surplus below threshold → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=800,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_battery_not_full_needs_protection(self):
        """Battery 50%, surplus=5000W → Rule 2 with battery check."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=50,
            battery_will_be_full=False,
            battery_check_fn=lambda _: (True, False),  # protection passes
            threshold=1400,
        )
        # 5000W → snap-down 4354W (7A), snap-up 5117W (8A) passes → 5117
        assert power == 5117
        assert source == "solar_surplus"

    def test_battery_not_full_protection_blocks(self):
        """Battery 50%, surplus=5000W → Rule 2, protection blocks."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=50,
            battery_will_be_full=False,
            battery_check_fn=lambda _: (False, False),  # protection fails
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestCarPluggedInWithSurplus:
    """Goal 1+2: Car plugged in while surplus already available."""

    def test_first_cycle_battery_full(self):
        """Battery full, 5kW surplus → Rule 1 charges immediately."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 4354
        assert source == "battery_full"

    def test_first_cycle_battery_charging_protection_passes(self):
        """Battery 60%, surplus 5kW, protection passes → Rule 2 charges."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=60,
            battery_will_be_full=True,
            threshold=1400,
        )
        assert power == 4354  # 7A step
        assert source == "solar_surplus"


class TestRisingSurplusRampUp:
    """Goal 1: Morning ramp-up — power steps increase as surplus rises."""

    def test_step_increases_with_surplus(self):
        """Rule 1: Battery full, surplus rises → snapped power increases."""
        levels = [0, 3500, 5000, 6500, 7800]
        results = []
        for surplus in levels:
            power, _ = compute_ev_charging_power(
                ev_mode="solar",
                surplus_power_w=surplus,
                battery_soc=100,
                threshold=1400,
            )
            results.append(power)

        # 0W → below threshold
        assert results[0] == 0
        # 3500W → snap to 3962W (min step, surplus below all steps)
        assert results[1] == 3962
        # 5000W → snap to 4354W (7A)
        assert results[2] == 4354
        # 6500W → snap to 6288W (10A)
        assert results[3] == 6288
        # 7800W → snap to 7624W (12A)
        assert results[4] == 7624
        # Power must be non-decreasing as surplus rises
        assert results == sorted(results)

    def test_step_increases_via_surplus_snap(self):
        """Rule 2 path: surplus rises, battery not full but will be full."""
        levels = [0, 3500, 5000, 6500, 7800]
        results = []
        for surplus in levels:
            power, _ = compute_ev_charging_power(
                ev_mode="solar",
                surplus_power_w=surplus,
                battery_soc=80,
                battery_will_be_full=True,
                threshold=1400,
            )
            results.append(power)

        # 0W → below threshold
        assert results[0] == 0
        # 3500W → snap to 3962W (min step, surplus below all steps)
        assert results[1] == 3962
        # 5000W → snap to 4354W (7A)
        assert results[2] == 4354
        # 6500W → snap to 6288W (10A)
        assert results[3] == 6288
        # 7800W → snap to 7624W (12A)
        assert results[4] == 7624
        # Power must be non-decreasing as surplus rises
        assert results == sorted(results)


class TestBatteryTransition:
    """Goal 2: Battery 99% → 100% transition — Rule 2 → Rule 1 switch."""

    def test_rule2_to_rule1_on_battery_full(self):
        """Same surplus, battery goes from 99% to 100% — charging continues."""
        # Cycle 1: battery 99%, Rule 2 with battery_will_be_full
        power_before, source_before = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5500,
            battery_soc=99,
            battery_will_be_full=True,
            threshold=1400,
        )
        # Cycle 2: battery 100%, Rule 1
        power_after, source_after = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5500,
            battery_soc=100,
            threshold=1400,
        )
        # Both must charge (no gap during transition)
        assert power_before > 0
        assert power_after > 0
        assert source_before == "solar_surplus"
        assert source_after == "battery_full"

    def test_rule2_to_rule1_continuity(self):
        """Battery 99% → 100% — same surplus produces same power."""
        power_before, _ = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5500,
            battery_soc=99,
            battery_will_be_full=True,
            threshold=1400,
        )
        power_after, _ = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5500,
            battery_soc=100,
            threshold=1400,
        )
        # Both use surplus → same snap result
        assert power_before == power_after


class TestThresholdBoundary:
    """Goal 4: Single threshold gates all charging — boundary conditions."""

    def test_surplus_exactly_at_threshold(self):
        """Surplus == threshold → should charge (>= check, not >)."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=1400,
            battery_soc=100,
            threshold=1400,
        )
        assert power > 0

    def test_surplus_one_below_threshold(self):
        """Surplus just below threshold → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=1399,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_rule2_exactly_at_threshold(self):
        """Rule 2: surplus == threshold → should charge."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=1400,
            battery_soc=50,
            threshold=1400,
        )
        assert power > 0
        assert source == "solar_surplus"

    def test_rule2_one_below_threshold(self):
        """Rule 2: surplus just below threshold → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=1399,
            battery_soc=50,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"
