"""Tests for _charge_gate_active() — the export-peak-shaving gate (FSD 4.2.3).

Regression guard: the gate must key off actual car presence
(binary_sensor.car_ready), NOT the wallbox↔server WebSocket link
(binary_sensor.wallbox_connected). The latter is ~always "on" whenever the
wallbox is powered, so gating on it would keep the system permanently in
use case A and shaving (use case B) would never run.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from run import EnergyManager

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
    with patch("run.ForecastReader"), \
         patch("run.SimulationWriter"), \
         patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)
    mgr.ha_client = MagicMock()
    mgr.optimizer = MagicMock()
    return mgr


def _wire(manager, *, car_ready: str, wallbox_connected: str = "on",
          car_soc: float | None = None, target: float | None = None) -> None:
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


def test_no_car_shaving_runs_even_when_wallbox_link_up(manager) -> None:
    # The exact production bug: WS link "on" but no car plugged in.
    _wire(manager, car_ready="off", wallbox_connected="on")
    assert manager._charge_gate_active() is True  # free to shave


def test_car_present_not_full_blocks_shaving(manager) -> None:
    _wire(manager, car_ready="on", car_soc=50.0, target=80.0)
    assert manager._charge_gate_active() is False  # use case A — EV owns surplus


def test_car_present_but_full_allows_shaving(manager) -> None:
    _wire(manager, car_ready="on", car_soc=80.0, target=80.0)
    assert manager._charge_gate_active() is True  # car full → free to shave
