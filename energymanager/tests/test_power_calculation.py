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
        # Snap surplus to power step
        elif surplus_power_w >= threshold:
            ev_charging_source = "forecast"
            candidate_power = snap_to_power_step(surplus_power_w, min_power_w, max_power_w)

            if battery_will_be_full:
                # Case 1: battery will reach 100% — use snapped level directly
                ev_charging_power_w = candidate_power
            else:
                # Case 2: step down through discrete power steps
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
        assert source == "surplus"

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
        assert power == 3962  # highest step ≤ 4000 (6A)
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
        assert source == "forecast"

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
        assert source == "forecast"

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
        assert source == "forecast"

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
