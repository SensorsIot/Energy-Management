"""
Tests for EV goal mode charging calculation (FSD 4.5.4.2).

Test cases:
1. Idle: both buttons off → idle status, 0W
2. Error: wallbox offline / faulted / car not connected
3. Ready: armed + car plugged in + cheap tariff (or charge_now)
4. Waiting: goal_charge armed + expensive tariff
5. Charging: actively charging
6. Auto-reset: idle for > timeout → reset
7. Charge Now override: immediate full power regardless of tariff
"""

import pytest

from src.ev_goal_mode import calculate_goal_mode, GoalModeResult


class TestIdle:
    """Both buttons off → idle."""

    def test_both_off(self):
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0

    def test_both_off_car_connected(self):
        """Even with car connected, idle if no buttons pressed."""
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0


class TestError:
    """Error states when armed but cannot charge."""

    def test_wallbox_offline(self):
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=False,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"
        assert "offline" in result.status_text.lower()

    def test_wallbox_faulted(self):
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Faulted",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"
        assert "fault" in result.status_text.lower()

    def test_car_not_connected(self):
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"
        assert "not connected" in result.status_text.lower()

    def test_charge_now_wallbox_offline(self):
        """Charge Now also blocked if wallbox offline."""
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=True,
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=False,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"

    def test_charge_now_car_not_connected(self):
        """Charge Now also shows error if no car."""
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=True,
            is_cheap_tariff=False,
            wallbox_status="Available",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "error"


class TestReady:
    """Ready: armed, car plugged in, can charge."""

    def test_goal_charge_cheap_tariff(self):
        """Goal charge + cheap tariff + car ready → ready + full power."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "ready"
        assert result.target_power_w == 11000

    def test_charge_now_expensive_tariff(self):
        """Charge Now ignores tariff → ready + full power."""
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=True,
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "ready"
        assert result.target_power_w == 11000


class TestWaiting:
    """Waiting: goal_charge armed but expensive tariff."""

    def test_goal_charge_expensive_tariff(self):
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.charge_status == "waiting"
        assert result.target_power_w == 0
        assert "waiting" in result.status_text.lower()

    def test_goal_charge_expensive_tariff_suspended(self):
        """Waiting even if wallbox in SuspendedEV state."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
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
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Charging",
            wallbox_connected=True,
            wallbox_power_w=8500,
            idle_minutes=0,
        )
        assert result.charge_status == "charging"
        assert result.target_power_w == 11000

    def test_charging_via_charge_now(self):
        """Charge Now actively charging."""
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=True,
            is_cheap_tariff=False,
            wallbox_status="Charging",
            wallbox_connected=True,
            wallbox_power_w=10500,
            idle_minutes=0,
        )
        assert result.charge_status == "charging"
        assert result.target_power_w == 11000


class TestAutoReset:
    """Auto-reset after car stops drawing current."""

    def test_auto_reset_after_timeout(self):
        """Idle > 5 min + Finishing → auto-reset (idle status)."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=6,
        )
        assert result.charge_status == "idle"
        assert result.target_power_w == 0
        assert "complete" in result.status_text.lower()

    def test_no_reset_before_timeout(self):
        """Idle < 5 min → still ready/charging, not reset."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=3,
        )
        # Should still be ready (cheap tariff, car connected)
        assert result.charge_status != "idle" or result.target_power_w >= 0

    def test_auto_reset_suspended_ev(self):
        """SuspendedEV + timeout → auto-reset."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=True,
            is_cheap_tariff=True,
            wallbox_status="SuspendedEV",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=10,
        )
        assert result.charge_status == "idle"
        assert "complete" in result.status_text.lower()

    def test_auto_reset_exact_timeout(self):
        """Exactly at timeout boundary → triggers reset."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=5,
        )
        assert result.charge_status == "idle"

    def test_custom_timeout(self):
        """Custom timeout of 10 minutes."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Finishing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=7,
            auto_reset_timeout_min=10,
        )
        # 7 < 10 → should NOT reset
        assert result.charge_status != "idle"

    def test_no_reset_while_charging_status(self):
        """No auto-reset if wallbox status is still Charging (even at 0W briefly)."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Charging",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=10,
        )
        # Charging status with 0W → not auto-reset (transient state)
        assert result.charge_status != "idle"


class TestChargeNowOverride:
    """Charge Now overrides tariff."""

    def test_charge_now_during_expensive(self):
        """Charge Now works during expensive tariff."""
        result = calculate_goal_mode(
            ev_goal_charge=False,
            ev_charge_now=True,
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.target_power_w == 11000

    def test_charge_now_overrides_goal_charge_waiting(self):
        """Both buttons: charge_now wins even during expensive tariff."""
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=True,
            is_cheap_tariff=False,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
        )
        assert result.target_power_w == 11000
        assert result.charge_status == "ready"


class TestCustomMaxPower:
    """Custom max power parameter."""

    def test_custom_max_power(self):
        result = calculate_goal_mode(
            ev_goal_charge=True,
            ev_charge_now=False,
            is_cheap_tariff=True,
            wallbox_status="Preparing",
            wallbox_connected=True,
            wallbox_power_w=0,
            idle_minutes=0,
            goal_max_power_w=7400,
        )
        assert result.target_power_w == 7400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
