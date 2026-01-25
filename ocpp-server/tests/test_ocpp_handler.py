"""Tests for OCPP handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock

# Add src to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ocpp_handler import ChargePointHandler


@pytest.fixture
def mock_connection():
    """Create a mock WebSocket connection."""
    conn = AsyncMock()
    conn.send = AsyncMock()
    conn.recv = AsyncMock()
    return conn


@pytest.fixture
def handler(mock_connection):
    """Create a ChargePointHandler instance."""
    return ChargePointHandler("test_wallbox", mock_connection)


class TestBootNotification:
    """Tests for BootNotification handling."""

    @pytest.mark.asyncio
    async def test_boot_notification_accepted(self, handler):
        """Wallbox boot notification should be accepted."""
        result = await handler.on_boot_notification(
            charge_point_vendor="TestVendor",
            charge_point_model="TestModel",
        )
        assert result.status.value == "Accepted"
        assert result.interval == 60


class TestStatusNotification:
    """Tests for StatusNotification handling."""

    @pytest.mark.asyncio
    async def test_status_change_callback(self, mock_connection):
        """Status change should trigger callback."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)

        await handler.on_status_notification(
            connector_id=1,
            error_code="NoError",
            status="Charging",
        )

        callback.assert_called_once_with("status", "Charging")
        assert handler.current_status == "Charging"


class TestMeterValues:
    """Tests for MeterValues handling."""

    @pytest.mark.asyncio
    async def test_power_meter_value(self, mock_connection):
        """Power meter value should update current_power_w."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)

        await handler.on_meter_values(
            connector_id=1,
            meter_value=[{
                "sampled_value": [
                    {"measurand": "Power.Active.Import", "value": "7000"}
                ]
            }],
        )

        assert handler.current_power_w == 7000
        callback.assert_called_with("power_w", 7000)

    @pytest.mark.asyncio
    async def test_energy_meter_value(self, mock_connection):
        """Energy meter value should update session_energy_wh."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)

        await handler.on_meter_values(
            connector_id=1,
            meter_value=[{
                "sampled_value": [
                    {"measurand": "Energy.Active.Import.Register", "value": "5000"}
                ]
            }],
        )

        assert handler.session_energy_wh == 5000
        callback.assert_called_with("energy_wh", 5000)


class TestTransactions:
    """Tests for transaction handling."""

    @pytest.mark.asyncio
    async def test_start_transaction(self, handler):
        """Start transaction should return transaction ID."""
        result = await handler.on_start_transaction(
            connector_id=1,
            id_tag="test_tag",
            meter_start=0,
            timestamp="2024-01-01T00:00:00Z",
        )

        assert result.transaction_id == 1
        assert result.id_tag_info["status"].value == "Accepted"
        assert handler.transaction_id == 1

    @pytest.mark.asyncio
    async def test_stop_transaction(self, handler):
        """Stop transaction should clear transaction ID."""
        # First start a transaction
        await handler.on_start_transaction(
            connector_id=1,
            id_tag="test_tag",
            meter_start=0,
            timestamp="2024-01-01T00:00:00Z",
        )

        # Then stop it
        result = await handler.on_stop_transaction(
            meter_stop=5000,
            timestamp="2024-01-01T01:00:00Z",
            transaction_id=1,
        )

        assert result.id_tag_info["status"].value == "Accepted"
        assert handler.transaction_id is None


class TestChargingPower:
    """Tests for charging power calculation."""

    def test_power_to_current_conversion(self):
        """Test power to current conversion."""
        # 7kW 3-phase: 7000 / (230 * 3) = 10.14A
        power_w = 7000
        phases = 3
        voltage = 230
        expected_current = power_w / (voltage * phases)

        assert abs(expected_current - 10.14) < 0.1

    def test_power_to_current_single_phase(self):
        """Test single phase power conversion."""
        # 3.68kW 1-phase: 3680 / 230 = 16A
        power_w = 3680
        phases = 1
        voltage = 230
        expected_current = power_w / (voltage * phases)

        assert abs(expected_current - 16) < 0.1


class TestAuthorization:
    """Tests for authorization handling."""

    @pytest.mark.asyncio
    async def test_authorize_accepts_all(self, handler):
        """All authorization requests should be accepted."""
        result = await handler.on_authorize(id_tag="any_tag")
        assert result.id_tag_info["status"].value == "Accepted"
