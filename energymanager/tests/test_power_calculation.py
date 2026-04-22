"""
Tests for EV Charging Power Calculation (FSD 4.6).

The power calculation lives in run.py control_ev_charging() and selects
between Rule 1 (battery full) and Rule 2 (solar surplus with the 48-h
min-SOC safety check). Both rules use surplus_power (PV - house load)
as input. These tests verify the logic in isolation.
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
    battery_check_fn: Callable[[float], bool] | None = None,
) -> tuple[float, str]:
    """Replicate the FSD 4.6 power calculation from run.py.

    Two rules based on battery state:
      Rule 1 (Battery Full): battery_soc >= 100 AND surplus >= threshold
      Rule 2 (Solar Surplus): battery still charging — safety gate applies

    Both rules use surplus_power (PV - house load) as the input signal.

    battery_check_fn(ev_load_wh) -> safe
      True  → EV load is safe for the home battery over the next 48 h
      False → EV load would drop the battery below min_soc_percent

    If None, defaults to always-safe.
    """
    if battery_check_fn is None:
        def battery_check_fn(_: float) -> bool:  # type: ignore[no-redef]
            return True

    ev_charging_power_w = 0.0
    ev_charging_source = "none"
    if ev_mode == "solar":
        # Rule 1: Battery Full — surplus capture, no safety gate
        if battery_soc >= 100 and surplus_power_w >= threshold:
            ev_charging_power_w = snap_to_power_step(
                surplus_power_w, min_power_w, max_power_w
            )
            ev_charging_source = "battery_full"
        # Rule 2 candidate: surplus above threshold (needs safety gate)
        elif surplus_power_w >= threshold:
            ev_charging_source = "solar_surplus"

        # Rule 2 safety gate: iterate snap-up first, then step down
        if ev_charging_source == "solar_surplus":
            candidate_power = snap_to_power_step(
                surplus_power_w, min_power_w, max_power_w
            )
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
                if battery_check_fn(ev_load_wh):
                    ev_charging_power_w = try_power
                    break

        if ev_charging_power_w == 0.0:
            ev_charging_source = "none"

    return ev_charging_power_w, ev_charging_source


class TestRule1BatteryFull:
    """Rule 1: battery_soc >= 100 AND surplus >= threshold → snap, no gate."""

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

    def test_battery_full_ignores_safety_gate(self):
        """Rule 1 doesn't consult the safety gate — battery is already full."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=100,
            battery_check_fn=lambda _: False,
            threshold=1400,
        )
        assert power == 4354
        assert source == "battery_full"


class TestRule2SolarSurplus:
    """Rule 2: surplus >= threshold, battery not full — 48h safety gate applies."""

    def test_surplus_above_threshold_safety_passes(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            threshold=1400,
        )
        # 4000W → snap-down 3962W (6A), snap-up 4354W (7A) passes → 4354
        assert power == 4354
        assert source == "solar_surplus"

    def test_safety_blocks(self):
        """Safety gate fails at every step → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            battery_check_fn=lambda _: False,
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
    """Step-down loop: if candidate power step fails, try lower steps."""

    def test_step_down_finds_lower_level(self):
        """Candidate 5117W (8A) fails, but 4354W (7A) passes."""
        def check(ev_load_wh: float) -> bool:
            power = ev_load_wh / 0.25
            return power <= 5000  # 5727W and 5117W fail, 4354W passes

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=check,
            threshold=1400,
        )
        assert power == 4354  # 7A
        assert source == "solar_surplus"

    def test_all_levels_fail(self):
        """All power steps fail safety gate → 0W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=lambda _: False,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_floor_at_threshold(self):
        """Step-down finds lowest valid step that passes safety gate."""
        # surplus 4400W → snap = 4354W (7A)
        # 4354W fails, but 3962W (6A) passes
        def check(ev_load_wh: float) -> bool:
            return ev_load_wh / 0.25 <= 4000

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
            battery_check_fn=lambda _: False,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_step_down_multiple_levels(self):
        """Step down skips several levels before finding one that passes."""
        # surplus 7100W → snap = 7034W (11A)
        # Only 5117W (8A) and below pass
        def check(ev_load_wh: float) -> bool:
            return ev_load_wh / 0.25 <= 5200

        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=7100,
            battery_check_fn=check,
            threshold=1400,
        )
        # 7624 fail, 7034 fail, 6288 fail, 5727 fail, 5117 pass
        assert power == 5117  # 8A
        assert source == "solar_surplus"


class TestSnapUp:
    """Snap-up: try one step above surplus before stepping down.

    If the safety gate passes at the higher step, use it — the battery
    covers the small gap between surplus and the next amp step.
    """

    def test_snap_up_when_safety_passes(self):
        """Surplus 5200W → snap-down 5117W, but snap-up 5727W passes → use 5727W."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_check_fn=lambda _: True,  # all pass
            threshold=1400,
        )
        assert power == 5727  # snap-up: next step above 5117
        assert source == "solar_surplus"

    def test_snap_up_fails_falls_back_to_snap_down(self):
        """Snap-up 5727W fails safety, falls back to snap-down 5117W."""
        def check(ev_load_wh: float) -> bool:
            return ev_load_wh / 0.25 <= 5200  # 5727W fails, 5117W passes

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
            battery_check_fn=lambda _: True,
            threshold=1400,
        )
        assert power == 7624  # already at max, no higher step
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
        assert power == 4354
        assert source == "battery_full"
