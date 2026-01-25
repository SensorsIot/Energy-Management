"""
Tests for appliance signal calculation (FSD v2.6).

Test cases:
1. GREEN: PV excess > appliance power
2. ORANGE: Final SOC% >= reserve% + appliance%
3. RED: Final SOC% < threshold
4. Edge cases (no simulation, low PV, etc.)
"""

import pytest
import pandas as pd
from datetime import datetime, timezone

from src.appliance_signal import calculate_appliance_signal, ApplianceSignal, get_final_soc_percent


def make_simulation(final_soc_percent: float) -> pd.DataFrame:
    """Create a minimal simulation DataFrame with given final SOC%."""
    times = pd.date_range(
        start=datetime.now(timezone.utc),
        periods=10,
        freq="15min"
    )
    # Create linear progression to final SOC
    soc_values = [80 + (final_soc_percent - 80) * i / 9 for i in range(10)]
    return pd.DataFrame({
        "soc_percent": soc_values,
        "soc_wh": [s * 100 for s in soc_values],  # For 10kWh battery
    }, index=times)


class TestGreenSignal:
    """GREEN: Current PV excess > appliance power."""

    def test_green_when_pv_excess_above_threshold(self):
        """PV excess 3000W > 2500W appliance power → GREEN."""
        signal = calculate_appliance_signal(
            current_pv_w=4000,
            current_load_w=1000,
            simulation=make_simulation(50),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "green"
        assert signal.excess_power_w == 3000
        assert "PV excess" in signal.reason

    def test_green_ignores_soc_when_pv_sufficient(self):
        """Even with low SOC, GREEN if PV excess is sufficient."""
        signal = calculate_appliance_signal(
            current_pv_w=5000,
            current_load_w=2000,
            simulation=make_simulation(5),  # Very low SOC
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "green"

    def test_not_green_when_pv_excess_exactly_equals_threshold(self):
        """PV excess exactly 2500W = 2500W threshold → NOT GREEN (need >)."""
        signal = calculate_appliance_signal(
            current_pv_w=3500,
            current_load_w=1000,
            simulation=make_simulation(50),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        # Excess is exactly 2500, need > 2500 for GREEN
        assert signal.signal != "green"


class TestOrangeSignal:
    """ORANGE: Final SOC% >= reserve% + appliance%."""

    def test_orange_when_soc_above_threshold(self):
        """Final SOC 30% >= 25% (10% reserve + 15% appliance) → ORANGE."""
        signal = calculate_appliance_signal(
            current_pv_w=1000,
            current_load_w=800,
            simulation=make_simulation(30),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        # Not GREEN (excess only 200W)
        # ORANGE: 30% >= 10% + 15% = 25%
        assert signal.signal == "orange"
        assert "reserve 10%" in signal.reason
        assert "appliance 15%" in signal.reason

    def test_orange_exactly_at_threshold(self):
        """Final SOC exactly at threshold (25%) → ORANGE."""
        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=500,
            simulation=make_simulation(25),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "orange"

    def test_orange_threshold_calculation(self):
        """Verify threshold calculation with different parameters."""
        # 20% reserve + 20% appliance (2000Wh / 10000Wh) = 40% threshold
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(45),  # Above 40% threshold
            appliance_power_w=2500,
            appliance_energy_wh=2000,  # 20% of 10kWh
            capacity_wh=10000,
            reserve_percent=20,
        )

        assert signal.signal == "orange"
        assert "reserve 20%" in signal.reason
        assert "appliance 20%" in signal.reason

    def test_orange_with_different_battery_capacity(self):
        """Verify calculation with 15kWh battery."""
        # 1500Wh / 15000Wh = 10% appliance
        # 10% reserve + 10% appliance = 20% threshold
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(25),  # Above 20% threshold
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=15000,  # 15kWh battery
            reserve_percent=10,
        )

        assert signal.signal == "orange"
        assert "appliance 10%" in signal.reason


class TestRedSignal:
    """RED: Final SOC% < reserve% + appliance%."""

    def test_red_when_soc_below_threshold(self):
        """Final SOC 20% < 25% threshold → RED."""
        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=800,
            simulation=make_simulation(20),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "red"
        assert "need reserve" in signal.reason

    def test_red_with_zero_pv(self):
        """No PV and low SOC → RED."""
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=1000,
            simulation=make_simulation(15),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "red"

    def test_red_just_below_threshold(self):
        """Final SOC 24% just below 25% threshold → RED."""
        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=500,
            simulation=make_simulation(24),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "red"


class TestEdgeCases:
    """Edge cases and special scenarios."""

    def test_empty_simulation_returns_red(self):
        """Empty simulation DataFrame → RED (safe default)."""
        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=500,
            simulation=pd.DataFrame(),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        # final_soc_percent = 0 when no simulation
        # 0% < 25% threshold → RED
        assert signal.signal == "red"

    def test_simulation_without_soc_column(self):
        """Simulation without soc_percent column → RED."""
        bad_simulation = pd.DataFrame({"other_column": [1, 2, 3]})
        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=500,
            simulation=bad_simulation,
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "red"

    def test_negative_pv_excess(self):
        """Load > PV (negative excess) → check SOC threshold."""
        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=2000,
            simulation=make_simulation(30),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        # Excess is -1500W (negative), not GREEN
        # SOC 30% >= 25% threshold → ORANGE
        assert signal.signal == "orange"
        assert signal.excess_power_w == -1500

    def test_zero_reserve_percent(self):
        """Zero reserve → only need appliance%."""
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(16),  # Just above 15%
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=0,  # No reserve
        )

        # Threshold = 0% + 15% = 15%
        # 16% >= 15% → ORANGE
        assert signal.signal == "orange"

    def test_high_reserve_percent(self):
        """High reserve (30%) changes threshold significantly."""
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(40),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=30,  # High reserve
        )

        # Threshold = 30% + 15% = 45%
        # 40% < 45% → RED
        assert signal.signal == "red"


class TestGetFinalSocPercent:
    """Test the get_final_soc_percent helper function."""

    def test_returns_last_value(self):
        """Should return the last soc_percent value."""
        sim = make_simulation(42)
        result = get_final_soc_percent(sim)
        assert abs(result - 42) < 0.1

    def test_empty_dataframe_returns_zero(self):
        """Empty DataFrame returns 0."""
        result = get_final_soc_percent(pd.DataFrame())
        assert result == 0

    def test_missing_column_returns_zero(self):
        """Missing soc_percent column returns 0."""
        bad_df = pd.DataFrame({"other": [1, 2, 3]})
        result = get_final_soc_percent(bad_df)
        assert result == 0


class TestApplianceSignalDataclass:
    """Test ApplianceSignal dataclass."""

    def test_dataclass_fields(self):
        """ApplianceSignal has all required fields."""
        signal = ApplianceSignal(
            signal="green",
            reason="Test",
            excess_power_w=1000,
            final_soc_percent=50,
        )

        assert signal.signal == "green"
        assert signal.reason == "Test"
        assert signal.excess_power_w == 1000
        assert signal.final_soc_percent == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
