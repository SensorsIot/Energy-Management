"""Tests for EV Charging Power Calculation (FSD 4.3.6-4.3.7).

The power calculation lives in run.py control_ev_charging(). In solar mode it:
  1. Battery full (SOC >= 100) → snap surplus to a step (grid-export capture).
  2. Otherwise, when surplus >= threshold, builds the Topic 2 candidate list
     (build_solar_candidates) and takes the highest candidate. The home-battery
     protection is Rule 4 (will_battery_hit_full → target_reachable: no
     candidates when the battery can't reach its target) plus the Topic 2
     step-up floor (step_up_allowed). There is no per-power 48 h-forecast veto.

These tests verify that selection in isolation.
"""

from __future__ import annotations

from src.ev_charging import snap_to_power_step, build_solar_candidates


def compute_ev_charging_power(
    *,
    ev_mode: str,
    surplus_power_w: float,
    threshold: float,
    min_power_w: float = 3962,
    max_power_w: float = 7624,
    battery_soc: float = 50.0,
    no_buy_floor: float = 20.0,
    min48h: float = 100.0,
    will_be_full: bool = True,
) -> tuple[float, str]:
    """Replicate the FSD 4.3.6-4.3.7 power calculation from run.py.

    - Rule 1 (Battery Full): SOC >= 100 and surplus >= threshold → snap, no gate.
    - Otherwise: surplus >= threshold builds the Topic 2 candidate list, gated by
      Rule 4 (`will_be_full` → target reachable) and the step-up floor
      (`min48h` and `battery_soc` both >= `no_buy_floor`). The highest candidate
      is taken; an empty list (target unreachable) means no charging.
    """
    if ev_mode != "solar":
        return 0.0, "none"

    # Rule 1: Battery Full — surplus capture, no home-battery gate.
    if battery_soc >= 100 and surplus_power_w >= threshold:
        return snap_to_power_step(surplus_power_w, min_power_w, max_power_w), "battery_full"

    if surplus_power_w < threshold:
        return 0.0, "none"

    candidate_power = snap_to_power_step(surplus_power_w, min_power_w, max_power_w)
    step_up_allowed = min48h >= no_buy_floor and battery_soc >= no_buy_floor
    candidates, _ = build_solar_candidates(
        candidate_power=candidate_power,
        threshold=threshold,
        step_up_allowed=step_up_allowed,
        target_reachable=will_be_full,
    )
    if candidates:
        return candidates[0], "solar_surplus"
    return 0.0, "none"


class TestRule1BatteryFull:
    """Rule 1: battery_soc >= 100 AND surplus >= threshold → snap, no gate."""

    def test_battery_full_surplus_above_threshold(self) -> None:
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

    def test_battery_full_surplus_below_threshold(self) -> None:
        """Battery 100%, surplus below threshold → no charging."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=800,
            battery_soc=100,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_battery_full_ignores_target_gate(self) -> None:
        """Rule 1 doesn't consult Rule 4 — the battery is already full."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5000,
            battery_soc=100,
            will_be_full=False,
            threshold=1400,
        )
        assert power == 4354
        assert source == "battery_full"


class TestRule4TargetGate:
    """Rule 4: the car may charge only while the battery still reaches target."""

    def test_target_reachable_charges(self) -> None:
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            will_be_full=True,
            threshold=1400,
        )
        # 4000W → candidate 3962, snap-up 4354 (step-up allowed) → 4354
        assert power == 4354
        assert source == "solar_surplus"

    def test_target_unreachable_blocks(self) -> None:
        """Battery can no longer reach target → no candidates → car stops."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4000,
            will_be_full=False,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_surplus_below_threshold(self) -> None:
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=1000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestNonSolarMode:
    def test_immediate_mode_returns_zero(self) -> None:
        power, source = compute_ev_charging_power(
            ev_mode="immediate",
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_cheap_mode_returns_zero(self) -> None:
        power, source = compute_ev_charging_power(
            ev_mode="cheap",
            surplus_power_w=5000,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestStepUpFloor:
    """The only draining step (one above surplus) needs the step-up floor."""

    def test_step_up_when_protected(self) -> None:
        """Surplus 5200W, SOC & min48h above floor → snap-up 5117→5727."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_soc=50,
            min48h=50,
            threshold=1400,
        )
        assert power == 5727
        assert source == "solar_surplus"

    def test_no_step_up_below_floor_soc(self) -> None:
        """SOC below the floor → step-up suppressed, snap-down only."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_soc=15.0,  # below the 20% floor
            min48h=50,
            threshold=1400,
        )
        assert power == 5117  # snap-down only — no snap-up to 5727
        assert source == "solar_surplus"

    def test_no_step_up_below_floor_forecast(self) -> None:
        """48h forecast below the floor → step-up suppressed."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_soc=50,
            min48h=15.0,  # forecast below floor
            threshold=1400,
        )
        assert power == 5117
        assert source == "solar_surplus"

    def test_step_up_at_floor(self) -> None:
        """SOC and forecast exactly at the floor → step-up still allowed."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=5200,
            battery_soc=20.0,
            min48h=20.0,
            threshold=1400,
        )
        assert power == 5727

    def test_at_or_below_surplus_always_charges(self) -> None:
        """Even with step-up suppressed, the EV still charges at/below surplus."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_power_w=4400,
            battery_soc=15.0,  # step-up suppressed
            threshold=1400,
        )
        # 4400W → snap-down 4354 (7A); no drain, so no floor needed
        assert power == 4354
        assert source == "solar_surplus"
