"""Tests for EV charging power calculation and phase-gap handling."""

from __future__ import annotations

from src.ev_charging import calculate_ev_power, resolve_phase_gap


# --- resolve_phase_gap unit tests ---


class TestResolvePhaseGap:
    def test_in_gap_battery_not_full_snaps_down(self):
        assert resolve_phase_gap(3900, battery_full=False) == 3700

    def test_in_gap_battery_full_snaps_up(self):
        assert resolve_phase_gap(3900, battery_full=True) == 4140

    def test_at_gap_lo_no_snap(self):
        """Boundary is exclusive — exactly 3700 stays."""
        assert resolve_phase_gap(3700, battery_full=False) == 3700

    def test_at_gap_hi_no_snap(self):
        """Boundary is exclusive — exactly 4140 stays."""
        assert resolve_phase_gap(4140, battery_full=True) == 4140

    def test_below_gap_unaffected(self):
        assert resolve_phase_gap(2000, battery_full=False) == 2000

    def test_above_gap_unaffected(self):
        assert resolve_phase_gap(7000, battery_full=True) == 7000


# --- calculate_ev_power integration tests ---


class TestCalculateEvPower:
    def test_below_min_pauses(self):
        result = calculate_ev_power(excess_w=1000, min_power_w=1400)
        assert result.target_power_w == 0

    def test_excess_in_gap_snaps_down(self):
        """3900 W rounds to 3900, then gap snaps to 3700."""
        result = calculate_ev_power(excess_w=3900, battery_full=False)
        assert result.target_power_w == 3700

    def test_excess_in_gap_battery_full_snaps_up(self):
        """3900 W rounds to 3900, then gap snaps to 4140."""
        result = calculate_ev_power(excess_w=3900, battery_full=True)
        assert result.target_power_w == 4140

    def test_at_gap_hi_rounds_to_4100_stays(self):
        """4140 W rounds to 4100, which is in gap → snaps to 3700 (default battery not full)."""
        result = calculate_ev_power(excess_w=4140, battery_full=False)
        assert result.target_power_w == 3700

    def test_normal_excess_unaffected(self):
        result = calculate_ev_power(excess_w=7000)
        assert result.target_power_w == 7000

    def test_clamps_to_max(self):
        result = calculate_ev_power(excess_w=15000, max_power_w=11000)
        assert result.target_power_w == 11000


# --- IT-PHASE-01: Phase-gap stability under cloud fluctuation ---


class TestPhaseGapStability:
    """Verify that excess values oscillating around the phase gap
    produce stable output without phase-switching flaps (IT-PHASE-01).

    The wallbox dead zone is 3700–4140 W.  Cloud fluctuations that
    repeatedly cross this band should snap to one side consistently
    so the wallbox never flip-flops between 1φ and 3φ.
    """

    # Simulated cloud fluctuation: 20 readings oscillating within the gap
    # (3701–4139 W).  After _round_to_step(100) they stay in 3800–4100,
    # all inside the dead zone → must snap consistently.
    _CLOUD_EXCESS_SERIES = [
        3750, 3850, 4050, 3800, 4000,
        3900, 3950, 4100, 3780, 3920,
        4080, 3820, 3870, 4130, 3760,
        3810, 4050, 3850, 3900, 4000,
    ]

    def test_cloud_fluctuation_battery_not_full(self):
        """All gap values snap to 3700 W (1φ max) — no phase switches."""
        results = [
            calculate_ev_power(excess_w=e, battery_full=False)
            for e in self._CLOUD_EXCESS_SERIES
        ]
        powers = [r.target_power_w for r in results]

        # Every output should be exactly 3700 (snapped down)
        assert all(p == 3700 for p in powers), (
            f"Expected all 3700, got {powers}"
        )

    def test_cloud_fluctuation_battery_full(self):
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
