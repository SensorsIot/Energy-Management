"""
Tests for EV charging mode calculation (FSD 4.5.4).

Test cases:
1. Solar: mode is solar → idle status, 0W (handled by solar excess logic)
2. Error: wallbox offline / faulted / car not connected
3. Ready: immediate or cheap + car plugged in + conditions met
4. Waiting: cheap mode + expensive tariff
5. Charging: actively charging
6. Auto-revert: idle for > timeout → revert to solar
7. Immediate mode: full power regardless of tariff
"""

import pytest

from src.ev_goal_mode import calculate_charging_mode, ChargingModeResult


class TestSolarMode:
    """Solar mode → idle (handled by ev_charging.py)."""

    def test_solar_mode(self):
        result = calculate_charging_mode(
            ev_charging_mode="solar",
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0

    def test_solar_mode_car_connected(self):
        """Solar mode with car connected → still idle (solar logic handles it)."""
        result = calculate_charging_mode(
            ev_charging_mode="solar",
            is_cheap_tariff=True,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0

    def test_unknown_mode_treated_as_solar(self):
        """Unknown mode treated as solar."""
        result = calculate_charging_mode(
            ev_charging_mode="off",
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0


class TestError:
    """Error states when mode active but cannot charge."""

    def test_wallbox_offline(self):
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=False,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"
        assert "offline" in result.status_text.lower()

    def test_wallbox_faulted(self):
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Faulted",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"
        assert "fault" in result.status_text.lower()

    def test_car_not_connected(self):
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"
        assert "not connected" in result.status_text.lower()

    def test_cheap_mode_wallbox_offline(self):
        """Cheap mode also blocked if wallbox offline."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=False,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"

    def test_immediate_car_not_connected(self):
        """Immediate mode also shows error if no car."""
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=False,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"


class TestReady:
    """Ready: mode active, car plugged in, can charge."""

    def test_cheap_mode_cheap_tariff(self):
        """Cheap mode + cheap tariff + car ready → ready + full power."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "ready"
        assert result.target_power_w == 11000

    def test_immediate_mode_expensive_tariff(self):
        """Immediate mode ignores tariff → ready + full power."""
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "ready"
        assert result.target_power_w == 11000


class TestWaiting:
    """Waiting: cheap mode armed but expensive tariff."""

    def test_cheap_mode_expensive_tariff(self):
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "waiting"
        assert result.target_power_w == 0
        assert "waiting" in result.status_text.lower()

    def test_cheap_mode_expensive_tariff_suspended(self):
        """Waiting even if wallbox in SuspendedEV state (below auto-reset timeout)."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=False,
            wallbox_status="SuspendedEV",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=2,
        )
        assert result.charge_status == "waiting"
        assert result.target_power_w == 0


class TestCharging:
    """Actively charging."""

    def test_charging_with_power(self):
        """Wallbox drawing power in Charging state."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Charging",
            wallbox_connected=True,
            wallbox_power_w=8500,
            idle_minutes=0,
        )
        assert result.charge_status == "charging"
        assert result.target_power_w == 11000

    def test_charging_via_immediate(self):
        """Immediate mode actively charging."""
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=False,
            wallbox_status="Charging",
            wallbox_connected=True,
            wallbox_power_w=10500,
            idle_minutes=0,
        )
        assert result.charge_status == "charging"
        assert result.target_power_w == 11000


class TestAutoRevert:
    """Auto-revert to solar after car stops drawing current."""

    def test_auto_revert_after_timeout(self):
        """Idle > 5 min + Finishing → auto-revert (idle status + revert flag)."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=6,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0
        assert "complete" in result.status_text.lower()
        assert result.revert_to_solar is True

    def test_no_revert_before_timeout(self):
        """Idle < 5 min → still ready/charging, not reverted."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=3,
        )
        # Should still be ready (cheap tariff, car connected)
        assert result.revert_to_solar is False

    def test_auto_revert_suspended_ev(self):
        """SuspendedEV + timeout → auto-revert."""
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=True,
            wallbox_status="SuspendedEV",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=10,
        )
        assert result.charge_status == "idle"
        assert "complete" in result.status_text.lower()
        assert result.revert_to_solar is True

    def test_auto_revert_exact_timeout(self):
        """Exactly at timeout boundary → triggers revert."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=5,
        )
        assert result.charge_status == "idle"
        assert result.revert_to_solar is True

    def test_custom_timeout(self):
        """Custom timeout of 10 minutes."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=7,
            auto_reset_timeout_min=10,
        )
        # 7 < 10 → should NOT revert
        assert result.revert_to_solar is False

    def test_no_revert_while_charging_status(self):
        """No auto-revert if wallbox status is still Charging (even at 0W briefly)."""
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Charging",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=10,
        )
        # Charging status with 0W → not auto-revert (transient state)
        assert result.revert_to_solar is False


class TestImmediateMode:
    """Immediate mode overrides tariff."""

    def test_immediate_during_expensive(self):
        """Immediate works during expensive tariff."""
        result = calculate_charging_mode(
            ev_charging_mode="immediate",
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.target_power_w == 11000


class TestCustomMaxPower:
    """Custom max power parameter."""

    def test_custom_max_power(self):
        result = calculate_charging_mode(
            ev_charging_mode="cheap",
            is_cheap_tariff=True,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
            max_power_w=7400,
        )
        assert result.target_power_w == 7400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
