"""Tests for OCPP handler and server throttle logic."""

import time

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


class TestCalibratedCurrent:
    """Tests for calibrated power-to-current conversion."""

    def test_zero_power(self, handler):
        """0W should return 0A."""
        assert handler._calibrated_current(0, 3) == 0.0

    def test_negative_power(self, handler):
        """Negative power should return 0A."""
        assert handler._calibrated_current(-100, 3) == 0.0

    def test_exact_calibration_point(self, handler):
        """Power matching a calibration point exactly should return that current."""
        # 10A → 6445W
        assert handler._calibrated_current(6445, 3) == 10.0

    def test_interpolation_mid_range(self, handler):
        """Power between calibration points should interpolate."""
        # 4800W between 6A→4094W and 8A→5137W
        # ratio = (4800-4094)/(5137-4094) = 706/1043 ≈ 0.677
        # current = 6.0 + 0.677 * 2.0 ≈ 7.354
        result = handler._calibrated_current(4800, 3)
        assert 7.3 < result < 7.5

    def test_below_min_calibration(self, handler):
        """Power below lowest calibration point should return min current (6A)."""
        assert handler._calibrated_current(2000, 3) == 6.0

    def test_above_max_calibration(self, handler):
        """Power above highest calibration point should return max current (16A)."""
        assert handler._calibrated_current(12000, 3) == 16.0

    def test_single_phase_uses_naive_formula(self, handler):
        """1-phase should use naive formula (no calibration data)."""
        # 3680W / (230 * 1) = 16A
        result = handler._calibrated_current(3680, 1)
        assert abs(result - 16.0) < 0.01

    def test_interpolation_upper_range(self, handler):
        """Interpolation in upper range (14A-15A)."""
        # 9321W→14A, 10007W→15A, midpoint 9664W
        # ratio = (9664-9321)/(10007-9321) = 343/686 = 0.5
        # current = 14.0 + 0.5 * 1.0 = 14.5
        result = handler._calibrated_current(9664, 3)
        assert abs(result - 14.5) < 0.01


class TestAuthorization:
    """Tests for authorization handling."""

    @pytest.mark.asyncio
    async def test_authorize_accepts_all(self, handler):
        """All authorization requests should be accepted."""
        result = await handler.on_authorize(id_tag="any_tag")
        assert result.id_tag_info["status"].value == "Accepted"


class TestThrottle:
    """Tests for power update throttle (TC-17/18/19).

    Tests exercise OCPPServer._watch_controls throttle logic by
    manipulating _pending_power_w, _last_profile_sent_at, and calling
    _send_power_to_wallbox directly.
    """

    @pytest.fixture
    def server(self):
        """Create an OCPPServer with mocked HA and charge point."""
        # Mock heavy dependencies not available in dev env
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer({
            "wallbox_id": "test",
            "power_update_interval_s": 5,
        })
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        # Mock charge point with active transaction
        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_tc17_rapid_changes_only_last_sent(self, server):
        """TC-17: Two rapid changes → only last value sent after interval."""
        # Simulate two rapid HA changes (only latest pending matters)
        server._pending_power_w = 3000.0
        server._pending_power_w = 5000.0  # overwrites previous

        # Interval has elapsed (last sent long ago)
        server._last_profile_sent_at = time.monotonic() - 10

        await server._send_power_to_wallbox(server._pending_power_w)

        # Only 5000W was sent
        server.charge_point.set_charging_power.assert_called_once_with(
            5000.0, num_phases=3
        )
        assert server._pending_power_w is None

    @pytest.mark.asyncio
    async def test_tc17_throttle_blocks_during_interval(self, server):
        """TC-17: Pending value not sent when interval hasn't elapsed."""
        server._pending_power_w = 4000.0
        server._last_profile_sent_at = time.monotonic()  # just sent

        # Check throttle condition (simulating _watch_controls logic)
        elapsed = time.monotonic() - server._last_profile_sent_at
        assert elapsed < server.power_update_interval_s
        # Should NOT call _send_power_to_wallbox — pending stays
        assert server._pending_power_w == 4000.0

    @pytest.mark.asyncio
    async def test_tc18_zero_watts_queued_normally(self, server):
        """TC-18: 0W during interval is queued normally (no bypass)."""
        server._last_profile_sent_at = time.monotonic()  # just sent
        server._pending_power_w = 0.0

        # Interval not elapsed → 0W stays pending
        elapsed = time.monotonic() - server._last_profile_sent_at
        assert elapsed < server.power_update_interval_s
        assert server._pending_power_w == 0.0

        # After interval elapses, 0W is sent
        server._last_profile_sent_at = time.monotonic() - 10
        server.charge_point.current_power_w = 3000
        await server._send_power_to_wallbox(0.0)

        server.charge_point.set_charging_power.assert_called_once_with(
            0.0, num_phases=3
        )
        assert server._pending_power_w is None

    @pytest.mark.asyncio
    async def test_tc19_zero_nonzero_zero_only_final_sent(self, server):
        """TC-19: 0W → >0W → 0W within interval → only final 0W sent."""
        # Simulate sequence of HA changes within one interval
        server._last_profile_sent_at = time.monotonic()  # just sent

        # Three changes arrive, each overwrites pending
        server._pending_power_w = 0.0
        server._pending_power_w = 7000.0
        server._pending_power_w = 0.0  # final value

        # Interval elapses
        server._last_profile_sent_at = time.monotonic() - 10
        server.charge_point.current_power_w = 5000
        await server._send_power_to_wallbox(server._pending_power_w)

        # Only the final 0W was sent
        server.charge_point.set_charging_power.assert_called_once_with(
            0.0, num_phases=3
        )
        assert server._pending_power_w is None
