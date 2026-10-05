"""Tests for EV charging amp-step selection.

The wallbox is commanded in amps, so the steps are amp levels and the single
watts-per-amp factor is only used to compare a step against solar surplus.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest

from src.ev_charging import (
    amp_steps,
    build_solar_candidates,
    simulate_house_and_car,
    snap_to_amp_step,
    solar_start_threshold,
    step_watts,
)

W_PER_A_1P = 230
W_PER_A_3P = 637
LADDER = amp_steps(6, 16)


# --- the amp ladder itself ---


class TestAmpSteps:
    def test_every_whole_amp_is_a_step(self) -> None:
        """6..16 A inclusive — no gaps, nothing unreachable."""
        assert LADDER == [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

    def test_three_phase_reaches_sixteen_amps(self) -> None:
        """Regression: the old 3φ watt table stopped at 12 A (7624 W), so roughly
        2.6 kW of surplus could never be offered to the car on a 3-phase cable.
        """
        assert LADDER[-1] == 16
        assert step_watts(16, W_PER_A_3P) == 10192
        # The old table's ceiling is now just one step among several.
        assert step_watts(12, W_PER_A_3P) == 7644
        assert [a for a in LADDER if a > 12] == [13, 14, 15, 16]

    def test_same_ladder_on_one_and_three_phases(self) -> None:
        """Phase count changes the watts per step, never which steps exist."""
        assert amp_steps(6, 16) == LADDER
        assert step_watts(16, W_PER_A_1P) == 3680
        assert step_watts(16, W_PER_A_3P) == 10192

    def test_step_watts_is_the_only_conversion(self) -> None:
        assert step_watts(6, W_PER_A_1P) == 1380
        assert step_watts(0, W_PER_A_3P) == 0


# --- snap_to_amp_step unit tests ---


class TestSnapToAmpStep:
    def test_picks_highest_affordable_step(self) -> None:
        """5000 W surplus on 3φ → 7 A (4459 W); 8 A would be 5096 W."""
        assert snap_to_amp_step(5000, LADDER, W_PER_A_3P) == 7

    def test_surplus_below_all_steps_returns_min(self) -> None:
        """2000 W on 3φ (< 6 A = 3822 W) → min step, battery covers the gap."""
        assert snap_to_amp_step(2000, LADDER, W_PER_A_3P) == 6

    def test_surplus_above_max_picks_max(self) -> None:
        """12000 W on 3φ → 16 A, the wallbox maximum (was capped at 12 A)."""
        assert snap_to_amp_step(12000, LADDER, W_PER_A_3P) == 16

    def test_exact_step_boundary(self) -> None:
        """Exactly 10 A worth of surplus → 10 A."""
        assert snap_to_amp_step(step_watts(10, W_PER_A_3P), LADDER, W_PER_A_3P) == 10

    def test_threshold_removes_low_steps(self) -> None:
        """A threshold above a step excludes it; the lowest allowed one is used."""
        assert snap_to_amp_step(5000, LADDER, W_PER_A_3P, threshold_w=5096) == 8

    def test_threshold_above_every_step_returns_zero(self) -> None:
        assert snap_to_amp_step(5000, LADDER, W_PER_A_3P, threshold_w=99999) == 0

    def test_single_phase_uses_the_whole_range(self) -> None:
        assert snap_to_amp_step(2500, LADDER, W_PER_A_1P) == 10   # 2300 W
        assert snap_to_amp_step(9000, LADDER, W_PER_A_1P) == 16   # 3680 W cap
        assert snap_to_amp_step(1000, LADDER, W_PER_A_1P) == 6    # below all steps


# --- Solar candidate gate (home-battery fills-today, EV-aware) ---


class TestBuildSolarCandidates:
    """Gate: include the snap-up step only when the HOME battery still reaches
    full today (with the EV load accounted for).

    surplus_w=5096 (8 A x 637) chosen so that snap_up=[9] and
    snap_down=[8, 7, 6] under threshold=3500.
    """

    def test_battery_full_keeps_snap_up(self) -> None:
        """Home battery still fills today → snap-up included (battery drain OK)."""
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=True,
        )
        assert candidates == [9, 8, 7, 6]
        assert "step-up allowed" in reason

    def test_battery_not_full_drops_snap_up(self) -> None:
        """Home battery would NOT fill today → snap-down only (preserve battery)."""
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=False,
        )
        assert candidates == [8, 7, 6]
        assert 9 not in candidates
        assert "preserve battery" in reason

    def test_candidate_at_top_step_no_snap_up_exists(self) -> None:
        """candidate=16 A (max) → snap_up list is empty even when allowed."""
        candidates, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=10192,
            threshold=3500,
            step_up_allowed=True,
        )
        # No step above 16 A exists; snap_down only.
        assert candidates == [16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6]

    def test_threshold_filters_low_steps_out(self) -> None:
        """threshold=5000 filters 6 A and 7 A out of snap_down."""
        candidates, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=5000,
            step_up_allowed=True,
        )
        assert candidates == [9, 8]

    def test_battery_not_full_still_charges_at_or_below_surplus(self) -> None:
        """Even when not filling, the EV still charges (snap-down), just no drain."""
        candidates, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=3822,
            threshold=3500,
            step_up_allowed=False,
        )
        # snap-down from 6 A: [6], no 7 A snap-up
        assert candidates == [6]


class TestStepUpSuppression:
    """Topic 2 step-up suppression (FSD 4.3.7): when the conservative p10
    forecast already fills BOTH the home battery and the car by evening,
    stepping up gains nothing and only pays the battery's round-trip loss."""

    def test_both_full_suppresses_step_up(self) -> None:
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=True,
            both_full_by_evening=True,
        )
        assert candidates == [8, 7, 6]
        assert 9 not in candidates
        assert "round-trip" in reason

    def test_default_off_preserves_step_up(self) -> None:
        """Omitting the flag (e.g. signal not computable) → unchanged behaviour."""
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=True,
        )
        assert candidates == [9, 8, 7, 6]
        assert "step-up allowed" in reason

    def test_suppression_does_not_block_charging(self) -> None:
        """The car keeps charging at/below surplus — only the drain step goes."""
        candidates, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=3822,
            threshold=3500,
            step_up_allowed=True,
            both_full_by_evening=True,
        )
        assert candidates == [6]

    def test_suppression_is_redundant_when_already_unprotected(self) -> None:
        """Below the floor the step-up is already gone; suppression is a no-op."""
        suppressed, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096, threshold=3500,
            step_up_allowed=False, both_full_by_evening=True,
        )
        unsuppressed, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096, threshold=3500,
            step_up_allowed=False, both_full_by_evening=False,
        )
        assert suppressed == unsuppressed == [8, 7, 6]

    def test_target_gate_still_wins_over_suppression(self) -> None:
        """Battery can't reach target → no charging at all, regardless."""
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=True,
            target_reachable=False,
            both_full_by_evening=True,
        )
        assert candidates == []
        assert "charge target" in reason


class TestSimulateHouseAndCar:
    """The shared allocation model behind the p50 dashboard curve and the p10
    step-up suppression gate: house battery first (to its target), overflow to
    the car, deficits drain the house only.

    It reports the car side as **energy**, not SOC — that independence from the
    starting car SOC is what lets the gate re-check a live SOC between runs."""

    @staticmethod
    def _steps(values_wh: list[float]) -> list[tuple[datetime, float]]:
        base = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
        return [(base + timedelta(minutes=15 * i), v) for i, v in enumerate(values_wh)]

    def _run(self, values_wh, **kw):
        defaults = dict(
            house_kwh=5.0, house_cap_kwh=10.0, house_ceil_kwh=9.0, car_efficiency=1.0,
        )
        return list(simulate_house_and_car(self._steps(values_wh), **{**defaults, **kw}))

    def test_house_fills_before_car(self) -> None:
        # 2 kWh surplus, house has 4 kWh headroom → all to house, car gets none.
        (_, house_kwh, car_kwh), = self._run([2000])
        assert house_kwh == pytest.approx(7.0)
        assert car_kwh == pytest.approx(0.0)

    def test_overflow_past_target_goes_to_car(self) -> None:
        # 6 kWh surplus, 4 kWh headroom → 2 kWh overflows to the car.
        (_, house_kwh, car_kwh), = self._run([6000])
        assert house_kwh == pytest.approx(9.0)
        assert car_kwh == pytest.approx(2.0)

    def test_efficiency_applied_to_car_only(self) -> None:
        (_, house_kwh, car_kwh), = self._run([6000], car_efficiency=0.9)
        assert house_kwh == pytest.approx(9.0)
        assert car_kwh == pytest.approx(1.8)

    def test_deficit_drains_house_not_car(self) -> None:
        (_, house_kwh, car_kwh), = self._run([-2000])
        assert house_kwh == pytest.approx(3.0)
        assert car_kwh == pytest.approx(0.0)

    def test_house_never_goes_negative(self) -> None:
        (_, house_kwh, _), = self._run([-9000])
        assert house_kwh == pytest.approx(0.0)

    def test_car_energy_is_monotonic(self) -> None:
        pts = self._run([9000] * 20, house_ceil_kwh=5.0)
        car = [p[2] for p in pts]
        assert car == sorted(car)
        assert car[-1] > 0

    def test_car_energy_is_independent_of_starting_car_soc(self) -> None:
        """The whole point: no car-SOC input, so one run serves any live SOC."""
        a = [p[2] for p in self._run([6000] * 4)]
        b = [p[2] for p in self._run([6000] * 4)]
        assert a == b

    def test_house_ceiling_is_the_target_not_capacity(self) -> None:
        # Ceiling 9 kWh < capacity 10 kWh: the house stops at the target and the
        # rest overflows, which is what makes the car reachable before 100%.
        pts = self._run([1000] * 10)
        assert max(p[1] for p in pts) == pytest.approx(9.0)


class TestTargetGate:
    """Topic 1 target gate (FSD 4.3.6): the car yields all surplus to the home
    battery once the battery can no longer reach its charge target today."""

    def test_target_unreachable_blocks_all_charging(self) -> None:
        """target_reachable=False → no candidates at all (car stops)."""
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=True,
            target_reachable=False,
        )
        assert candidates == []
        assert "charge target" in reason

    def test_target_unreachable_overrides_step_down_too(self) -> None:
        """Even snap-down is suppressed — the battery owns the surplus."""
        candidates, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=3822,
            threshold=3500,
            step_up_allowed=False,
            target_reachable=False,
        )
        assert candidates == []

    def test_target_reachable_default_is_unchanged(self) -> None:
        """Omitting target_reachable defaults to True → existing behaviour."""
        candidates, reason = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_3P,
            surplus_w=5096,
            threshold=3500,
            step_up_allowed=True,
        )
        assert candidates == [9, 8, 7, 6]
        assert "step-up allowed" in reason


# --- single-phase stepping (cable phase detection) ---


class TestSinglePhaseLadder:
    def test_same_ladder_regardless_of_phase_count(self) -> None:
        """Only the watts per step change with the cable, never the steps."""
        assert amp_steps(6, 16) == LADDER
        assert step_watts(6, W_PER_A_1P) == 1380
        assert step_watts(16, W_PER_A_1P) == 3680

    def test_single_phase_range_is_reachable(self) -> None:
        """The old bug: 3φ watt steps snapped into the 1φ range yielded nothing.
        With amps the same ladder serves both, so 2500 W of surplus is a step.
        """
        assert snap_to_amp_step(2500, LADDER, W_PER_A_1P) == 10

    def test_step_up_uses_the_same_ladder_on_one_phase(self) -> None:
        """Protected at 10 A on 1φ → step up to 11 A."""
        cands, _ = build_solar_candidates(
            steps=LADDER,
            watts_per_amp=W_PER_A_1P,
            surplus_w=2300,
            threshold=1380,
            step_up_allowed=True,
            target_reachable=True,
        )
        assert cands[0] == 11
        assert all(c in LADDER for c in cands)


class TestSolarStartThreshold:
    def test_single_phase_ignores_min_solar_uses_wallbox_min(self) -> None:
        # 1φ: ev_min_solar_power (3000) ignored → wallbox min (1380).
        assert solar_start_threshold(1, 3000, 1380) == 1380

    def test_three_phase_honors_min_solar(self) -> None:
        assert solar_start_threshold(3, 3000, 3822) == 3000

    def test_three_phase_falls_back_to_wallbox_min(self) -> None:
        assert solar_start_threshold(3, None, 3822) == 3822
        assert solar_start_threshold(3, 0, 3822) == 3822
