"""Tests for the export-peak-shaving day mode + gate (FSD 4.2.3).

The shave-vs-car-day choice is decided once per day at shaving_decision_hour
and latched (`_update_shaving_day_mode`); `_charge_gate_active()` is true only
on a shaving day. The decision keys off actual car presence
(binary_sensor.car_ready), NOT the wallbox↔server WebSocket link
(binary_sensor.wallbox_connected, ~always "on" whenever the wallbox is
powered).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run import EnergyManager
from src.battery_optimizer import BatteryOptimizer

MINIMAL_OPTIONS = {
    "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "test"},
    "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
    "battery": {"capacity_kwh": 10.0, "max_discharge_w": 5000},
    "tariff": {},
    "ev_charging": {"enabled": True},
    "schedule": {"update_interval_minutes": 15},
}


@dataclass
class FakeTariff:
    is_cheap_now: bool = False


@pytest.fixture()
def manager():
    with patch("run.ForecastReader"), patch("run.SimulationWriter"), patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)
    mgr.ha_client = MagicMock()
    mgr.optimizer = MagicMock()
    return mgr


def _wire(
    manager,
    *,
    car_ready: str,
    wallbox_connected: str = "on",
    car_soc: float | None = None,
    target: float | None = None,
) -> None:
    def _get_state(entity):
        if entity == manager.car_ready_entity:
            return {"state": car_ready}
        if entity == manager.wallbox_connected_entity:
            return {"state": wallbox_connected}
        return {"state": "unknown"}

    def _sensor(entity):
        if entity == manager.smart_car_soc_entity:
            return car_soc
        if entity == manager.car_charging_max_entity:
            return target
        return None

    manager.ha_client.get_state.side_effect = _get_state
    manager.ha_client.get_sensor_value.side_effect = _sensor


# Decision-hour times (Europe/Zurich = UTC+2 in June).
_AT_DECISION = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)      # 12:00 local ≥ 8
_BEFORE_DECISION = datetime(2026, 6, 7, 4, 0, tzinfo=UTC)   # 06:00 local < 8


class TestDayModeDecision:
    """The shave-vs-car-day choice is decided once per day at
    shaving_decision_hour and latched (FSD 4.2.3)."""

    def test_car_absent_at_decision_is_car_day(self, manager) -> None:
        # WS link up but no car plugged in → car day (battery charges greedily).
        _wire(manager, car_ready="off", wallbox_connected="on")
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        assert manager._charge_gate_active() is False

    def test_car_below_target_at_decision_is_car_day(self, manager) -> None:
        _wire(manager, car_ready="on", car_soc=50.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        assert manager._charge_gate_active() is False

    def test_car_connected_and_full_at_decision_is_shaving_day(self, manager) -> None:
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "shaving_day"
        assert manager._charge_gate_active() is True

    def test_before_decision_hour_defaults_car_day(self, manager) -> None:
        # Even a full car: before the decision hour nothing is committed yet.
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_BEFORE_DECISION)
        assert manager._shaving_day_mode == "car_day"
        assert manager._shaving_decision_date is None

    def test_latch_holds_when_car_fills_later_same_day(self, manager) -> None:
        # At the decision hour the car still needs energy → car day …
        _wire(manager, car_ready="on", car_soc=50.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        # … later the car reaches its target, but the latch does NOT re-flip.
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)
        later = datetime(2026, 6, 7, 15, 0, tzinfo=UTC)  # 17:00 local
        manager._update_shaving_day_mode(later)
        assert manager._shaving_day_mode == "car_day"

    def test_decision_recomputed_next_day(self, manager) -> None:
        _wire(manager, car_ready="off")
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        # Next day the car is full at the decision hour → shaving day.
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)
        next_day = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
        manager._update_shaving_day_mode(next_day)
        assert manager._shaving_day_mode == "shaving_day"


def _forecast(now_utc: datetime, nets: list[float]) -> pd.DataFrame:
    """Build a 15-min net_energy_wh forecast starting at now_utc (UTC)."""
    idx = pd.date_range(start=now_utc, periods=len(nets), freq="15min", tz="UTC")
    return pd.DataFrame({"net_energy_wh": nets}, index=idx)


class TestMarginalDayGate:
    """B0 marginal-day gate (FSD 4.2.3): only shave when the battery is
    forecast to reach full today under the conservative p10-PV forecast.

    The gate just runs a greedy sim over whatever forecast it is handed; the
    p10-vs-p50 conservatism is applied at the fetch layer (run loop), so these
    tests pass the already-conservative forecast directly.
    """

    def _real_optimizer(self, manager) -> None:
        manager.optimizer = BatteryOptimizer(capacity_wh=10000, max_charge_w=5000)

    def test_abundant_day_fills_today(self, manager) -> None:
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [1500.0] * 64)  # strong surplus → fills today
        assert manager._will_fill_today(50.0, fc, now) is True

    def test_marginal_day_never_fills(self, manager) -> None:
        self._real_optimizer(manager)
        # 16:00 UTC = 18:00 Swiss; trickle surplus, short window → never full.
        now = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)
        fc = _forecast(now, [50.0] * 24)
        assert manager._will_fill_today(50.0, fc, now) is False

    def test_empty_gate_forecast_is_marginal(self, manager) -> None:
        """No conservative forecast → treat as marginal (never defer blindly)."""
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        assert manager._will_fill_today(50.0, pd.DataFrame(), now) is False

    def test_marginal_day_routes_to_greedy_charge(self, manager) -> None:
        """End-to-end: no car + marginal day → charge greedily at full power."""
        self._real_optimizer(manager)
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)  # full → shaving day
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)
        fc = _forecast(now, [50.0] * 24)  # marginal → never fills today

        manager.control_battery_charge(50.0, fc, fc, now)

        assert manager._charge_use_case == "B"
        assert manager._charge_action == "charging"
        assert "marginal" in manager._charge_reason
        # Released to full max_charge_w (not the gentle shaving power).
        manager.ha_client.set_number.assert_called_once_with(
            manager.charge_control_entity, manager.charge_max_w, max_retries=5
        )

    def test_abundant_day_routes_to_shaving(self, manager) -> None:
        """End-to-end: no car + abundant day → gate passes to the water-fill
        (not the greedy 'marginal' path)."""
        self._real_optimizer(manager)
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)  # full → shaving day
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        # Low surplus now, big peak later → fills today, water-fill defers now.
        fc = _forecast(now, [200.0] + [3000.0] * 40 + [200.0] * 23)

        manager.control_battery_charge(50.0, fc, fc, now)

        assert manager._charge_use_case == "B"
        assert "marginal" not in manager._charge_reason  # gate let it through

    def test_fills_but_below_margin_is_marginal(self, manager) -> None:
        """Fills today but surplus only just exceeds headroom (< ×1.2 margin)
        → treated as marginal (no real peak to shave)."""
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        # SOC 50% → headroom 5000 Wh. ~5200 Wh surplus fills it (×0.95 eff →
        # ~99.4%) but is under the 1.2 margin (6000 Wh) → marginal.
        fc = _forecast(now, [1300.0] * 4 + [0.0] * 12)
        assert manager._will_fill_today(50.0, fc, now) is False

    def test_fills_with_margin_is_abundant(self, manager) -> None:
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [1300.0] * 8 + [0.0] * 8)  # ~10.4 kWh >> 6 kWh margin
        assert manager._will_fill_today(50.0, fc, now) is True

    def test_charge_target_hold_defers(self, manager) -> None:
        """Topic 3 (FSD 4.2.4): at/above the charge target → hold (limit 0)."""
        self._real_optimizer(manager)
        _wire(manager, car_ready="off")
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = True
        manager._battery_target_soc = 50.0
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)

        manager.control_battery_charge(60.0, fc, fc, now)

        assert manager._charge_action == "deferred"
        assert "charge target" in manager._charge_reason
        manager.ha_client.set_number.assert_called_once_with(
            manager.charge_control_entity, 0, max_retries=5
        )

    def test_below_charge_target_does_not_hold(self, manager) -> None:
        """SOC below the charge target → normal logic runs (not a hold)."""
        self._real_optimizer(manager)
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)  # shaving day
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = True
        manager._battery_target_soc = 90.0
        now = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)
        fc = _forecast(now, [50.0] * 24)  # marginal → greedy, not a hold

        manager.control_battery_charge(50.0, fc, fc, now)

        assert "charge target" not in manager._charge_reason

    def test_stale_forecast_routes_to_greedy(self, manager) -> None:
        """Fail-safe: a stale PV forecast → greedy charging, never shave."""
        self._real_optimizer(manager)
        _wire(manager, car_ready="on", car_soc=80.0, target=80.0)  # shaving day
        manager.ha_client.set_number.return_value = (True, None)
        manager._forecast_fresh = False
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)  # abundant, but forecast not trusted

        manager.control_battery_charge(50.0, fc, fc, now)

        assert manager._charge_action == "charging"
        assert "stale" in manager._charge_reason
        manager.ha_client.set_number.assert_called_once_with(
            manager.charge_control_entity, manager.charge_max_w, max_retries=5
        )
