"""
Tests for appliance signal calculation (FSD v2.29).

Test cases:
1. GREEN: PV excess > appliance power
2. ORANGE: SOC with appliance load simulated stays above reserve%
3. RED: SOC with appliance would drop below reserve%
4. Edge cases (no simulation, low PV, etc.)
"""

import pytest
import pandas as pd
from datetime import datetime, timezone

from src.appliance_signal import (
    calculate_appliance_signal,
    calculate_grid_export_before_evening,
    simulate_with_appliance,
    ApplianceSignal,
    get_final_soc_percent,
    get_min_soc_percent,
)


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
        "soc_wh": [s * 100 for s in soc_values],
        "net_wh": [0] * 10,
    }, index=times)


def make_simulation_with_dip(final_soc_percent: float, min_soc_percent: float) -> pd.DataFrame:
    """Create a simulation that dips to min_soc_percent then recovers."""
    times = pd.date_range(
        start=datetime.now(timezone.utc),
        periods=5,
        freq="15min"
    )
    start = max(final_soc_percent, min_soc_percent + 10)
    mid = min_soc_percent + (start - min_soc_percent) / 2
    soc_values = [start, mid, min_soc_percent, mid, final_soc_percent]
    return pd.DataFrame({
        "soc_percent": soc_values,
        "soc_wh": [s * 100 for s in soc_values],
        "net_wh": [0] * 5,
    }, index=times)


def make_simulation_with_export(
    min_soc_percent: float,
    export_wh_per_period: float,
    export_periods: int,
    start_hour: int = 10,
) -> pd.DataFrame:
    """Create a simulation with battery full and grid export."""
    base_time = datetime.now(timezone.utc).replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    times = pd.date_range(start=base_time, periods=export_periods + 4, freq="15min")

    soc_values = []
    net_wh_values = []

    for i in range(len(times)):
        if i < 2:
            soc_values.append(min_soc_percent)
            net_wh_values.append(100)
        elif i < 2 + export_periods:
            soc_values.append(100)
            net_wh_values.append(export_wh_per_period)
        else:
            soc_values.append(100)
            net_wh_values.append(-50)

    return pd.DataFrame({
        "soc_percent": soc_values,
        "net_wh": net_wh_values,
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
            simulation=make_simulation(5),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "green"

    def test_not_green_when_pv_excess_exactly_equals_threshold(self):
        """PV excess exactly 2500W = threshold → NOT GREEN (need >)."""
        signal = calculate_appliance_signal(
            current_pv_w=3500,
            current_load_w=1000,
            simulation=make_simulation(50),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal != "green"


class TestOrangeSignal:
    """ORANGE: SOC with appliance load stays above reserve%."""

    def test_orange_when_soc_above_threshold(self):
        """Min SOC 30% - 15% appliance = 15% ≥ 10% reserve → ORANGE."""
        signal = calculate_appliance_signal(
            current_pv_w=1000,
            current_load_w=800,
            simulation=make_simulation(30),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "orange"
        assert "appliance" in signal.reason.lower()

    def test_orange_exactly_at_threshold(self):
        """Min SOC 25% - 15% = 10% = reserve → ORANGE (>= check)."""
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
        """20% reserve, 2000Wh/10kWh = 20% appliance. Min 45% - 20% = 25% ≥ 20% → ORANGE."""
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(45),
            appliance_power_w=2500,
            appliance_energy_wh=2000,
            capacity_wh=10000,
            reserve_percent=20,
        )

        assert signal.signal == "orange"

    def test_orange_with_different_battery_capacity(self):
        """15kWh battery: 1500Wh = 10% appliance. Min 25% - 10% = 15% ≥ 10% → ORANGE."""
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(25),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=15000,
            reserve_percent=10,
        )

        assert signal.signal == "orange"


class TestRedSignal:
    """RED: SOC with appliance would drop below reserve%."""

    def test_red_when_soc_below_threshold(self):
        """Min SOC 20% - 15% = 5% < 10% reserve → RED."""
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
        assert "reserve" in signal.reason.lower()

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
        """Min SOC 24% - 15% = 9% < 10% reserve → RED."""
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


class TestExportContext:
    """Grid export info is included in ORANGE reason when available."""

    def test_orange_includes_export_note(self):
        """ORANGE with enough export mentions it in the reason."""
        # min_soc=30 → 30-15=15 ≥ 10 → ORANGE, and has export
        simulation = make_simulation_with_export(
            min_soc_percent=30,
            export_wh_per_period=500,
            export_periods=10,
            start_hour=10,
        )

        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=800,
            simulation=simulation,
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
            evening_hour=18,
        )

        assert signal.signal == "orange"
        assert "export" in signal.reason.lower()

    def test_red_despite_export_when_soc_too_low(self):
        """Export available but SOC too low → RED (SOC constraint takes priority)."""
        # min_soc=10 → 10-15=-5→0 < 10 → RED despite 2000Wh export
        simulation = make_simulation_with_export(
            min_soc_percent=10,
            export_wh_per_period=250,
            export_periods=8,
            start_hour=10,
        )

        signal = calculate_appliance_signal(
            current_pv_w=500,
            current_load_w=800,
            simulation=simulation,
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
            evening_hour=18,
        )

        assert signal.signal == "red"


class TestSimulateWithAppliance:
    """Test the simulate_with_appliance helper."""

    def test_subtracts_appliance_from_soc(self):
        """1500Wh / 10000Wh = 15% subtracted from all SOC values."""
        sim = make_simulation(30)  # min SOC is 30 (linear from 80 to 30)
        min_soc = simulate_with_appliance(sim, 1500, 10000)
        # 30% - 15% = 15%
        assert abs(min_soc - 15) < 0.1

    def test_clips_at_zero(self):
        """SOC can't go below 0 after subtraction."""
        sim = make_simulation(5)  # min SOC is 5%
        min_soc = simulate_with_appliance(sim, 1500, 10000)
        # 5% - 15% = -10% → clipped to 0%
        assert min_soc == 0.0

    def test_empty_simulation(self):
        min_soc = simulate_with_appliance(pd.DataFrame(), 1500, 10000)
        assert min_soc == 0.0


class TestCalculateGridExport:
    """Test the grid export calculation helper."""

    def test_export_counted_when_battery_full(self):
        """Export only counted when SOC >= 99.9%."""
        simulation = make_simulation_with_export(
            min_soc_percent=10,
            export_wh_per_period=100,
            export_periods=10,
            start_hour=10,
        )

        export = calculate_grid_export_before_evening(
            simulation, evening_hour=18, local_timezone="Europe/Zurich"
        )

        assert export == 1000  # 10 × 100Wh

    def test_no_export_when_battery_not_full(self):
        """No export counted when SOC < 99.9%."""
        times = pd.date_range(
            start=datetime.now(timezone.utc).replace(hour=10),
            periods=5,
            freq="15min"
        )
        simulation = pd.DataFrame({
            "soc_percent": [50, 60, 70, 80, 90],
            "net_wh": [100, 100, 100, 100, 100],
        }, index=times)

        export = calculate_grid_export_before_evening(
            simulation, evening_hour=18, local_timezone="Europe/Zurich"
        )

        assert export == 0

    def test_export_not_counted_after_evening(self):
        """Export after evening hour not counted."""
        times = pd.date_range(
            start=datetime.now(timezone.utc).replace(hour=17),
            periods=5,
            freq="15min"
        )
        simulation = pd.DataFrame({
            "soc_percent": [100, 100, 100, 100, 100],
            "net_wh": [200, 200, 200, 200, 200],
        }, index=times)

        export = calculate_grid_export_before_evening(
            simulation, evening_hour=18, local_timezone="Europe/Zurich"
        )

        assert export == 0

    def test_empty_simulation_returns_zero(self):
        export = calculate_grid_export_before_evening(
            pd.DataFrame(), evening_hour=18, local_timezone="Europe/Zurich"
        )

        assert export == 0


class TestMinSocCheck:
    """RED when SOC with appliance dips below reserve."""

    def test_red_when_soc_dips_below_reserve(self):
        """SOC dips to 0% → with appliance still 0% < 10% → RED."""
        signal = calculate_appliance_signal(
            current_pv_w=1000,
            current_load_w=800,
            simulation=make_simulation_with_dip(final_soc_percent=48, min_soc_percent=0),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "red"

    def test_red_when_soc_dips_just_below_threshold(self):
        """SOC 24% - 15% = 9% < 10% reserve → RED."""
        signal = calculate_appliance_signal(
            current_pv_w=1000,
            current_load_w=800,
            simulation=make_simulation_with_dip(final_soc_percent=48, min_soc_percent=24),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "red"

    def test_orange_when_soc_stays_above_threshold(self):
        """SOC 30% - 15% = 15% ≥ 10% reserve → ORANGE."""
        signal = calculate_appliance_signal(
            current_pv_w=1000,
            current_load_w=800,
            simulation=make_simulation_with_dip(final_soc_percent=30, min_soc_percent=30),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "orange"

    def test_orange_when_min_soc_exactly_at_threshold(self):
        """SOC 25% - 15% = 10% = reserve → ORANGE (>= check)."""
        signal = calculate_appliance_signal(
            current_pv_w=1000,
            current_load_w=800,
            simulation=make_simulation_with_dip(final_soc_percent=30, min_soc_percent=25),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=10,
        )

        assert signal.signal == "orange"


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
        """Load > PV (negative excess) → check SOC with appliance."""
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
        # Min SOC 30% - 15% = 15% ≥ 10% → ORANGE
        assert signal.signal == "orange"
        assert signal.excess_power_w == -1500

    def test_zero_reserve_percent(self):
        """Zero reserve → only need SOC > appliance%."""
        signal = calculate_appliance_signal(
            current_pv_w=0,
            current_load_w=500,
            simulation=make_simulation(16),
            appliance_power_w=2500,
            appliance_energy_wh=1500,
            capacity_wh=10000,
            reserve_percent=0,
        )

        # 16% - 15% = 1% ≥ 0% → ORANGE
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
            reserve_percent=30,
        )

        # Min SOC 40% - 15% = 25% < 30% reserve → RED
        assert signal.signal == "red"


class TestGetFinalSocPercent:
    """Test the get_final_soc_percent helper function."""

    def test_returns_last_value(self):
        sim = make_simulation(42)
        result = get_final_soc_percent(sim)
        assert abs(result - 42) < 0.1

    def test_empty_dataframe_returns_zero(self):
        result = get_final_soc_percent(pd.DataFrame())
        assert result == 0

    def test_missing_column_returns_zero(self):
        bad_df = pd.DataFrame({"other": [1, 2, 3]})
        result = get_final_soc_percent(bad_df)
        assert result == 0


class TestGetMinSocPercent:
    """Test the get_min_soc_percent helper function."""

    def test_returns_minimum_value(self):
        sim = make_simulation_with_dip(final_soc_percent=48, min_soc_percent=5)
        result = get_min_soc_percent(sim)
        assert abs(result - 5) < 0.1

    def test_empty_dataframe_returns_zero(self):
        result = get_min_soc_percent(pd.DataFrame())
        assert result == 0

    def test_missing_column_returns_zero(self):
        bad_df = pd.DataFrame({"other": [1, 2, 3]})
        result = get_min_soc_percent(bad_df)
        assert result == 0


class TestApplianceSignalDataclass:
    """Test ApplianceSignal dataclass."""

    def test_dataclass_fields(self):
        """ApplianceSignal has all required fields."""
        signal = ApplianceSignal(
            signal="green",
            reason="Test",
            excess_power_w=1000,
            min_soc_percent=50,
        )

        assert signal.signal == "green"
        assert signal.reason == "Test"
        assert signal.excess_power_w == 1000
        assert signal.min_soc_percent == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
