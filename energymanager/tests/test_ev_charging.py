"""
Tests for EV charging power calculation (FSD 4.5.6).

Test cases:
1. No excess → pause (0W)
2. Excess in range (min–max) → charge at excess
3. Excess above max → clamp to max
4. Edge cases (rounding, custom limits)
"""

import pytest

from src.ev_charging import calculate_ev_power, EVChargingResult, _round_to_step


class TestPause:
    """Pause charging when excess < min_power_w."""

    def test_negative_excess(self):
        result = calculate_ev_power(excess_w=-1000)
        assert result.target_power_w == 0
        assert result.available_excess_w == -1000
        assert "Pause" in result.reason

    def test_small_excess(self):
        result = calculate_ev_power(excess_w=1000)
        assert result.target_power_w == 0

    def test_zero_excess(self):
        result = calculate_ev_power(excess_w=0)
        assert result.target_power_w == 0

    def test_just_below_minimum(self):
        result = calculate_ev_power(excess_w=1399)
        assert result.target_power_w == 0


class TestCharging:
    """Charging when excess >= min_power_w."""

    def test_at_minimum(self):
        result = calculate_ev_power(excess_w=1400)
        assert result.target_power_w == 1400

    def test_mid_range(self):
        result = calculate_ev_power(excess_w=5000)
        assert result.target_power_w == 5000
        assert result.available_excess_w == 5000

    def test_at_max(self):
        result = calculate_ev_power(excess_w=11000)
        assert result.target_power_w == 11000

    def test_above_max_clamps(self):
        result = calculate_ev_power(excess_w=15000)
        assert result.target_power_w == 11000

    def test_various_values(self):
        for excess in [1400, 2500, 3700, 4100, 7000, 11000]:
            result = calculate_ev_power(excess_w=excess)
            assert result.target_power_w == excess
            assert result.available_excess_w == excess


class TestRounding:
    """Rounding to 100W step size."""

    def test_round_to_nearest_100(self):
        assert _round_to_step(1450) == 1400
        assert _round_to_step(1451) == 1500
        assert _round_to_step(1500) == 1500
        assert _round_to_step(1550) == 1600

    def test_rounding_applied_to_result(self):
        result = calculate_ev_power(excess_w=1680)
        assert result.target_power_w == 1700

    def test_rounding_does_not_exceed_max(self):
        result = calculate_ev_power(excess_w=11050, max_power_w=11000)
        assert result.target_power_w == 11000


class TestCustomLimits:
    """Custom min/max power limits."""

    def test_1phase_only(self):
        """1-phase wallbox: 1400–3700W."""
        result = calculate_ev_power(excess_w=2000, min_power_w=1400, max_power_w=3700)
        assert result.target_power_w == 2000

    def test_1phase_clamps_at_max(self):
        result = calculate_ev_power(excess_w=5000, min_power_w=1400, max_power_w=3700)
        assert result.target_power_w == 3700

    def test_3phase_only(self):
        """3-phase wallbox: 4100–11000W."""
        result = calculate_ev_power(excess_w=5000, min_power_w=4100, max_power_w=11000)
        assert result.target_power_w == 5000

    def test_3phase_below_min_pauses(self):
        result = calculate_ev_power(excess_w=3000, min_power_w=4100, max_power_w=11000)
        assert result.target_power_w == 0

    def test_switchable(self):
        """Switchable wallbox: 1400–11000W."""
        result = calculate_ev_power(excess_w=2000, min_power_w=1400, max_power_w=11000)
        assert result.target_power_w == 2000
        result = calculate_ev_power(excess_w=8000, min_power_w=1400, max_power_w=11000)
        assert result.target_power_w == 8000

    def test_custom_max_clamps(self):
        result = calculate_ev_power(excess_w=10000, min_power_w=1400, max_power_w=8000)
        assert result.target_power_w == 8000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
