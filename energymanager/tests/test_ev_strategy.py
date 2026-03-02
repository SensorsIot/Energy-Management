"""
Tests for surplus-based EV charging strategy (FSD 4.5.6).
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
    """Return a Wednesday at noon Swiss (11:00 UTC), guaranteed weekday expensive tariff."""
    return datetime(2026, 3, 4, 12, 0, 0, tzinfo=SWISS_TZ).astimezone(ZoneInfo("UTC"))


# ---------------------------------------------------------------------------
# Surplus snap: picks correct amp level based on surplus
# ---------------------------------------------------------------------------

class TestSurplusSnap:
    def test_surplus_5000_picks_8a(self):
        """5000 W surplus → 8A (8×230×3 = 5520 W), not 16A."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1000)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
            surplus_power_w=5000,
        )
        assert result.amps == 8
        assert result.power_w == 8 * 230 * 3  # 5520 W

    def test_surplus_below_min_uses_min(self):
        """2000 W surplus (< 6A×230×3=4140) → snaps to 6A = 4140 W."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1000)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
            surplus_power_w=2000,
        )
        assert result.amps == 6
        assert result.power_w == 6 * 230 * 3  # 4140 W

    def test_surplus_above_max_uses_max(self):
        """12000 W surplus (> 16A×230×3=11040) → caps at 16A = 11040 W."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1000)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
            surplus_power_w=12000,
        )
        assert result.amps == 16
        assert result.power_w == 16 * 230 * 3  # 11040 W

    def test_surplus_exact_step_boundary(self):
        """Surplus exactly on a step boundary (6900 = 10A×230×3) → picks 10A."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=1000)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
            surplus_power_w=6900,
        )
        assert result.amps == 10
        assert result.power_w == 6900


# ---------------------------------------------------------------------------
# Battery protection: steps down when SOC target at risk
# ---------------------------------------------------------------------------

class TestBatteryProtection:
    def test_tight_battery_steps_down(self):
        """Surplus 8000 W → candidate 12A, but only 3 forecast slots limit recovery.

        10 kWh battery at 82% = 8200 Wh. 3 slots of 500 Wh.
        Baseline: 8200 + 3×500×0.95 = 9625 → 96.25%. Protection = 80%.
        12A = 8280 W → 2070 Wh from slot 0 → net = -1570 → SOC ~65.5%.
        2 recovery slots add 950 Wh → ~75%. < 80% → 12A fails.
        8A = 5520 W → 1380 Wh from slot 0 → net = -880 → SOC ~72.7%.
        2 recovery slots add 950 Wh → ~82.2%. ≥ 80% → 8A passes.
        """
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=3, net_wh_per_slot=500)
        result = strategy.calculate(
            current_soc=82, forecast=forecast, now=now,
            surplus_power_w=8000,
            protection_soc_percent=80,
        )
        # Candidate 12A fails, should step down to ~8A
        assert 0 < result.amps < 12
        assert result.power_w == result.amps * 230 * 3

    def test_very_tight_battery_returns_zero(self):
        """Surplus 5000 W but battery so tight even min_amps fails → 0."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        # Tiny surplus: 30 Wh/slot × 40 slots × 0.95 = 1140 Wh charge
        # Starting at 78% = 7800 Wh → ends at ~8940 Wh ≈ 89% baseline
        # Protection = min(80, 89) = 80%
        # Min EV (4140 W) subtracts 1035 Wh from slot 0 → drops below 80%
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=30)
        result = strategy.calculate(
            current_soc=78, forecast=forecast, now=now,
            surplus_power_w=5000,
            protection_soc_percent=80,
        )
        assert result.power_w == 0
        assert result.amps == 0
        assert "protection fail" in result.reason


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_forecast_returns_zero(self):
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = pd.DataFrame()
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
            surplus_power_w=5000,
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
            surplus_power_w=5000,
            protection_soc_percent=60,
        )
        assert result.protection_target <= 60

    def test_deficit_baseline_drops_protection(self):
        """With deficit forecast, protection target drops to baseline SOC at 21:00."""
        opt = _make_optimizer()
        strategy = EVChargingStrategy(optimizer=opt)
        now = _noon_utc()
        forecast = _make_forecast(now, slots=40, net_wh_per_slot=-200)
        result = strategy.calculate(
            current_soc=50, forecast=forecast, now=now,
            surplus_power_w=5000,
        )
        assert result.protection_target < 80
        assert result.baseline_soc_2100 < 50


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
            surplus_power_w=3000,
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
            surplus_power_w=5000,
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
