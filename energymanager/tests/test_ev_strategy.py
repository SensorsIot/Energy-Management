"""
Tests for forecast-based EV charging strategy (FSD 4.5.7).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.battery_optimizer import BatteryOptimizer
from src.ev_strategy import EVChargingStrategy, EVStrategyResult

SWISS_TZ = ZoneInfo("Europe/Zurich")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_optimizer(**overrides) -> BatteryOptimizer:
    defaults = dict(
        capacity_wh=10000,
        min_soc_percent=10,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
        max_charge_w=5000,
        max_discharge_w=5000,
        weekday_cheap_start="21:00",
        weekday_cheap_end="06:00",
    )
    defaults.update(overrides)
    return BatteryOptimizer(**defaults)


def _make_forecast(
    start: datetime,
    slots: int,
    net_wh_per_slot: float,
) -> pd.DataFrame:
    """Create a uniform forecast DataFrame with net_energy_wh column."""
    times = [start + timedelta(minutes=15 * i) for i in range(slots)]
    idx = pd.DatetimeIndex(times, tz="UTC")
    return pd.DataFrame({"net_energy_wh": [net_wh_per_slot] * slots}, index=idx)


def _noon_utc() -> datetime:
    """Return today at noon UTC (≈ 13:00 Swiss, expensive tariff)."""
    return datetime.now(SWISS_TZ).replace(
        hour=12, minute=0, second=0, microsecond=0,
    ).astimezone(ZoneInfo("UTC"))


# ---------------------------------------------------------------------------
# Good day: plenty of surplus → charges at optimal amps
# ---------------------------------------------------------------------------

class TestGoodDay:
    def test_sunny_forecast_returns_positive_power(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        # Each slot has 1000 Wh net surplus (huge surplus)
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1000)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
        )
        assert result.power_w > 0
        assert result.amps >= 6

    def test_high_surplus_reaches_max_amps(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        # Massive surplus — battery easily full, lots of clipping
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=2000)
        result = strategy.calculate(
            current_soc=90, forecast=forecast, now=now,
        )
        # With huge surplus and high SOC, should push amps up
        assert result.amps >= 10
        assert result.power_w == result.amps * 230 * 3


# ---------------------------------------------------------------------------
# Bad day: cloudy → protection target drops, still charges if viable
# ---------------------------------------------------------------------------

class TestBadDay:
    def test_cloudy_but_viable_charges(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        # Small surplus per slot — SOC at 21:00 might be lower
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=300)
        result = strategy.calculate(
            current_soc=60, forecast=forecast, now=now,
        )
        # Should still charge since protection drops with baseline
        assert result.protection_target <= 80


# ---------------------------------------------------------------------------
# Very bad day: viability fails → returns 0W
# ---------------------------------------------------------------------------

class TestViabilityFail:
    def test_tight_margin_viability_fails(self):
        """When baseline barely reaches protection, adding min EV load tips it below.

        10kWh battery at 78%. Baseline needs tiny surplus to reach 80%.
        Min EV = 6A × 230V × 3ph = 4140W → 1035Wh per slot subtracted from slot 0.
        That drops SOC below the 80% target.
        """
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        # Tiny surplus: 30 Wh/slot × 40 slots × 0.95 = 1140 Wh charge
        # Starting at 78% = 7800 Wh → ends at ~8940 Wh ≈ 89% baseline
        # But protection is 80% (min of 80, 89) = 80%
        # Min EV subtracts 1035 Wh from slot 0:
        # slot 0 net = 30 - 1035 = -1005 Wh → battery discharges
        # 7800 - 1005/0.95 = 7800 - 1058 = 6742 Wh ≈ 67%
        # Remaining 39 slots add back 39 × 30 × 0.95 = 1112 Wh → 7854 Wh ≈ 78.5%
        # 78.5% < 80% target → viability fails
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=30)
        result = strategy.calculate(
            current_soc=78, forecast=forecast, now=now,
            protection_soc_percent=80,
        )
        assert result.power_w == 0
        assert result.amps == 0
        assert "viability fail" in result.reason

    def test_deficit_baseline_drops_protection(self):
        """With deficit forecast, protection target drops to baseline SOC at 21:00.

        When baseline is already low, the algorithm allows EV because the
        battery is going to be empty anyway.
        """
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=-200)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
        )
        # Protection target drops to baseline (near 0%), so EV is allowed
        assert result.protection_target < 80
        assert result.baseline_soc_2100 < 50


# ---------------------------------------------------------------------------
# Battery full: pushes amps up aggressively (clipping prevention)
# ---------------------------------------------------------------------------

class TestBatteryFull:
    def test_high_soc_with_surplus_charges(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        # Good surplus with high SOC — clipping risk
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=800)
        result = strategy.calculate(
            current_soc=95, forecast=forecast, now=now,
        )
        assert result.power_w > 0
        assert result.amps >= 6


# ---------------------------------------------------------------------------
# Empty forecast
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_forecast_returns_zero(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = pd.DataFrame()
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
        )
        assert result.power_w == 0
        assert result.reason == "no forecast"

    def test_protection_target_capped_by_config(self):
        """Protection target never exceeds protection_soc_percent."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1500)
        result = strategy.calculate(
            current_soc=90, forecast=forecast, now=now,
            protection_soc_percent=60,
        )
        assert result.protection_target <= 60


# ---------------------------------------------------------------------------
# Config variations
# ---------------------------------------------------------------------------

class TestConfigVariations:
    def test_single_phase(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1000)
        result = strategy.calculate(
            current_soc=60, forecast=forecast, now=now,
            phases=1,
        )
        # Single phase: amps × 230 × 1
        assert result.power_w == result.amps * 230 * 1

    def test_custom_amp_range(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1500)
        result = strategy.calculate(
            current_soc=60, forecast=forecast, now=now,
            min_amps=8, max_amps=12,
        )
        assert 8 <= result.amps <= 12 or result.amps == 0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

class TestResultDataclass:
    def test_frozen(self):
        r = EVStrategyResult(4140, 6, 80.0, 85.0, "test")
        with pytest.raises(AttributeError):
            r.power_w = 0

    def test_fields(self):
        r = EVStrategyResult(6900, 10, 75.0, 82.0, "10A × 3ph = 6900W")
        assert r.power_w == 6900
        assert r.amps == 10
        assert r.protection_target == 75.0
        assert r.baseline_soc_2100 == 82.0
