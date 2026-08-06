"""Tests for the Topic 2 step-up suppression signal (FSD 4.3.7).

Step-up (one amp level above surplus, drawing the gap from the home battery) is
pointless when the conservative **p10** forecast already fills both the home
battery and the car by evening: the car lands at the same SOC either way, and
routing the gap through the home battery only pays its round-trip loss.

`_evaluate_both_full_by_evening` computes that signal every 15 min. These tests
verify it fires only when BOTH targets are met, and fails **open** (no
suppression) whenever the signal cannot be trusted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run import EnergyManager, SWISS_TZ


MINIMAL_OPTIONS = {
    "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "test"},
    "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
    "battery": {"capacity_kwh": 10.0, "max_discharge_w": 5000},
    "tariff": {},
    "ev_charging": {"enabled": True},
    "schedule": {"update_interval_minutes": 15},
}


@pytest.fixture()
def manager():
    with patch("run.ForecastReader"), \
         patch("run.SimulationWriter"), \
         patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)

    mgr.ha_client = MagicMock()
    mgr.smart_car_enabled = True
    mgr.smart_car_capacity_kwh = 50.0
    mgr.smart_car_charge_efficiency = 1.0
    mgr.capacity_wh = 10_000
    mgr._battery_target_soc = 90.0
    mgr._forecast_fresh = True
    # Car at 50%, car-side target 80%.
    mgr._read_car_soc_with_fallback = MagicMock(return_value=50.0)
    mgr.ha_client.get_sensor_value.return_value = 80.0
    return mgr


def forecast_from(net_wh_per_period: list[float], start_hour: int = 8):
    """Build a forecast frame of 15-min periods starting today at `start_hour`."""
    start = datetime.now(UTC).replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    idx = [start + timedelta(minutes=15 * i) for i in range(len(net_wh_per_period))]
    return pd.DataFrame({"net_energy_wh": net_wh_per_period}, index=idx)


class TestBothFullByEvening:
    def test_both_reach_targets_suppresses(self, manager) -> None:
        # House starts at 50% (5 kWh) with a 9 kWh ceiling → 4 kWh headroom.
        # 24 kWh total surplus: 4 kWh fills the house to its 90% target, the
        # remaining 20 kWh gives the car +40% → 90% (target 80%). Both met.
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._both_full_by_evening is True
        assert "ok" in manager._both_full_reason

    def test_car_short_keeps_step_up(self, manager) -> None:
        # 6 kWh total: house reaches its target, car only gets 2 kWh (+4% → 54%).
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 3), 50.0)
        assert manager._both_full_by_evening is False
        assert "car EOD 54%/80% (short)" in manager._both_full_reason

    def test_battery_short_keeps_step_up(self, manager) -> None:
        # 2 kWh only: house climbs 50%→70%, short of its 90% target.
        manager._evaluate_both_full_by_evening(forecast_from([1000] * 2), 50.0)
        assert manager._both_full_by_evening is False
        assert "short" in manager._both_full_reason

    def test_battery_peak_not_eod_counts(self, manager) -> None:
        """The battery hits its target midday then discharges into the evening —
        that still counts as 'reached', so an evening drain must not un-suppress."""
        # Fill both targets, then a long evening deficit drains the house to 0.
        manager._evaluate_both_full_by_evening(
            forecast_from([2000] * 12 + [-3000] * 4), 50.0
        )
        assert manager._both_full_by_evening is True

    def test_periods_after_midnight_are_ignored(self, manager) -> None:
        """Tomorrow's sun must not satisfy 'by evening'."""
        # Anchor to the real cutoff: 2 periods land before today's Swiss
        # midnight (2 kWh — not enough), the big block lands after it.
        cutoff = (
            datetime.now(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        before = [cutoff - timedelta(minutes=30), cutoff - timedelta(minutes=15)]
        after = [cutoff + timedelta(minutes=15 * i) for i in range(1, 9)]
        frame = pd.DataFrame(
            {"net_energy_wh": [1000, 1000] + [20000] * 8},
            index=before + after,
        )
        manager._evaluate_both_full_by_evening(frame, 50.0)
        assert manager._both_full_by_evening is False
        # Proves the pre-cutoff periods WERE evaluated (not an empty-window skip).
        assert "car EOD 50%/80% (short)" in manager._both_full_reason


class TestFailsOpen:
    """Whenever the signal cannot be computed, step-up keeps its normal gate."""

    def test_stale_forecast(self, manager) -> None:
        manager._forecast_fresh = False
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._both_full_by_evening is False
        assert manager._both_full_reason == "no fresh p10 forecast"

    def test_empty_forecast(self, manager) -> None:
        manager._evaluate_both_full_by_evening(pd.DataFrame(), 50.0)
        assert manager._both_full_by_evening is False

    def test_none_forecast(self, manager) -> None:
        manager._evaluate_both_full_by_evening(None, 50.0)
        assert manager._both_full_by_evening is False

    def test_no_house_soc(self, manager) -> None:
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), None)
        assert manager._both_full_by_evening is False
        assert manager._both_full_reason == "car sim unavailable"

    def test_car_disabled(self, manager) -> None:
        manager.smart_car_enabled = False
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._both_full_by_evening is False

    def test_car_soc_unknown(self, manager) -> None:
        manager._read_car_soc_with_fallback = MagicMock(return_value=None)
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._both_full_by_evening is False
        assert manager._both_full_reason == "car SOC/target unknown"

    def test_car_target_unreadable(self, manager) -> None:
        manager.ha_client.get_sensor_value.return_value = "unavailable"
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._both_full_by_evening is False
        assert manager._both_full_reason == "car SOC/target unknown"
