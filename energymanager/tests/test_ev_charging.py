"""Tests for EV charging power calculation and phase-gap handling."""

from __future__ import annotations

from src.ev_charging import (
    build_solar_candidates,
    calculate_ev_power,
    resolve_phase_gap,
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


# --- Solar candidate gate (EOD car SOC forecast) ---


class TestBuildSolarCandidates:
    """Gate: only include snap-up step when EV won't reach target by EOD.

    candidate_power=5117 (8A) chosen so that snap_up=[5727] and
    snap_down=[5117, 4354, 3962] under default threshold=3500.
    """

    def test_forecast_reaches_target_drops_snap_up(self) -> None:
        """Forecast 85% ≥ target 80% → no snap-up; stays at-or-below surplus."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            forecast_eod=85.0,
            ev_target_max=80.0,
        )
        assert candidates == [5117, 4354, 3962]
        assert "no battery drain" in reason

    def test_forecast_below_target_keeps_snap_up(self) -> None:
        """Forecast 70% < target 80% → snap-up included (battery drain allowed)."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            forecast_eod=70.0,
            ev_target_max=80.0,
        )
        assert candidates == [5727, 5117, 4354, 3962]
        assert "snap-up allowed" in reason

    def test_forecast_equals_target_drops_snap_up(self) -> None:
        """Boundary: forecast == target → target is reached → no drain."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            forecast_eod=80.0,
            ev_target_max=80.0,
        )
        assert 5727 not in candidates
        assert candidates == [5117, 4354, 3962]
        assert "no battery drain" in reason

    def test_no_forecast_falls_back_to_snap_up(self) -> None:
        """forecast_eod None (e.g. car SOC unknown) → keep current snap-up behavior."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            forecast_eod=None,
            ev_target_max=80.0,
        )
        assert candidates == [5727, 5117, 4354, 3962]
        assert "no forecast" in reason

    def test_no_target_falls_back_to_snap_up(self) -> None:
        """ev_target_max None (entity missing) → keep current snap-up behavior."""
        candidates, reason = build_solar_candidates(
            candidate_power=5117,
            threshold=3500,
            forecast_eod=85.0,
            ev_target_max=None,
        )
        assert candidates == [5727, 5117, 4354, 3962]
        assert "no forecast" in reason

    def test_candidate_at_top_step_no_snap_up_exists(self) -> None:
        """candidate=7624 (max) → snap_up list is empty regardless of gate."""
        candidates, _ = build_solar_candidates(
            candidate_power=7624,
            threshold=3500,
            forecast_eod=50.0,  # would normally allow snap-up
            ev_target_max=80.0,
        )
        # No step above 7624 exists; snap_down only.
        assert candidates == [7624, 7034, 6288, 5727, 5117, 4354, 3962]

    def test_threshold_filters_low_steps_out(self) -> None:
        """threshold=5000 filters 3962/4354 from snap_down."""
        candidates, _ = build_solar_candidates(
            candidate_power=5117,
            threshold=5000,
            forecast_eod=70.0,  # snap-up allowed
            ev_target_max=80.0,
        )
        assert candidates == [5727, 5117]
