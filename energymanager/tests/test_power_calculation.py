"""
Tests for EV Charging Power Calculation (FSD 4.5.6).

The power calculation lives in run.py control_ev_charging() and selects
between surplus capture and snap-to-amp-step, applying threshold and
battery protection rules. These tests verify the logic in isolation.
"""

from __future__ import annotations

from typing import Callable

from src.ev_charging import snap_to_amp_step


def compute_ev_charging_power(
    *,
    ev_mode: str,
    surplus_capture_power_w: float,
    surplus_power_w: float,
    threshold: float,
    min_amps: int = 6,
    max_amps: int = 16,
    phases: int = 3,
    battery_will_be_full: bool = False,
    battery_check_fn: Callable[[float], tuple[bool, bool]] | None = None,
) -> tuple[float, str]:
    """Replicate the FSD 4.5.6 power calculation from run.py.

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
        # Rule 1: surplus capture has priority (exported energy is wasted)
        if surplus_capture_power_w >= threshold:
            ev_charging_power_w = surplus_capture_power_w
            ev_charging_source = "surplus"
        # Snap surplus to amp step
        elif surplus_power_w >= threshold:
            ev_charging_source = "forecast"
            candidate_amps = snap_to_amp_step(surplus_power_w, min_amps, max_amps, phases)

            if battery_will_be_full:
                # Case 1: battery will reach 100% — use snapped level directly
                ev_charging_power_w = candidate_amps * 230 * phases
            else:
                # Case 2: step down until battery checks pass
                # or power drops below ev_min_solar_power
                for try_amps in range(candidate_amps, 0, -1):
                    try_power = try_amps * 230 * phases
                    if try_power < threshold:
                        break
                    ev_load_wh = try_power * 0.25
                    reaches_target, battery_will_hit_min = battery_check_fn(ev_load_wh)
                    if reaches_target and not battery_will_hit_min:
                        ev_charging_power_w = try_power
                        break

            if ev_charging_power_w == 0.0:
                ev_charging_source = "none"

    return ev_charging_power_w, ev_charging_source


class TestSurplusPriority:
    """Surplus capture has priority over snap-to-amp (FSD Rule 1)."""

    def test_surplus_above_threshold_wins(self):
        """Surplus above threshold wins even when snap would produce power."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1610,
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 1610
        assert source == "surplus"

    def test_surplus_below_threshold_uses_snap(self):
        """Surplus capture below threshold, but surplus_power above → snap to amp step."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1000,
            surplus_power_w=4000,
            threshold=1400,
        )
        # 4000W / 690 = 5.8 → ceil = 6A → 6 × 230 × 3 = 4140W
        assert power == 4140
        assert source == "forecast"


class TestSnapWithProtection:
    """Snap path requires surplus >= threshold and battery protection (FSD Rule 2)."""

    def test_snap_surplus_above_protection_passed(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=4000,
            threshold=1400,
        )
        assert power == 4140  # 6A × 230 × 3
        assert source == "forecast"

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
    """Surplus capture doesn't need battery protection — it's free energy."""

    def test_surplus_works_without_protection(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1610,
            surplus_power_w=1610,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        assert power == 1610
        assert source == "surplus"


class TestStepDown:
    """Step-down loop: if candidate amp level fails battery checks, try lower levels."""

    def test_step_down_finds_lower_level(self):
        """Candidate 8A fails, but 7A passes → use 7A."""
        # surplus 5500W → snap = ceil(5500/690) = 8A
        # 8A = 5520W fails, 7A = 4830W passes
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5000:
                return False, False  # 8A fails reaches_target
            return True, False  # 7A and below pass

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5500,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 4830  # 7A × 230 × 3
        assert source == "forecast"

    def test_battery_full_skips_step_down(self):
        """Battery will be full → first iteration wins regardless of protection."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5500,
            battery_will_be_full=True,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
        )
        # 5500W → snap = 8A = 5520W, battery full → first iteration wins
        assert power == 5520  # 8A × 230 × 3
        assert source == "forecast"

    def test_all_levels_fail(self):
        """All amp levels fail battery checks → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=5500,
            battery_check_fn=lambda _: (False, True),  # always hits minimum
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_floor_at_threshold(self):
        """Step-down stops when power drops below ev_min_solar_power threshold."""
        # surplus 4200W → snap = ceil(4200/690) = 7A = 4830W
        # 7A fails, 6A = 4140W → still above 1400 threshold → checked
        # But if check fails too, we stop (next would be 5A = 3450W, still above,
        # but let's make 6A pass)
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 4200:
                return False, False  # 7A fails
            return True, False  # 6A passes

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=4200,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 4140  # 6A × 230 × 3
        assert source == "forecast"

    def test_floor_prevents_charging_below_threshold(self):
        """All levels above threshold fail → 0W even though lower levels would pass."""
        # surplus 1500W, threshold 1400 → snap = ceil(1500/690) = 3A = 2070W
        # But min_amps clamps to 6A = 4140W. Make 6A fail.
        # Step down: 5A = 3450W > 1400 but fails, 4A = 2760W > 1400 fails,
        # 3A = 2070W > 1400 fails, 2A = 1380W < 1400 → break (floor)
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=4000,
            battery_check_fn=lambda _: (False, False),
            threshold=1400,
            min_amps=6,
        )
        assert power == 0.0
        assert source == "none"

    def test_step_down_multiple_levels(self):
        """Step down skips several levels before finding one that passes."""
        # surplus 7000W → snap = ceil(7000/690) = 11A = 7590W
        # Only 8A and below pass
        def check(ev_load_wh: float) -> tuple[bool, bool]:
            power = ev_load_wh / 0.25
            if power > 5600:
                return False, False
            return True, False

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            surplus_power_w=7000,
            battery_check_fn=check,
            threshold=1400,
        )
        # 11A=7590 fail, 10A=6900 fail, 9A=6210 fail, 8A=5520 pass
        assert power == 5520  # 8A × 230 × 3
        assert source == "forecast"

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
