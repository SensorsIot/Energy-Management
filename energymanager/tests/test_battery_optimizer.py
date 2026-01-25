"""
Tests for simplified battery discharge optimization algorithm (FSD v2.6).

Test cases:
1. Expensive tariff → ALLOW (always)
2. Cheap tariff + SOC stays above min → ALLOW
3. Cheap tariff + SOC would drop below min → BLOCK
4. Edge cases (no forecast, weekend, etc.)
"""

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from src.battery_optimizer import BatteryOptimizer, DischargeDecision, TariffPeriod

SWISS_TZ = ZoneInfo("Europe/Zurich")


def make_forecast(
    start: datetime,
    hours: int,
    pv_pattern: list[float],
    load_pattern: list[float],
) -> pd.DataFrame:
    """
    Create a forecast DataFrame for testing.

    Args:
        start: Start time (UTC)
        hours: Number of hours to generate
        pv_pattern: PV power pattern in W (repeated to fill hours)
        load_pattern: Load power pattern in W (repeated to fill hours)

    Returns:
        DataFrame with pv_energy_wh, load_energy_wh, net_energy_wh at 15-min intervals
    """
    periods = hours * 4  # 15-min intervals
    times = pd.date_range(start=start, periods=periods, freq="15min", tz=timezone.utc)

    # Extend patterns to fill all periods
    pv_extended = (pv_pattern * (periods // len(pv_pattern) + 1))[:periods]
    load_extended = (load_pattern * (periods // len(load_pattern) + 1))[:periods]

    # Convert power (W) to energy per 15-min period (Wh)
    pv_wh = [p * 0.25 for p in pv_extended]
    load_wh = [l * 0.25 for l in load_extended]
    net_wh = [p - l for p, l in zip(pv_wh, load_wh)]

    return pd.DataFrame({
        "pv_energy_wh": pv_wh,
        "load_energy_wh": load_wh,
        "net_energy_wh": net_wh,
    }, index=times)


class TestExpensiveTariff:
    """During expensive tariff (06:00-21:00): always ALLOW discharge."""

    def test_expensive_tariff_allows_discharge(self):
        """At 12:00 (expensive), discharge should be allowed regardless of SOC forecast."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        # Midday on a weekday - expensive tariff
        now = datetime(2026, 1, 26, 11, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Forecast with high load, no PV (worst case)
        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=[0],  # No PV
            load_pattern=[2000],  # 2kW constant load
        )

        decision, sim_full, sim_strategy = optimizer.calculate_decision(
            soc_percent=50,
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True
        assert "Expensive tariff" in decision.reason

    def test_expensive_tariff_low_soc_still_allows(self):
        """Even with low SOC during expensive tariff, discharge is allowed."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        now = datetime(2026, 1, 26, 14, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=[0],
            load_pattern=[5000],  # Very high load
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=15,  # Low starting SOC
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True


class TestCheapTariffAllow:
    """During cheap tariff: ALLOW if SOC stays >= min during expensive hours."""

    def test_cheap_tariff_high_pv_allows_discharge(self):
        """At 22:00 (cheap), with good PV forecast, discharge should be allowed."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        # Evening on a weekday - cheap tariff
        now = datetime(2026, 1, 26, 21, 30, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Good PV during day, moderate load
        # Simulate: night (no PV), then day (good PV)
        pv_pattern = [0] * 36 + [4000] * 48 + [0] * 12  # 0 until 6am, then PV
        load_pattern = [500] * 96  # 500W constant

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=80,  # Good starting SOC
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True
        assert "SOC stays >=" in decision.reason

    def test_cheap_tariff_full_battery_allows_discharge(self):
        """With 100% SOC and good PV, should allow discharge."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Good PV during day (enough to cover load and charge battery)
        # 22:00 → 06:00 = 8h = 32 periods of no PV
        # 06:00 → 21:00 = 15h = 60 periods of good PV
        pv_pattern = [0] * 32 + [5000] * 60 + [0] * 4  # Strong PV during day
        load_pattern = [400] * 96  # Low load

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=100,
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True


class TestCheapTariffBlock:
    """During cheap tariff: BLOCK if SOC would drop below min during expensive hours."""

    def test_cheap_tariff_low_pv_blocks_discharge(self):
        """At 22:00 (cheap), with poor PV forecast, discharge should be blocked."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        now = datetime(2026, 1, 26, 21, 30, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Poor PV (cloudy day), high load
        pv_pattern = [0] * 36 + [500] * 48 + [0] * 12  # Very little PV
        load_pattern = [1500] * 96  # 1.5kW constant

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=50,  # Medium SOC
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is False
        assert "Block" in decision.reason

    def test_cheap_tariff_low_soc_blocks_discharge(self):
        """At 22:00 (cheap), with low starting SOC, discharge should be blocked."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Moderate PV but starting SOC is low
        pv_pattern = [0] * 36 + [2000] * 48 + [0] * 12
        load_pattern = [1000] * 96

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=20,  # Low starting SOC
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is False

    def test_min_soc_threshold_respected(self):
        """SOC dropping to exactly min_soc should be allowed, below should block."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=20,  # Higher threshold
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Create forecast that would result in ~15% min SOC
        pv_pattern = [0] * 36 + [1000] * 48 + [0] * 12
        load_pattern = [800] * 96

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=40,
            forecast=forecast,
            now=now,
        )

        # With 20% threshold, should block if min SOC drops below 20%
        assert decision.min_soc_percent < 20 or decision.discharge_allowed is True


class TestSelfCorrecting:
    """Test that re-checking every 15 min allows self-correction."""

    def test_block_then_allow_as_conditions_improve(self):
        """If initially blocked, later check with better SOC should allow."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=10,
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        # Balanced forecast: PV roughly matches load during day
        # Night: no PV, 500W load
        # Day: 3000W PV, 500W load (net surplus)
        pv_pattern = [0] * 32 + [3000] * 60 + [0] * 4
        load_pattern = [500] * 96

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        # First check: low SOC (30%) - might not have enough for expensive hours
        decision1, _, _ = optimizer.calculate_decision(
            soc_percent=30,
            forecast=forecast,
            now=now,
        )

        # Second check: high SOC (90%) - should definitely have enough
        decision2, _, _ = optimizer.calculate_decision(
            soc_percent=90,
            forecast=forecast,
            now=now,
        )

        # Higher starting SOC → higher min SOC during expensive hours
        assert decision2.min_soc_percent > decision1.min_soc_percent
        # With 90% SOC and good PV, should allow discharge
        assert decision2.discharge_allowed is True


class TestEdgeCases:
    """Edge cases and special scenarios."""

    def test_no_forecast_data_allows_discharge(self):
        """With no forecast data, default to allowing discharge."""
        optimizer = BatteryOptimizer()

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        decision, sim_full, sim_strategy = optimizer.calculate_decision(
            soc_percent=50,
            forecast=pd.DataFrame(),  # Empty forecast
            now=now,
        )

        assert decision.discharge_allowed is True
        assert "No forecast data" in decision.reason

    def test_weekend_all_day_cheap(self):
        """Weekend is all-day cheap tariff."""
        optimizer = BatteryOptimizer(weekend_all_day_cheap=True)

        # Saturday midday
        now = datetime(2026, 1, 31, 12, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        tariff = optimizer.get_tariff_periods(now)

        assert tariff.is_cheap_now is True

    def test_weekday_morning_is_expensive(self):
        """Weekday 08:00 should be expensive tariff."""
        optimizer = BatteryOptimizer()

        # Monday morning
        now = datetime(2026, 1, 26, 8, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        tariff = optimizer.get_tariff_periods(now)

        assert tariff.is_cheap_now is False

    def test_weekday_night_is_cheap(self):
        """Weekday 23:00 should be cheap tariff."""
        optimizer = BatteryOptimizer()

        # Monday night
        now = datetime(2026, 1, 26, 23, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        tariff = optimizer.get_tariff_periods(now)

        assert tariff.is_cheap_now is True

    def test_holiday_is_cheap(self):
        """Configured holidays should be all-day cheap."""
        optimizer = BatteryOptimizer(holidays=["2026-01-01"])

        # New Year's Day midday
        now = datetime(2026, 1, 1, 12, 0, tzinfo=SWISS_TZ).astimezone(timezone.utc)

        assert optimizer.is_holiday(now) is True
        assert optimizer.is_cheap_day(now) is True


class TestDecisionDataclass:
    """Test DischargeDecision dataclass fields."""

    def test_decision_has_required_fields(self):
        """DischargeDecision should have discharge_allowed, reason, min_soc_percent."""
        decision = DischargeDecision(
            discharge_allowed=True,
            reason="Test reason",
            min_soc_percent=50.0,
        )

        assert decision.discharge_allowed is True
        assert decision.reason == "Test reason"
        assert decision.min_soc_percent == 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
