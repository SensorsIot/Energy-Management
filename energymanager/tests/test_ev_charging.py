"""
Tests for EV charging power calculation (FSD 4.5.4.1).

Test cases:
1. No excess → pause (0W)
2. 1-phase range (1400–3700W) → correct clamping
3. 3-phase range (4100–11000W) → correct clamping
4. Gap range (3700–4100W) → falls to 1-phase max (3700W)
5. Grid export + wallbox → correct excess calculation
6. Edge cases (wallbox at 0W, large export, rounding)
"""

import pytest

from src.ev_charging import calculate_ev_power, EVChargingResult, _round_to_step


class TestPause:
    """Pause charging when excess < 1400W."""

    def test_no_excess_grid_importing(self):
        """Grid importing 1kW, no wallbox → excess -1000W → pause."""
        result = calculate_ev_power(grid_power_w=1000, wallbox_power_w=0)
        assert result.target_power_w == 0
        assert result.available_excess_w == -1000
        assert "Pause" in result.reason

    def test_small_excess_below_minimum(self):
        """Excess 1000W < 1400W minimum → pause."""
        result = calculate_ev_power(grid_power_w=-1000, wallbox_power_w=0)
        assert result.target_power_w == 0
        assert result.available_excess_w == 1000

    def test_grid_balanced_no_wallbox(self):
        """Grid at 0W, no wallbox → excess 0W → pause."""
        result = calculate_ev_power(grid_power_w=0, wallbox_power_w=0)
        assert result.target_power_w == 0
        assert result.available_excess_w == 0

    def test_excess_just_below_minimum(self):
        """Excess 1399W → pause (just below 1400W threshold)."""
        result = calculate_ev_power(grid_power_w=-1399, wallbox_power_w=0)
        assert result.target_power_w == 0


class TestOnePhase:
    """1-phase charging: 1400W–3700W."""

    def test_minimum_1phase(self):
        """Excess exactly 1400W → 1-phase at 1400W."""
        result = calculate_ev_power(grid_power_w=-1400, wallbox_power_w=0)
        assert result.target_power_w == 1400
        assert "1-phase" in result.reason

    def test_mid_1phase(self):
        """Excess 2500W → 1-phase at 2500W."""
        result = calculate_ev_power(grid_power_w=-2500, wallbox_power_w=0)
        assert result.target_power_w == 2500
        assert "1-phase" in result.reason

    def test_max_1phase(self):
        """Excess 3700W → 1-phase at 3700W."""
        result = calculate_ev_power(grid_power_w=-3700, wallbox_power_w=0)
        assert result.target_power_w == 3700
        assert "1-phase" in result.reason

    def test_excess_in_gap_clamps_to_1phase_max(self):
        """Excess 4000W (in gap 3700–4100) → 1-phase clamped at 3700W."""
        result = calculate_ev_power(grid_power_w=-4000, wallbox_power_w=0)
        assert result.target_power_w == 3700
        assert "1-phase" in result.reason

    def test_excess_just_below_3phase(self):
        """Excess 4099W → 1-phase clamped at 3700W."""
        result = calculate_ev_power(grid_power_w=-4099, wallbox_power_w=0)
        assert result.target_power_w == 3700
        assert "1-phase" in result.reason


class TestThreePhase:
    """3-phase charging: 4100W–11000W."""

    def test_minimum_3phase(self):
        """Excess exactly 4100W → 3-phase at 4100W."""
        result = calculate_ev_power(grid_power_w=-4100, wallbox_power_w=0)
        assert result.target_power_w == 4100
        assert "3-phase" in result.reason

    def test_mid_3phase(self):
        """Excess 7000W → 3-phase at 7000W."""
        result = calculate_ev_power(grid_power_w=-7000, wallbox_power_w=0)
        assert result.target_power_w == 7000
        assert "3-phase" in result.reason

    def test_max_3phase(self):
        """Excess 11000W → 3-phase at 11000W."""
        result = calculate_ev_power(grid_power_w=-11000, wallbox_power_w=0)
        assert result.target_power_w == 11000
        assert "3-phase" in result.reason

    def test_excess_above_max_clamps(self):
        """Excess 15000W → clamped at 11000W."""
        result = calculate_ev_power(grid_power_w=-15000, wallbox_power_w=0)
        assert result.target_power_w == 11000
        assert "3-phase" in result.reason


class TestExcessCalculation:
    """Verify excess = -grid_power + wallbox_power."""

    def test_exporting_with_active_wallbox(self):
        """Grid exporting 2kW, wallbox at 5kW → excess 7000W → 3-phase."""
        result = calculate_ev_power(grid_power_w=-2000, wallbox_power_w=5000)
        assert result.available_excess_w == 7000
        assert result.target_power_w == 7000
        assert "3-phase" in result.reason

    def test_balanced_with_active_wallbox(self):
        """Grid balanced, wallbox at 5kW → excess 5000W → 3-phase."""
        result = calculate_ev_power(grid_power_w=0, wallbox_power_w=5000)
        assert result.available_excess_w == 5000
        assert result.target_power_w == 5000
        assert "3-phase" in result.reason

    def test_importing_with_active_wallbox(self):
        """Grid importing 1kW, wallbox at 5kW → excess 4000W → 1-phase 3700W."""
        result = calculate_ev_power(grid_power_w=1000, wallbox_power_w=5000)
        assert result.available_excess_w == 4000
        assert result.target_power_w == 3700
        assert "1-phase" in result.reason

    def test_heavy_import_with_wallbox_reduces_to_pause(self):
        """Grid importing 4kW, wallbox at 3kW → excess -1000W → pause."""
        result = calculate_ev_power(grid_power_w=4000, wallbox_power_w=3000)
        assert result.available_excess_w == -1000
        assert result.target_power_w == 0

    def test_wallbox_not_connected_exporting(self):
        """Wallbox 0W (not charging), grid exporting 5kW → excess 5000W → 3-phase."""
        result = calculate_ev_power(grid_power_w=-5000, wallbox_power_w=0)
        assert result.available_excess_w == 5000
        assert result.target_power_w == 5000


class TestRounding:
    """Rounding to 100W step size."""

    def test_round_to_nearest_100(self):
        """Values round to nearest 100W."""
        assert _round_to_step(1450) == 1400
        assert _round_to_step(1451) == 1500
        assert _round_to_step(1500) == 1500
        assert _round_to_step(1549) == 1500
        assert _round_to_step(1550) == 1600

    def test_rounding_applied_to_result(self):
        """Excess 1680W → rounds to 1700W."""
        result = calculate_ev_power(grid_power_w=-1680, wallbox_power_w=0)
        assert result.target_power_w == 1700

    def test_rounding_does_not_exceed_max(self):
        """Excess 3750W in 1-phase → clamped to 3700W (max), not rounded to 3800W."""
        result = calculate_ev_power(grid_power_w=-3750, wallbox_power_w=0)
        assert result.target_power_w == 3700


class TestCustomLimits:
    """Custom power limits via parameters."""

    def test_custom_1phase_limits(self):
        """Custom 1-phase range: 1000–3000W."""
        result = calculate_ev_power(
            grid_power_w=-2000,
            wallbox_power_w=0,
            min_power_1phase_w=1000,
            max_power_1phase_w=3000,
        )
        assert result.target_power_w == 2000
        assert "1-phase" in result.reason

    def test_custom_3phase_limits(self):
        """Custom 3-phase range: 3500–8000W."""
        result = calculate_ev_power(
            grid_power_w=-5000,
            wallbox_power_w=0,
            min_power_3phase_w=3500,
            max_power_3phase_w=8000,
        )
        assert result.target_power_w == 5000
        assert "3-phase" in result.reason

    def test_custom_max_clamps(self):
        """Custom max 8000W clamps excess of 10000W."""
        result = calculate_ev_power(
            grid_power_w=-10000,
            wallbox_power_w=0,
            min_power_3phase_w=3500,
            max_power_3phase_w=8000,
        )
        assert result.target_power_w == 8000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
