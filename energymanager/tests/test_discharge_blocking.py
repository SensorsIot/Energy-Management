"""
Tests for two-flag battery discharge blocking (OR logic).

The battery discharge is blocked when EITHER:
- Battery protection blocks it (SOC forecast too low), OR
- EV is actively charging in immediate/cheap mode.

These tests verify:
1. _update_discharge_control() combines both flags correctly
2. run_optimization() sets the protection flag
3. control_ev_charging_mode() sets/clears the EV flag
4. Switching to solar mode clears the EV flag
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from run import EnergyManager
from src.ev_goal_mode import ChargingModeResult


# ---------------------------------------------------------------------------
# Minimal options to construct EnergyManager without real connections
# ---------------------------------------------------------------------------

MINIMAL_OPTIONS = {
    "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "test"},
    "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
    "battery": {"capacity_kwh": 10.0, "max_discharge_w": 5000},
    "tariff": {},
    "ev_charging": {"enabled": True},
    "schedule": {"update_interval_minutes": 15},
}


@dataclass
class FakeDecision:
    discharge_allowed: bool
    reason: str = "test"
    min_soc_percent: float = 50.0


@dataclass
class FakeTariff:
    is_cheap_now: bool = False
    cheap_start: object = None
    cheap_end: object = None
    target: object = None


# ---------------------------------------------------------------------------
# Fixture: a manager with mocked external dependencies
# ---------------------------------------------------------------------------

@pytest.fixture()
def manager():
    """Create an EnergyManager with mocked HA client and optimizer."""
    with patch("run.ForecastReader"), \
         patch("run.SimulationWriter"), \
         patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)

    mgr.ha_client = MagicMock()
    mgr.control_battery = MagicMock()
    mgr.optimizer = MagicMock()
    mgr.optimizer.get_tariff_periods.return_value = FakeTariff()
    return mgr


# ===================================================================
# _update_discharge_control — OR truth table
# ===================================================================


class TestUpdateDischargeControl:
    """Verify _update_discharge_control combines flags with OR logic."""

    def test_both_off_allows_discharge(self, manager):
        manager._discharge_blocked_by_protection = False
        manager._discharge_blocked_by_ev = False
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(True)

    def test_protection_blocks(self, manager):
        manager._discharge_blocked_by_protection = True
        manager._discharge_blocked_by_ev = False
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(False)

    def test_ev_blocks(self, manager):
        manager._discharge_blocked_by_protection = False
        manager._discharge_blocked_by_ev = True
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(False)

    def test_both_block(self, manager):
        manager._discharge_blocked_by_protection = True
        manager._discharge_blocked_by_ev = True
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(False)

    def test_no_call_when_unchanged(self, manager):
        """If last_discharge_allowed matches the computed value, skip."""
        manager.last_discharge_allowed = True
        manager._discharge_blocked_by_protection = False
        manager._discharge_blocked_by_ev = False
        manager._update_discharge_control()
        manager.control_battery.assert_not_called()

    def test_calls_on_transition_allow_to_block(self, manager):
        manager.last_discharge_allowed = True
        manager._discharge_blocked_by_ev = True
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(False)

    def test_calls_on_transition_block_to_allow(self, manager):
        manager.last_discharge_allowed = False
        manager._discharge_blocked_by_protection = False
        manager._discharge_blocked_by_ev = False
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(True)


# ===================================================================
# control_ev_charging_mode — EV flag management
# ===================================================================

def _stub_charging_result(target_power_w: float, **kwargs) -> ChargingModeResult:
    return ChargingModeResult(
        target_power_w=target_power_w,
        charge_status=kwargs.get("charge_status", "charging"),
        status_text=kwargs.get("status_text", "test"),
        reason=kwargs.get("reason", "test"),
        revert_to_solar=kwargs.get("revert_to_solar", False),
    )


def _setup_immediate_mode(manager, target_power_w: float):
    """Configure mocks so control_ev_charging_mode runs in immediate mode."""
    manager.ha_client.get_input_select.return_value = "immediate"
    manager.ha_client.get_state.return_value = {"state": "Charging"}
    manager.ha_client.get_sensor_value.return_value = 11000  # user limit
    manager.ha_client.set_sensor_state.return_value = True
    with patch("run.calculate_charging_mode", return_value=_stub_charging_result(target_power_w)):
        manager.control_ev_charging_mode(wallbox_connected=True, wallbox_power=target_power_w)


class TestEVFlagOnCharging:
    """EV flag is set when immediate/cheap mode charges with power > 0."""

    def test_immediate_charging_blocks_discharge(self, manager):
        assert manager._discharge_blocked_by_ev is False
        _setup_immediate_mode(manager, target_power_w=5000)
        assert manager._discharge_blocked_by_ev is True
        manager.control_battery.assert_called_with(False)

    def test_immediate_zero_power_clears_flag(self, manager):
        manager._discharge_blocked_by_ev = True
        manager.last_discharge_allowed = False
        _setup_immediate_mode(manager, target_power_w=0)
        assert manager._discharge_blocked_by_ev is False
        manager.control_battery.assert_called_with(True)

    def test_flag_not_toggled_when_already_set(self, manager):
        """No redundant control_battery calls when flag is already True."""
        manager._discharge_blocked_by_ev = True
        manager.last_discharge_allowed = False
        _setup_immediate_mode(manager, target_power_w=5000)
        # Flag already True, last_discharge_allowed already False → no call
        manager.control_battery.assert_not_called()

    def test_flag_not_toggled_when_already_clear(self, manager):
        """No redundant control_battery calls when flag is already False."""
        manager._discharge_blocked_by_ev = False
        manager.last_discharge_allowed = True
        _setup_immediate_mode(manager, target_power_w=0)
        # Flag already False, last_discharge_allowed already True → no call
        manager.control_battery.assert_not_called()


class TestSolarModeClearsEVFlag:
    """Switching to solar mode clears the EV discharge block."""

    def test_solar_mode_clears_ev_flag(self, manager):
        manager._discharge_blocked_by_ev = True
        manager.last_discharge_allowed = False
        manager.ha_client.get_input_select.return_value = "solar"
        manager.control_ev_charging_mode(wallbox_connected=True, wallbox_power=0)
        assert manager._discharge_blocked_by_ev is False
        manager.control_battery.assert_called_with(True)

    def test_solar_mode_noop_when_flag_already_clear(self, manager):
        manager._discharge_blocked_by_ev = False
        manager.last_discharge_allowed = True
        manager.ha_client.get_input_select.return_value = "solar"
        manager.control_ev_charging_mode(wallbox_connected=True, wallbox_power=0)
        assert manager._discharge_blocked_by_ev is False
        manager.control_battery.assert_not_called()


# ===================================================================
# Combined: protection + EV interaction
# ===================================================================


class TestCombinedBlocking:
    """Both flags interact correctly — discharge stays blocked until BOTH clear."""

    def test_ev_stops_but_protection_keeps_blocked(self, manager):
        """EV finishes charging but protection still blocks → stays blocked."""
        manager._discharge_blocked_by_protection = True
        manager._discharge_blocked_by_ev = True
        manager.last_discharge_allowed = False

        # EV stops → clears EV flag
        _setup_immediate_mode(manager, target_power_w=0)
        assert manager._discharge_blocked_by_ev is False
        assert manager._discharge_blocked_by_protection is True
        # Still blocked by protection → no call (unchanged)
        manager.control_battery.assert_not_called()

    def test_protection_clears_but_ev_keeps_blocked(self, manager):
        """Protection clears but EV still charging → stays blocked."""
        manager._discharge_blocked_by_protection = True
        manager._discharge_blocked_by_ev = True
        manager.last_discharge_allowed = False

        # Simulate optimizer clearing protection
        manager._discharge_blocked_by_protection = False
        manager._update_discharge_control()
        # Still blocked by EV → no call (unchanged)
        manager.control_battery.assert_not_called()

    def test_both_clear_allows_discharge(self, manager):
        """When both flags clear, discharge is allowed."""
        manager._discharge_blocked_by_protection = True
        manager._discharge_blocked_by_ev = True
        manager.last_discharge_allowed = False

        # Clear both
        manager._discharge_blocked_by_protection = False
        manager._discharge_blocked_by_ev = False
        manager._update_discharge_control()
        manager.control_battery.assert_called_once_with(True)

    def test_ev_starts_during_protection_block(self, manager):
        """EV starts charging while protection already blocks — stays blocked."""
        manager._discharge_blocked_by_protection = True
        manager.last_discharge_allowed = False

        _setup_immediate_mode(manager, target_power_w=5000)
        assert manager._discharge_blocked_by_ev is True
        # Already blocked → no call
        manager.control_battery.assert_not_called()
