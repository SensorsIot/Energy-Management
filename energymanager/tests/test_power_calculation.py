"""
Tests for EV Charging Power Calculation (FSD 4.5.6).

The power calculation lives in run.py control_ev_charging() and selects
between surplus capture and snap-to-power-step, applying threshold and
battery protection rules. These tests verify the logic in isolation.
"""

from __future__ import annotations

from typing import Callable

from src.ev_charging import snap_to_power_step, POWER_STEPS_3P


def compute_ev_charging_power(
    *,
    ev_mode: str,
    surplus_capture_power_w: float,
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
      Rule 1 (Battery Full): battery_soc >= 100 — use grid export or surplus
      Rule 2 (Solar Surplus): battery still charging — battery protection gate

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
        # Rule 1: surplus capture (grid export — energy is being wasted)
        if surplus_capture_power_w >= threshold:
            ev_charging_power_w = surplus_capture_power_w
            ev_charging_source = "battery_full"
        # Rule 2 candidate: surplus above threshold
        elif surplus_power_w >= threshold:
            ev_charging_source = "solar_surplus"

        # Battery gate: compute power based on battery state
        if battery_soc >= 100:
            # Rule 1: Battery full — no protection needed
            if ev_charging_source == "solar_surplus":
                candidate_power = snap_to_power_step(
                    surplus_power_w, min_power_w, max_power_w
                )
                ev_charging_power_w = candidate_power
        elif ev_charging_source == "solar_surplus":
            # Rule 2: Solar Surplus Charging — battery protection gate
            candidate_power = snap_to_power_step(
                surplus_power_w, min_power_w, max_power_w
            )
            if battery_will_be_full:
                # Battery will reach 100% today — use candidate directly
                ev_charging_power_w = candidate_power
            else:
                # Step down through discrete power steps
                candidates = [
                    s for s in reversed(POWER_STEPS_3P)
                    if s <= candidate_power and s >= threshold
                ]
                for try_power in candidates:
                    ev_load_wh = try_power * 0.25
                    reaches_target, battery_will_hit_min = battery_check_fn(ev_load_wh)
                    if reaches_target and not battery_will_hit_min:
                        ev_charging_power_w = try_power
                        break

        if ev_charging_power_w == 0.0:
            ev_charging_source = "none"

    return ev_charging_power_w, ev_charging_source


class TestSurplusPriority:
    """Surplus capture has priority over snap-to-power-step (FSD Rule 1)."""

    def test_surplus_above_threshold_wins(self):
        """Surplus above threshold wins even when snap would produce power."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1610,
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 1610
        assert source == "battery_full"

    def test_surplus_below_threshold_uses_snap(self):
        """Surplus capture below threshold, but surplus_power above → snap to power step."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1000,
            surplus_power_w=4000,
            threshold=1400,
        )
        # 4000W → highest step ≤ 4000 = 3962W (6A)
        assert power == 3962
        assert source == "solar_surplus"


class TestSnapWithProtection:
    """Snap path requires surplus >= threshold and battery protection (FSD Rule 2)."""

    def test_snap_surplus_above_protection_passed(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=4000,
            threshold=1400,
        )
        assert power == 3962  # highest step ≤ 4000 (6A)
        assert source == "solar_surplus"

    def test_snap_protection_failed(self):
        """Battery protection blocks snap → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=4000,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_snap_surplus_below_threshold(self):
        """Surplus power below threshold → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=1000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestBothBelowThreshold:
    def test_both_below_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=500,
            surplus_power_w=800,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestNonSolarMode:
    def test_immediate_mode_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="immediate",
            surplus_capture_power_w=2000,
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_cheap_mode_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="cheap",
            surplus_capture_power_w=2000,
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestSurplusBypassesProtection:
    """Rule 1 (Battery Full) doesn't need battery protection — it's free energy."""

    def test_surplus_works_without_protection(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1610,
            surplus_power_w=1610,
            battery_soc=100,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        assert power == 1610
        assert source == "battery_full"


class TestStepDown:
    """Step-down loop: if candidate power step fails battery checks, try lower steps."""

    def test_step_down_finds_lower_level(self):
        """Candidate 5117W (8A) fails, but 4354W (7A) passes."""
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5000:
                return False, False  # 5117W fails
            return True, False  # 4354W passes

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
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
            surplus_capture_power_w=0,
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
            surplus_capture_power_w=0,
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
            surplus_capture_power_w=0,
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
            surplus_capture_power_w=0,
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
            surplus_capture_power_w=0,
            surplus_power_w=7100,
            battery_check_fn=check,
            threshold=1400,
        )
        # 7034 fail, 6288 fail, 5727 fail, 5117 pass
        assert power == 5117  # 8A
        assert source == "solar_surplus"

    def test_battery_will_hit_min_blocks_even_if_reaches_target(self):
        """will_battery_hit_minimum blocks charging even when reaches_target is True."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5000,
            battery_check_fn=lambda _: (True, True),  # reaches target but hits min
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestBatteryFullSurplusPath:
    """Rule 1: Battery full — charge from surplus even when grid capture fails.

    Regression tests for v1.6.85 fix: when battery_soc=100% and surplus
    capture fails (e.g. stale M-Bus meter), the surplus path must still
    compute charging power via snap_to_power_step().
    """

    def test_battery_full_stale_meter_uses_surplus(self):
        """Battery 100%, grid capture=0 (stale meter), surplus=5000W → charge.

        Entry path is via surplus, but battery gate treats it as Rule 1
        (battery full — no protection needed). The key assertion is that
        power is NOT 0 (the v1.6.85 bug).
        """
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,  # M-Bus stale → no grid export
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        # 5000W → snap to 4354W (7A)
        assert power == 4354
        assert source == "solar_surplus"  # entry via surplus, promoted by battery gate

    def test_battery_full_grid_capture_active(self):
        """Battery 100%, grid capture=5000W → use grid capture directly."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=5000,
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 5000
        assert source == "battery_full"

    def test_battery_full_both_below_threshold(self):
        """Battery 100% but both sources below threshold → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=500,
            surplus_power_w=800,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_battery_not_full_stale_meter_needs_protection(self):
        """Battery 50%, grid capture=0, surplus=5000W → Rule 2 with battery check."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5000,
            battery_soc=50,
            battery_will_be_full=False,
            battery_check_fn=lambda _: (True, False),  # protection passes
            threshold=1400,
        )
        # 5000W → snap to 4354W (7A), battery check passes
        assert power == 4354
        assert source == "solar_surplus"

    def test_battery_not_full_stale_meter_protection_blocks(self):
        """Battery 50%, grid capture=0, surplus=5000W → Rule 2, protection blocks."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5000,
            battery_soc=50,
            battery_will_be_full=False,
            battery_check_fn=lambda _: (False, False),  # protection fails
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestCarPluggedInWithSurplus:
    """Goal 1+2: Car plugged in while surplus already available.

    Regression for the 2026-03-20 incident: car connected with 5kW surplus
    and battery at 100%, but charging never started.
    """

    def test_first_cycle_battery_full_grid_export(self):
        """Battery full, 5kW grid export → Rule 1 charges immediately."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=5000,
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 5000
        assert source == "battery_full"

    def test_first_cycle_battery_full_no_grid_export(self):
        """Battery full, meter stale (no grid export), surplus 5kW → still charges."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5000,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 4354  # 7A step
        assert power > 0, "Must not be 0 — this was the v1.6.85 bug"

    def test_first_cycle_battery_charging_protection_passes(self):
        """Battery 60%, surplus 5kW, protection passes → Rule 2 charges."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
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
        """Simulate morning: surplus rises from 0 → 3500 → 5000 → 6500 → 7800W."""
        levels = [0, 3500, 5000, 6500, 7800]
        results = []
        for surplus in levels:
            # Grid export tracks surplus when battery is full
            power, _ = compute_ev_charging_power(
                ev_mode="solar",
                surplus_capture_power_w=surplus,
                surplus_power_w=surplus,
                battery_soc=100,
                threshold=1400,
            )
            results.append(power)

        # 0W → below threshold
        assert results[0] == 0
        # 3500W → snap to 3962W (min step, export >= threshold)
        assert results[1] == 3500  # grid capture uses raw export
        # 5000W → grid capture at 5000W
        assert results[2] == 5000
        # 6500W → grid capture at 6500W
        assert results[3] == 6500
        # 7800W → grid capture at 7800W (above max step but grid capture clamps elsewhere)
        assert results[4] == 7800
        # Power must be non-decreasing as surplus rises
        assert results == sorted(results)

    def test_step_increases_via_surplus_snap(self):
        """Rule 2 path: surplus rises, battery not full but will be full."""
        levels = [0, 3500, 5000, 6500, 7800]
        results = []
        for surplus in levels:
            power, _ = compute_ev_charging_power(
                ev_mode="solar",
                surplus_capture_power_w=0,  # no grid capture
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
            surplus_capture_power_w=0,
            surplus_power_w=5500,
            battery_soc=99,
            battery_will_be_full=True,
            threshold=1400,
        )
        # Cycle 2: battery 100%, Rule 1
        power_after, source_after = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=5500,  # now grid exports since battery full
            surplus_power_w=5500,
            battery_soc=100,
            threshold=1400,
        )
        # Both must charge (no gap during transition)
        assert power_before > 0
        assert power_after > 0
        assert source_before == "solar_surplus"
        assert source_after == "battery_full"

    def test_rule2_to_rule1_stale_meter(self):
        """Battery 99% → 100% but meter stale — must not drop to 0W."""
        power_before, _ = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5500,
            battery_soc=99,
            battery_will_be_full=True,
            threshold=1400,
        )
        # Battery hits 100% but meter still stale
        power_after, _ = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,  # still stale
            surplus_power_w=5500,
            battery_soc=100,
            threshold=1400,
        )
        assert power_before > 0
        assert power_after > 0, "Must not drop to 0W during battery transition"


class TestThresholdBoundary:
    """Goal 4: Single threshold gates all charging — boundary conditions."""

    def test_surplus_exactly_at_threshold(self):
        """Surplus == threshold → should charge (>= check, not >)."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=1400,
            battery_soc=100,
            threshold=1400,
        )
        assert power > 0

    def test_surplus_one_below_threshold(self):
        """Surplus just below threshold → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=1399,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_grid_export_exactly_at_threshold(self):
        """Grid export == threshold → should charge."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1400,
            surplus_power_w=1400,
            battery_soc=100,
            threshold=1400,
        )
        assert power > 0
        assert source == "battery_full"

    def test_grid_export_one_below_threshold(self):
        """Grid export just below threshold, surplus also below → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1399,
            surplus_power_w=1399,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"
