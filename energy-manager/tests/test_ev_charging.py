"""Tests for EV charging power calculation and phase-gap handling."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest

from src.ev_charging import (
    build_solar_candidates,
    calculate_ev_power,
    resolve_phase_gap,
    simulate_house_and_car,
    snap_to_power_step,
)


# --- snap_to_power_step unit tests ---


class TestSnapToPowerStep:
    def test_surplus_5000_picks_4354(self) -> None:
        """5000 W surplus → highest step ≤ 5000 = 4354W (7A)."""
        assert snap_to_power_step(5000) == 4354

    def test_surplus_below_steps_returns_min(self) -> None:
        """2000 W surplus (< 3962W) → returns min step, battery covers gap."""
        assert snap_to_power_step(2000) == 3962

    def test_surplus_above_max_picks_max(self) -> None:
        """12000 W surplus → picks 7624W (12A, max step)."""
        assert snap_to_power_step(12000) == 7624

    def test_exact_step_boundary(self) -> None:
        """6288 W = exactly 10A step → picks 6288."""
        assert snap_to_power_step(6288) == 6288

    def test_custom_power_range(self) -> None:
        """5000 W with min=5117 → returns 5117 (min valid step)."""
        assert snap_to_power_step(5000, min_power_w=5117) == 5117

    def test_custom_max(self) -> None:
        """12000 W with max=6288 → 6288."""
        assert snap_to_power_step(12000, max_power_w=6288) == 6288

    def test_between_steps(self) -> None:
        """5200 W → highest step ≤ 5200 = 5117 (8A)."""
        assert snap_to_power_step(5200) == 5117

    def test_just_at_min_step(self) -> None:
        """3962 W exactly → picks 3962."""
        assert snap_to_power_step(3962) == 3962


# --- resolve_phase_gap unit tests ---


class TestResolvePhaseGap:
    def test_in_gap_battery_not_full_snaps_down(self) -> None:
        assert resolve_phase_gap(3900, battery_full=False) == 3680

    def test_in_gap_battery_full_snaps_up(self) -> None:
        assert resolve_phase_gap(3900, battery_full=True) == 4140

    def test_at_gap_lo_no_snap(self) -> None:
        """Boundary is exclusive — exactly 3680 stays."""
        assert resolve_phase_gap(3680, battery_full=False) == 3680

    def test_at_gap_hi_no_snap(self) -> None:
        """Boundary is exclusive — exactly 4140 stays."""
        assert resolve_phase_gap(4140, battery_full=True) == 4140

    def test_below_gap_unaffected(self) -> None:
        assert resolve_phase_gap(2000, battery_full=False) == 2000

    def test_above_gap_unaffected(self) -> None:
        assert resolve_phase_gap(7000, battery_full=True) == 7000


# --- calculate_ev_power integration tests ---


class TestCalculateEvPower:
    def test_below_min_pauses(self) -> None:
        result = calculate_ev_power(excess_w=1000, min_power_w=1400)
        assert result.target_power_w == 0

    def test_excess_in_gap_snaps_down(self) -> None:
        """3900 W in gap → snaps to 3680."""
        result = calculate_ev_power(excess_w=3900, battery_full=False)
        assert result.target_power_w == 3680

    def test_excess_in_gap_battery_full_snaps_up(self) -> None:
        """3900 W in gap, battery full → snaps to 4140."""
        result = calculate_ev_power(excess_w=3900, battery_full=True)
        assert result.target_power_w == 4140

    def test_at_gap_hi_stays(self) -> None:
        """4140 W is exactly at gap boundary (exclusive) → stays 4140."""
        result = calculate_ev_power(excess_w=4140, battery_full=False)
        assert result.target_power_w == 4140

    def test_normal_excess_unaffected(self) -> None:
        result = calculate_ev_power(excess_w=7000)
        assert result.target_power_w == 7000

    def test_clamps_to_max(self) -> None:
        result = calculate_ev_power(excess_w=15000, max_power_w=11000)
        assert result.target_power_w == 11000


# --- IT-PHASE-01: Phase-gap stability under cloud fluctuation ---


class TestPhaseGapStability:
    """Verify that excess values oscillating around the phase gap
    produce stable output without phase-switching flaps (IT-PHASE-01).

    The wallbox dead zone is 3680–4140 W.  Cloud fluctuations that
    repeatedly cross this band should snap to one side consistently
    so the wallbox never flip-flops between 1φ and 3φ.
    """

    # Simulated cloud fluctuation: 20 readings oscillating within the gap
    # (3701–4139 W).  All inside the dead zone → must snap consistently.
    _CLOUD_EXCESS_SERIES = [
        3750, 3850, 4050, 3800, 4000,
        3900, 3950, 4100, 3780, 3920,
        4080, 3820, 3870, 4130, 3760,
        3810, 4050, 3850, 3900, 4000,
    ]

    def test_cloud_fluctuation_battery_not_full(self) -> None:
        """All gap values snap to 3680 W (1φ max) — no phase switches."""
        results = [
            calculate_ev_power(excess_w=e, battery_full=False)
            for e in self._CLOUD_EXCESS_SERIES
        ]
        powers = [r.target_power_w for r in results]

        # Every output should be exactly 3680 (snapped down)
        assert all(p == 3680 for p in powers), (
            f"Expected all 3680, got {powers}"
        )

    def test_cloud_fluctuation_battery_full(self) -> None:
        """All gap values snap to 4140 W (3φ min) — no phase switches."""
        results = [
            calculate_ev_power(excess_w=e, battery_full=True)
            for e in self._CLOUD_EXCESS_SERIES
        ]
        powers = [r.target_power_w for r in results]

        # Every output should be exactly 4140 (snapped up)
        assert all(p == 4140 for p in powers), (
            f"Expected all 4140, got {powers}"
        )


# --- Solar candidate gate (home-battery fills-today, EV-aware) ---


class TestBuildSolarCandidates:
    """Gate: include the snap-up step only when the HOME battery still reaches
    full today (with the EV load accounted for).

    candidate_power=5117 (8A) chosen so that snap_up=[5727] and
    snap_down=[5117, 4354, 3962] under default threshold=3500.
    """

    def test_battery_full_keeps_snap_up(self) -> None:
        """Home battery still fills today → snap-up included (battery drain OK)."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            step_up_allowed=True,
        )
        assert candidates == [5727, 5117, 4354, 3962]
        assert "step-up allowed" in reason

    def test_battery_not_full_drops_snap_up(self) -> None:
        """Home battery would NOT fill today → snap-down only (preserve battery)."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            step_up_allowed=False,
        )
        assert candidates == [5117, 4354, 3962]
        assert 5727 not in candidates
        assert "preserve battery" in reason

    def test_candidate_at_top_step_no_snap_up_exists(self) -> None:
        """candidate=7624 (max) → snap_up list is empty even when allowed."""
        candidates, _ = build_solar_candidates(
            candidate_power=7624,
            threshold=3500,
            step_up_allowed=True,
        )
        # No step above 7624 exists; snap_down only.
        assert candidates == [7624, 7034, 6288, 5727, 5117, 4354, 3962]

    def test_threshold_filters_low_steps_out(self) -> None:
        """threshold=5000 filters 3962/4354 from snap_down."""
        candidates, _ = build_solar_candidates(
            candidate_power=5117,
            threshold=5000,
            step_up_allowed=True,
        )
        assert candidates == [5727, 5117]

    def test_battery_not_full_still_charges_at_or_below_surplus(self) -> None:
        """Even when not filling, the EV still charges (snap-down), just no drain."""
        candidates, _ = build_solar_candidates(
            candidate_power=4354,
            threshold=3500,
            step_up_allowed=False,
        )
        # snap-down from 7A: [4354, 3962], no 5117 snap-up
        assert candidates == [4354, 3962]


class TestStepUpSuppression:
    """Topic 2 step-up suppression (FSD 4.3.7): when the conservative p10
    forecast already fills BOTH the home battery and the car by evening,
    stepping up gains nothing and only pays the battery's round-trip loss."""

    def test_both_full_suppresses_step_up(self) -> None:
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            step_up_allowed=True,
            both_full_by_evening=True,
        )
        assert candidates == [5117, 4354, 3962]
        assert 5727 not in candidates
        assert "round-trip" in reason

    def test_default_off_preserves_step_up(self) -> None:
        """Omitting the flag (e.g. signal not computable) → unchanged behaviour."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            step_up_allowed=True,
        )
        assert candidates == [5727, 5117, 4354, 3962]
        assert "step-up allowed" in reason

    def test_suppression_does_not_block_charging(self) -> None:
        """The car keeps charging at/below surplus — only the drain step goes."""
        candidates, _ = build_solar_candidates(
            candidate_power=4354,
            threshold=3500,
            step_up_allowed=True,
            both_full_by_evening=True,
        )
        assert candidates == [4354, 3962]

    def test_suppression_is_redundant_when_already_unprotected(self) -> None:
        """Below the floor the step-up is already gone; suppression is a no-op."""
        suppressed, _ = build_solar_candidates(
            candidate_power=5117, threshold=3500,
            step_up_allowed=False, both_full_by_evening=True,
        )
        unsuppressed, _ = build_solar_candidates(
            candidate_power=5117, threshold=3500,
            step_up_allowed=False, both_full_by_evening=False,
        )
        assert suppressed == unsuppressed == [5117, 4354, 3962]

    def test_target_gate_still_wins_over_suppression(self) -> None:
        """Battery can't reach target → no charging at all, regardless."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
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
    the car, deficits drain the house only."""

    @staticmethod
    def _steps(values_wh: list[float]) -> list[tuple[datetime, float]]:
        base = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
        return [(base + timedelta(minutes=15 * i), v) for i, v in enumerate(values_wh)]

    def _run(self, values_wh, **kw):
        defaults = dict(
            house_kwh=5.0, house_cap_kwh=10.0, house_ceil_kwh=9.0,
            car_soc_pct=50.0, car_capacity_kwh=50.0, car_efficiency=1.0,
        )
        return list(simulate_house_and_car(self._steps(values_wh), **{**defaults, **kw}))

    def test_house_fills_before_car(self) -> None:
        # 2 kWh surplus, house has 4 kWh headroom → all to house, car unchanged.
        (_, house_kwh, car_pct), = self._run([2000])
        assert house_kwh == pytest.approx(7.0)
        assert car_pct == pytest.approx(50.0)

    def test_overflow_past_target_goes_to_car(self) -> None:
        # 6 kWh surplus, 4 kWh headroom → 2 kWh overflows to the car (+4% of 50 kWh).
        (_, house_kwh, car_pct), = self._run([6000])
        assert house_kwh == pytest.approx(9.0)
        assert car_pct == pytest.approx(54.0)

    def test_efficiency_applied_to_car_only(self) -> None:
        (_, _, car_pct), = self._run([6000], car_efficiency=0.9)
        assert car_pct == pytest.approx(50.0 + 2.0 * 0.9 / 50 * 100)

    def test_deficit_drains_house_not_car(self) -> None:
        (_, house_kwh, car_pct), = self._run([-2000])
        assert house_kwh == pytest.approx(3.0)
        assert car_pct == pytest.approx(50.0)

    def test_house_never_goes_negative(self) -> None:
        (_, house_kwh, _), = self._run([-9000])
        assert house_kwh == pytest.approx(0.0)

    def test_car_soc_is_monotonic_and_capped_at_100(self) -> None:
        pts = self._run([9000] * 20, house_ceil_kwh=5.0)
        car = [p[2] for p in pts]
        assert car == sorted(car)
        assert car[-1] == pytest.approx(100.0)

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
            candidate_power=5117,
            threshold=3500,
            step_up_allowed=True,
            target_reachable=False,
        )
        assert candidates == []
        assert "charge target" in reason

    def test_target_unreachable_overrides_step_down_too(self) -> None:
        """Even snap-down is suppressed — the battery owns the surplus."""
        candidates, _ = build_solar_candidates(
            candidate_power=4354,
            threshold=3500,
            step_up_allowed=False,
            target_reachable=False,
        )
        assert candidates == []

    def test_target_reachable_default_is_unchanged(self) -> None:
        """Omitting target_reachable defaults to True → existing behaviour."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            step_up_allowed=True,
        )
        assert candidates == [5727, 5117, 4354, 3962]
        assert "step-up allowed" in reason


# --- single-phase power stepping (cable phase detection) ---

from src.ev_charging import (  # noqa: E402
    POWER_STEPS_1P,
    POWER_STEPS_3P,
    power_steps_for_phases,
    solar_start_threshold,
)


class TestPowerStepsForPhases:
    def test_single_phase_selects_1p_table(self) -> None:
        assert power_steps_for_phases(1) == POWER_STEPS_1P

    def test_three_phase_selects_3p_table(self) -> None:
        assert power_steps_for_phases(3) == POWER_STEPS_3P

    def test_default_is_three_phase(self) -> None:
        # Unknown/other phase counts fall back to the 3-phase table.
        assert power_steps_for_phases(0) == POWER_STEPS_3P

    def test_1p_table_spans_single_phase_range(self) -> None:
        # 6A..16A at 230 W/A → 1380..3680 W (the published 1φ range).
        assert POWER_STEPS_1P[0] == 1380
        assert POWER_STEPS_1P[-1] == 3680


class TestSnapSinglePhase:
    def test_snaps_within_single_phase_range(self) -> None:
        # 2500 W surplus on 1φ → 2300 W step (10A), not 0 (3φ min is 3962).
        step = snap_to_power_step(2500, POWER_STEPS_1P[0], POWER_STEPS_1P[-1],
                                  steps=POWER_STEPS_1P)
        assert step == 2300

    def test_below_all_1p_steps_returns_min(self) -> None:
        step = snap_to_power_step(1000, POWER_STEPS_1P[0], POWER_STEPS_1P[-1],
                                  steps=POWER_STEPS_1P)
        assert step == 1380

    def test_above_all_1p_steps_returns_max(self) -> None:
        step = snap_to_power_step(9000, POWER_STEPS_1P[0], POWER_STEPS_1P[-1],
                                  steps=POWER_STEPS_1P)
        assert step == 3680

    def test_3p_table_would_return_zero_for_single_phase_range(self) -> None:
        # The old bug: 3φ steps snapped into the 1φ range yield no valid step.
        assert snap_to_power_step(2500, 1380, 3680) == 0


class TestBuildSolarCandidatesSinglePhase:
    def test_step_up_uses_1p_steps(self) -> None:
        # candidate 2300 (10A), protected → step up to 2530 (11A) on 1φ.
        cands, _ = build_solar_candidates(
            candidate_power=2300, threshold=1380, step_up_allowed=True,
            target_reachable=True, steps=POWER_STEPS_1P,
        )
        assert 2530 in cands
        assert all(c in POWER_STEPS_1P for c in cands)


class TestSolarStartThreshold:
    def test_single_phase_ignores_min_solar_uses_wallbox_min(self) -> None:
        # 1φ: ev_min_solar_power (3000) ignored → wallbox min (1380).
        assert solar_start_threshold(1, 3000, 1380) == 1380

    def test_three_phase_honors_min_solar(self) -> None:
        assert solar_start_threshold(3, 3000, 4140) == 3000

    def test_three_phase_falls_back_to_wallbox_min(self) -> None:
        # Missing/zero ev_min_solar_power → wallbox min.
        assert solar_start_threshold(3, None, 4140) == 4140
        assert solar_start_threshold(3, 0, 4140) == 4140
