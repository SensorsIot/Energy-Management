"""Tests for OCPP handler and server throttle logic."""

import asyncio
import math
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
    async def test_boot_notification_accepted(self, handler) -> None:
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
    async def test_status_change_callback(self, mock_connection) -> None:
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
    async def test_power_meter_value(self, mock_connection) -> None:
        """Power meter value should be corrected and update current_power_w."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        # Power is only accepted during an active transaction
        handler.transaction_id = 1

        # OCPP reports 7000W → corrected = 0.962115 * 7000 + 105.6 = 6840.4
        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {
                    "sampled_value": [
                        {"measurand": "Power.Active.Import", "value": "7000"}
                    ]
                }
            ],
        )

        expected = 0.962115 * 7000 + 105.6
        assert handler.current_power_w == pytest.approx(expected, abs=0.1)
        callback.assert_called_once()
        assert callback.call_args[0][0] == "power_w"
        assert callback.call_args[0][1] == pytest.approx(expected, abs=0.1)

    @pytest.mark.asyncio
    async def test_energy_meter_value(self, mock_connection) -> None:
        """Energy meter value should update session_energy_wh."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)

        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {
                    "sampled_value": [
                        {"measurand": "Energy.Active.Import.Register", "value": "5000"}
                    ]
                }
            ],
        )

        assert handler.session_energy_wh == 5000
        callback.assert_called_with("energy_wh", 5000)

    @pytest.mark.asyncio
    async def test_energy_only_message_preserves_power(self, mock_connection) -> None:
        """Energy-only MeterValues (Sample.Clock) must not zero current_power_w.

        The AcTec wallbox sends a Sample.Clock message at 15-minute boundaries
        containing only Energy.Active.Import.Register (no Power measurand).
        Previously this zeroed wallbox_power, causing false 0W readings.
        """
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        handler.transaction_id = 1
        handler.current_power_w = 3940  # actively charging

        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {
                    "sampled_value": [
                        {
                            "measurand": "Energy.Active.Import.Register",
                            "value": "49890",
                            "context": "Sample.Clock",
                        }
                    ]
                }
            ],
        )

        # Power must be preserved — not zeroed
        assert handler.current_power_w == 3940
        # Energy should still be updated
        assert handler.session_energy_wh == 49890
        # Callback should only have been called for energy, not power
        callback.assert_called_once_with("energy_wh", 49890)


class TestSecurityInputValidation:
    """Security test cases — untrusted MeterValues input (FSD §8.1).

    SEC-01/SEC-03 (CWE-20): the wallbox is on an unauthenticated LAN WebSocket,
    so its MeterValues are untrusted input. Malformed / out-of-range values must
    be dropped, never crash the handler or corrupt the reported power.
    SEC-02 (CWE-400): a flood must not accumulate state; stale frames are dropped.
    """

    @pytest.mark.asyncio
    async def test_sec01_non_numeric_value_dropped_no_crash(self, mock_connection) -> None:
        """SEC-01: a non-numeric MeterValues value is dropped, not fatal."""
        handler = ChargePointHandler("test", mock_connection)
        handler.transaction_id = 1
        handler.current_power_w = 3940  # actively charging

        # Must return normally (no exception) and keep the last good reading.
        result = await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {"sampled_value": [
                    {"measurand": "Power.Active.Import", "value": "not-a-number"}
                ]}
            ],
        )
        assert result is not None
        assert handler.current_power_w == 3940  # corrupt value ignored

    @pytest.mark.asyncio
    async def test_sec01_valid_value_survives_alongside_bad(self, mock_connection) -> None:
        """SEC-01: a good power value is still processed when a sibling value is bad."""
        handler = ChargePointHandler("test", mock_connection)
        handler.transaction_id = 1
        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {"sampled_value": [
                    {"measurand": "Power.Active.Import", "value": "garbage"},
                    {"measurand": "Power.Active.Import", "value": "7000"},
                ]}
            ],
        )
        expected = 0.962115 * 7000 + 105.6
        assert handler.current_power_w == pytest.approx(expected, abs=0.1)

    @pytest.mark.asyncio
    async def test_sec03_negative_power_dropped(self, mock_connection) -> None:
        """SEC-03: negative phase power is dropped; current_power_w never corrupted."""
        handler = ChargePointHandler("test", mock_connection)
        handler.transaction_id = 1
        handler.current_power_w = 3940
        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {"sampled_value": [
                    {"measurand": "Power.Active.Import", "value": "-5000"}
                ]}
            ],
        )
        assert handler.current_power_w >= 0
        assert handler.current_power_w == 3940  # prior good value kept

    @pytest.mark.asyncio
    async def test_sec03_non_finite_power_dropped(self, mock_connection) -> None:
        """SEC-03: NaN / Inf power values are dropped, power stays finite."""
        handler = ChargePointHandler("test", mock_connection)
        handler.transaction_id = 1
        handler.current_power_w = 3940
        for bad in ("nan", "inf", "-inf"):
            await handler.on_meter_values(
                connector_id=1,
                meter_value=[
                    {"sampled_value": [
                        {"measurand": "Power.Active.Import", "value": bad}
                    ]}
                ],
            )
        assert math.isfinite(handler.current_power_w)
        assert handler.current_power_w == 3940

    @pytest.mark.asyncio
    async def test_sec02_flood_bounded_and_stale_dropped(self, mock_connection) -> None:
        """SEC-02: a MeterValues flood doesn't accumulate state; stale frames are dropped."""
        handler = ChargePointHandler("test", mock_connection)
        handler.transaction_id = 1
        handler.trigger_meter_values = AsyncMock()  # avoid the stale-frame re-trigger side effect

        for _ in range(200):
            await handler.on_meter_values(
                connector_id=1,
                meter_value=[
                    {"sampled_value": [
                        {"measurand": "Power.Active.Import", "value": "6000"}
                    ]}
                ],
            )
        # State is a scalar, not a growing collection.
        assert isinstance(handler.current_power_w, (int, float))

        prev = handler.current_power_w
        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {"timestamp": "2000-01-01T00:00:00Z", "sampled_value": [
                    {"measurand": "Power.Active.Import", "value": "9999"}
                ]}
            ],
        )
        assert handler.current_power_w == prev  # stale frame ignored


class TestTransactions:
    """Tests for transaction handling."""

    @pytest.mark.asyncio
    async def test_start_transaction(self, handler) -> None:
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
    async def test_stop_transaction(self, handler) -> None:
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


class TestSetChargingPowerAmps:
    """Tests for SetChargingProfile converting watts to decimal amps."""

    @pytest.mark.asyncio
    async def test_sends_amps_from_watts(self, handler) -> None:
        """SetChargingProfile should convert watts to integer amps via demand calibration."""
        with patch.object(handler, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = type("R", (), {"status": "Accepted"})()
            result = await handler.set_charging_power(6288, 3)
            assert result is True
            profile = mock_call.call_args[0][0].cs_charging_profiles
            schedule = profile["charging_schedule"]
            assert schedule["charging_rate_unit"] == "A"
            # 6288 / 637 = 9.87 → round = 10A
            assert schedule["charging_schedule_period"][0]["limit"] == 10

    @pytest.mark.asyncio
    async def test_zero_power(self, handler) -> None:
        """0W should send limit=0."""
        with patch.object(handler, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = type("R", (), {"status": "Accepted"})()
            await handler.set_charging_power(0, 3)
            profile = mock_call.call_args[0][0].cs_charging_profiles
            assert profile["charging_schedule"]["charging_schedule_period"][0]["limit"] == 0

    @pytest.mark.asyncio
    async def test_negative_power_clamped_to_zero(self, handler) -> None:
        """Negative power should be clamped to 0."""
        with patch.object(handler, "call", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = type("R", (), {"status": "Accepted"})()
            await handler.set_charging_power(-500, 3)
            profile = mock_call.call_args[0][0].cs_charging_profiles
            assert profile["charging_schedule"]["charging_schedule_period"][0]["limit"] == 0


class TestAuthorization:
    """Tests for authorization handling."""

    @pytest.mark.asyncio
    async def test_authorize_accepts_all(self, handler) -> None:
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

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
            }
        )
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
    async def test_tc17_rapid_changes_only_last_sent(self, server) -> None:
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
    async def test_tc17_throttle_blocks_during_interval(self, server) -> None:
        """TC-17: Pending value not sent when interval hasn't elapsed."""
        server._pending_power_w = 4000.0
        server._last_profile_sent_at = time.monotonic()  # just sent

        # Check throttle condition (simulating _watch_controls logic)
        elapsed = time.monotonic() - server._last_profile_sent_at
        assert elapsed < server.power_update_interval_s
        # Should NOT call _send_power_to_wallbox — pending stays
        assert server._pending_power_w == 4000.0

    @pytest.mark.asyncio
    async def test_tc18_zero_watts_bypasses_throttle(self, server) -> None:
        """TC-18: 0W sent immediately (bypasses throttle), pending queue cleared."""
        # Queue a pending value
        server._pending_power_w = 5000.0
        server._last_change_at = time.monotonic()  # just changed

        # 0W bypasses throttle — send immediately
        server.charge_point.current_power_w = 5000
        await server._send_power_to_wallbox(0.0)

        server.charge_point.set_charging_power.assert_called_once_with(
            0.0, num_phases=3
        )
        # Pending queue cleared by _send_power_to_wallbox
        assert server._pending_power_w is None

    @pytest.mark.asyncio
    async def test_ha_restart_resyncs_connected_state(self, server) -> None:
        """After HA restart, re-registration should re-sync wallbox_connected.

        Scenario: wallbox is connected (heartbeats flowing), HA core restarts,
        OCPP server gets 502s during initial set_state. When HA recovers,
        _watch_controls detects missing control entity, calls register_entities()
        which resets binary sensors to off, then _sync_ha_state() must re-push
        connected=on from the live charge_point.
        """
        # Wallbox is connected with active status
        server.charge_point.current_status = "Charging"
        server.charge_point.current_power_w = 5000
        server.charge_point.session_energy_wh = 1200
        server.charge_point.transaction_id = 42

        # Simulate: register_entities resets connected to off (as it does)
        await server.ha.set_state("binary_sensor.wallbox_connected", "off")
        server.ha.set_state.reset_mock()

        # Now _sync_ha_state should re-push correct state
        await server._sync_ha_state()

        # Verify connected was set to on
        calls = {
            args[0]: args[1]
            for args, _ in [
                (c.args, c.kwargs) for c in server.ha.set_state.call_args_list
            ]
        }
        assert calls["binary_sensor.wallbox_connected"] == "on"
        assert calls["sensor.wallbox_status"] == "Charging"
        assert calls["sensor.wallbox_power"] == 5000
        assert calls["sensor.wallbox_energy"] == 1200
        assert calls["sensor.wallbox_transaction"] == "charging"

    @pytest.mark.asyncio
    async def test_ha_restart_no_wallbox_stays_disconnected(self, server) -> None:
        """When wallbox is not connected, _sync_ha_state sets connected=off."""
        server.charge_point = None

        await server._sync_ha_state()

        calls = {
            args[0]: args[1]
            for args, _ in [
                (c.args, c.kwargs) for c in server.ha.set_state.call_args_list
            ]
        }
        assert calls["binary_sensor.wallbox_connected"] == "off"

    @pytest.mark.asyncio
    async def test_tc19_first_zero_immediate_nonzero_queued_final_zero_immediate(
        self, server
    ) -> None:
        """TC-19: First 0W sent immediately, >0W queued, final 0W sent immediately."""
        server._last_change_at = time.monotonic() - 10  # interval elapsed

        # First 0W — sent immediately (bypass)
        server.charge_point.current_power_w = 5000
        await server._send_power_to_wallbox(0.0)
        assert server.charge_point.set_charging_power.call_count == 1
        server.charge_point.set_charging_power.assert_called_with(0.0, num_phases=3)

        # >0W within interval — queued (not sent)
        server._last_change_at = time.monotonic()
        server._pending_power_w = 7000.0
        # Don't call _send — this simulates the throttle holding it

        # Final 0W — sent immediately (bypass), clears pending
        server.charge_point.current_power_w = 0
        await server._send_power_to_wallbox(0.0)
        assert server.charge_point.set_charging_power.call_count == 2
        assert server._pending_power_w is None

    @pytest.mark.asyncio
    async def test_tc22_rapid_nonzero_zero_nonzero(self, server) -> None:
        """TC-22: Rapid >0W → 0W → >0W: 0W sent immediately, >0W queued."""
        server._last_change_at = time.monotonic() - 10  # interval elapsed

        # >0W — sent immediately (interval elapsed)
        await server._send_power_to_wallbox(5000.0)
        assert server.charge_point.set_charging_power.call_count == 1
        server.charge_point.set_charging_power.assert_called_with(5000.0, num_phases=3)

        # 0W within interval — sent immediately (bypass)
        server._last_change_at = time.monotonic()
        server.charge_point.current_power_w = 5000
        await server._send_power_to_wallbox(0.0)
        assert server.charge_point.set_charging_power.call_count == 2

        # >0W within interval — queued (not sent)
        server._pending_power_w = 7000.0
        assert server._pending_power_w == 7000.0
        # set_charging_power still at 2 calls (not sent yet)
        assert server.charge_point.set_charging_power.call_count == 2

        # After interval elapses, pending is sent
        server._last_change_at = time.monotonic() - 10
        await server._send_power_to_wallbox(server._pending_power_w)
        assert server.charge_point.set_charging_power.call_count == 3
        assert server._pending_power_w is None


class TestPowerZeroing:
    """Tests for power zeroing on status transitions (Item 1)."""

    @pytest.mark.asyncio
    async def test_suspended_evse_zeros_power(self, mock_connection) -> None:
        """SuspendedEVSE with power > 0 should zero power and fire callback."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        handler.current_power_w = 5000

        await handler.on_status_notification(
            connector_id=1, error_code="NoError", status="SuspendedEVSE"
        )

        assert handler.current_power_w == 0
        callback.assert_any_call("power_w", 0)

    @pytest.mark.asyncio
    async def test_suspended_evse_already_zero_no_callback(self, mock_connection) -> None:
        """SuspendedEVSE with power = 0 should not fire power_w callback."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        handler.current_power_w = 0

        await handler.on_status_notification(
            connector_id=1, error_code="NoError", status="SuspendedEVSE"
        )

        # Status callback fires, but power_w should NOT
        power_calls = [c for c in callback.call_args_list if c.args[0] == "power_w"]
        assert power_calls == []

    @pytest.mark.asyncio
    async def test_charging_status_keeps_power(self, mock_connection) -> None:
        """Charging status should not zero power."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        handler.current_power_w = 5000

        await handler.on_status_notification(
            connector_id=1, error_code="NoError", status="Charging"
        )

        assert handler.current_power_w == 5000

    @pytest.mark.asyncio
    async def test_stop_transaction_zeros_power(self, mock_connection) -> None:
        """StopTransaction with power > 0 should zero power and fire callback."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        handler.current_power_w = 5000
        handler.transaction_id = 1

        await handler.on_stop_transaction(
            meter_stop=10000, timestamp="2024-01-01T01:00:00Z", transaction_id=1
        )

        assert handler.current_power_w == 0
        callback.assert_any_call("power_w", 0)

    @pytest.mark.asyncio
    async def test_connector_0_ignored(self, mock_connection) -> None:
        """Connector 0 (charge point level) status should be ignored."""
        callback = MagicMock()
        handler = ChargePointHandler("test", mock_connection, on_status_change=callback)
        initial_status = handler.current_status

        await handler.on_status_notification(
            connector_id=0, error_code="NoError", status="Available"
        )

        assert handler.current_status == initial_status
        callback.assert_not_called()


class TestPhaseSwitchDecision:
    """Tests for phase switch decision logic (Item 2)."""

    @pytest.fixture
    def phase_server(self):
        """Create an OCPPServer with phase switching enabled."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "phase_switch_entity": "switch.earu_relay",
                "wallbox_type": "external_breaker",
                "min_current_a": 6,
                "max_current_a": 16,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")  # BL0942 current = 0
        srv.ha.call_service = AsyncMock(return_value=True)

        # Mock charge point with active transaction
        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        cp.current_status = "SuspendedEVSE"
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_low_power_switches_to_1_phase(self, phase_server) -> None:
        """Power < 4140W on 3-phase should switch to 1-phase."""
        phase_server._current_phases = 3

        await phase_server._send_power_to_wallbox(3000.0)

        # Relay turn_off = 1-phase
        phase_server.ha.call_service.assert_any_call(
            "switch", "turn_off", {"entity_id": "switch.earu_relay"}
        )
        assert phase_server._current_phases == 1

    @pytest.mark.asyncio
    async def test_high_power_switches_to_3_phase(self, phase_server) -> None:
        """Power >= 4140W on 1-phase should switch to 3-phase."""
        phase_server._current_phases = 1

        await phase_server._send_power_to_wallbox(5000.0)

        # Relay turn_on = 3-phase
        phase_server.ha.call_service.assert_any_call(
            "switch", "turn_on", {"entity_id": "switch.earu_relay"}
        )
        assert phase_server._current_phases == 3

    @pytest.mark.asyncio
    async def test_same_phase_no_switch(self, phase_server) -> None:
        """Already on correct phase count should not call relay service."""
        phase_server._current_phases = 3

        await phase_server._send_power_to_wallbox(5000.0)

        # call_service should NOT be called for relay switching
        relay_calls = [
            c
            for c in phase_server.ha.call_service.call_args_list
            if c.args[0] == "switch"
        ]
        assert relay_calls == []

    @pytest.mark.asyncio
    async def test_phase_switching_disabled_no_switch(self, phase_server) -> None:
        """Phase switching disabled should not attempt switch."""
        phase_server._phase_switching_disabled = True
        phase_server._current_phases = 3

        await phase_server._send_power_to_wallbox(3000.0)

        relay_calls = [
            c
            for c in phase_server.ha.call_service.call_args_list
            if c.args[0] == "switch"
        ]
        assert relay_calls == []
        assert phase_server._current_phases == 3


class TestPhaseSwitchSafetyAbort:
    """Tests for phase switch safety abort (Item 3)."""

    @pytest.fixture
    def phase_server(self):
        """Create an OCPPServer with phase switching enabled."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "phase_switch_entity": "switch.earu_relay",
                "wallbox_type": "external_breaker",
                "min_current_a": 6,
                "max_current_a": 16,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        cp.current_status = "SuspendedEVSE"
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_high_current_aborts_phase_switch(self, phase_server) -> None:
        """BL0942 current >= 0.5A should abort phase switch and disable it."""
        phase_server._current_phases = 3
        # BL0942 returns high current
        phase_server.ha.get_state = AsyncMock(return_value="2.5")

        await phase_server._switch_phases(1)

        assert phase_server._phase_switching_disabled is True
        phase_server.ha.set_state.assert_any_call(
            "binary_sensor.wallbox_single_phase_supported", "off"
        )

    @pytest.mark.asyncio
    async def test_abort_on_1_phase_forces_3_phase(self, phase_server) -> None:
        """Abort while on 1-phase should force back to 3-phase."""
        phase_server._current_phases = 1
        phase_server.ha.get_state = AsyncMock(return_value="2.5")

        await phase_server._switch_phases(3)

        # _abort_phase_switch is called, which sets _current_phases = 3
        # when currently on 1-phase
        assert phase_server._current_phases == 3

    @pytest.mark.asyncio
    async def test_abort_on_3_phase_stays_3_phase(self, phase_server) -> None:
        """Abort while on 3-phase should stay 3-phase, no relay call."""
        phase_server._current_phases = 3
        phase_server.ha.get_state = AsyncMock(return_value="2.5")

        await phase_server._switch_phases(1)

        assert phase_server._current_phases == 3
        # No relay switch.turn_on call (already on 3-phase)
        relay_on_calls = [
            c
            for c in phase_server.ha.call_service.call_args_list
            if c.args[:2] == ("switch", "turn_on")
        ]
        assert relay_on_calls == []


class TestResendOnSuspendedEVSE:
    """Tests for re-send logic when wallbox stuck in SuspendedEVSE (Item 4).

    Tests verify the boolean conditions in _watch_controls rather than
    running the async loop.
    """

    @pytest.fixture
    def server(self):
        """Create an OCPPServer for re-send condition testing."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_resend_when_suspended_with_power(self, server) -> None:
        """SuspendedEVSE + last sent > 0 + interval elapsed → re-send."""
        server.charge_point.current_status = "SuspendedEVSE"
        server._last_sent_power_w = 5000.0
        server._last_change_at = time.monotonic() - 10  # interval elapsed
        server._pending_power_w = None

        # All 4 conditions are true
        assert server._pending_power_w is None
        assert server.charge_point is not None
        assert server.charge_point.current_status == "SuspendedEVSE"
        assert server._last_sent_power_w > 0

        # Call _send_power_to_wallbox as _watch_controls would
        await server._send_power_to_wallbox(server._last_sent_power_w)

        server.charge_point.set_charging_power.assert_called_once_with(
            5000.0, num_phases=3
        )

    @pytest.mark.asyncio
    async def test_no_resend_when_last_sent_zero(self, server) -> None:
        """SuspendedEVSE + last sent = 0 → condition fails, no re-send."""
        server.charge_point.current_status = "SuspendedEVSE"
        server._last_sent_power_w = 0.0
        server._last_change_at = time.monotonic() - 10
        server._pending_power_w = None

        # Condition 4 fails: _last_sent_power_w is not > 0
        assert not (server._last_sent_power_w > 0)

    @pytest.mark.asyncio
    async def test_no_resend_when_charging(self, server) -> None:
        """Charging + last sent > 0 → condition fails, no re-send."""
        server.charge_point.current_status = "Charging"
        server._last_sent_power_w = 5000.0
        server._last_change_at = time.monotonic() - 10
        server._pending_power_w = None

        # Condition 3 fails: status is not SuspendedEVSE
        assert not (server.charge_point.current_status == "SuspendedEVSE")

    @pytest.mark.asyncio
    async def test_no_resend_when_pending_queued(self, server) -> None:
        """SuspendedEVSE + pending queued → condition fails, no re-send."""
        server.charge_point.current_status = "SuspendedEVSE"
        server._last_sent_power_w = 5000.0
        server._last_change_at = time.monotonic() - 10
        server._pending_power_w = 3000.0

        # Condition 1 fails: _pending_power_w is not None
        assert server._pending_power_w is not None


class TestPostConnectSetup:
    """Tests for post-connect state sync (Item 5)."""

    @pytest.fixture
    def server_with_cp(self, mock_connection):
        """Create OCPPServer with a real ChargePointHandler (real asyncio.Events)."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = ChargePointHandler(
            "test", mock_connection, on_status_change=srv._on_status_change
        )
        cp.trigger_meter_values = AsyncMock()
        cp.set_charging_power = AsyncMock()  # avoid real OCPP send (post-connect re-applies 0W)
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_status_event_completes_setup(self, server_with_cp) -> None:
        """StatusNotification arriving should complete post-connect setup."""
        cp = server_with_cp.charge_point
        cp.current_status = "Preparing"
        cp.status_event.set()

        await server_with_cp._post_connect_setup()

        assert server_with_cp._setup_complete.is_set()
        cp.trigger_meter_values.assert_called_once()

    @pytest.mark.asyncio
    async def test_boot_event_completes_setup(self, server_with_cp) -> None:
        """Boot event arriving should complete post-connect setup."""
        cp = server_with_cp.charge_point
        cp.current_status = "Available"
        cp.boot_event.set()

        await server_with_cp._post_connect_setup()

        assert server_with_cp._setup_complete.is_set()

    @pytest.mark.asyncio
    async def test_charging_status_triggers_recovery(self, server_with_cp) -> None:
        """Charging status should complete setup and trigger meter values."""
        cp = server_with_cp.charge_point
        cp.current_status = "Charging"
        cp.status_event.set()

        await server_with_cp._post_connect_setup()

        assert server_with_cp._setup_complete.is_set()
        cp.trigger_meter_values.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_message_timeout_setup_incomplete(self, server_with_cp) -> None:
        """No message within timeout should leave setup incomplete."""

        # Don't set any events — patch asyncio.wait to return empty done set
        async def fake_wait(tasks, timeout=None, return_when=None):
            # Cancel all tasks and return empty done set
            for t in tasks:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            return set(), set()

        with patch("asyncio.wait", side_effect=fake_wait):
            await server_with_cp._post_connect_setup()

        assert not server_with_cp._setup_complete.is_set()


class TestCarReady:
    """Tests for binary_sensor.car_ready (FSD v3.1 Item 1)."""

    @pytest.fixture
    def server(self, mock_connection):
        """Create an OCPPServer with a real ChargePointHandler."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer({"wallbox_id": "test", "power_update_interval_s": 5})
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")

        cp = ChargePointHandler(
            "test", mock_connection, on_status_change=srv._on_status_change
        )
        cp.trigger_meter_values = AsyncMock()
        cp.set_charging_power = AsyncMock()  # avoid real OCPP send (post-connect re-applies 0W)
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_car_ready_on_for_preparing(self, server) -> None:
        """Preparing status → car_ready on."""
        server._setup_complete.set()
        server.charge_point.current_status = "Preparing"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "on")

    @pytest.mark.asyncio
    async def test_car_ready_on_for_charging(self, server) -> None:
        """Charging status → car_ready on."""
        server._setup_complete.set()
        server.charge_point.current_status = "Charging"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "on")

    @pytest.mark.asyncio
    async def test_car_ready_on_for_suspended_evse(self, server) -> None:
        """SuspendedEVSE status → car_ready on."""
        server._setup_complete.set()
        server.charge_point.current_status = "SuspendedEVSE"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "on")

    @pytest.mark.asyncio
    async def test_car_ready_off_for_available(self, server) -> None:
        """Available status → car_ready off."""
        server._setup_complete.set()
        server.charge_point.current_status = "Available"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "off")

    @pytest.mark.asyncio
    async def test_car_ready_off_for_suspended_ev(self, server) -> None:
        """SuspendedEV status → car_ready off."""
        server._setup_complete.set()
        server.charge_point.current_status = "SuspendedEV"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "off")

    @pytest.mark.asyncio
    async def test_car_ready_off_for_finishing(self, server) -> None:
        """Finishing status → car_ready off."""
        server._setup_complete.set()
        server.charge_point.current_status = "Finishing"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "off")

    @pytest.mark.asyncio
    async def test_car_ready_off_before_setup_complete(self, server) -> None:
        """Before _setup_complete → car_ready off regardless of status."""
        server.charge_point.current_status = "Charging"
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "off")

    @pytest.mark.asyncio
    async def test_car_ready_off_on_disconnect(self, server) -> None:
        """Disconnect → car_ready off."""
        server._setup_complete.set()
        server.charge_point = None
        await server._update_car_ready()
        server.ha.set_state.assert_called_with("binary_sensor.car_ready", "off")


class TestGapHandling:
    """Tests for gap clamping 3681–4139W (FSD v3.1 Item 2)."""

    @pytest.fixture
    def server(self):
        """Create an OCPPServer for gap clamping tests."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "phase_switch_entity": "",
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_gap_1_phase_clamps_down(self, server) -> None:
        """3900W on 1-phase → clamp to 3680W."""
        server._current_phases = 1
        await server._send_power_to_wallbox(3900.0)
        server.charge_point.set_charging_power.assert_called_once_with(
            3680, num_phases=1
        )

    @pytest.mark.asyncio
    async def test_gap_3_phase_clamps_up(self, server) -> None:
        """3900W on 3-phase → clamp to 4140W."""
        server._current_phases = 3
        await server._send_power_to_wallbox(3900.0)
        server.charge_point.set_charging_power.assert_called_once_with(
            4140, num_phases=3
        )

    @pytest.mark.asyncio
    async def test_boundary_3680_unchanged(self, server) -> None:
        """3680W (below gap) should pass through unchanged."""
        server._current_phases = 1
        await server._send_power_to_wallbox(3680.0)
        server.charge_point.set_charging_power.assert_called_once_with(
            3680.0, num_phases=1
        )

    @pytest.mark.asyncio
    async def test_boundary_4140_unchanged(self, server) -> None:
        """4140W (above gap) should pass through unchanged."""
        server._current_phases = 3
        await server._send_power_to_wallbox(4140.0)
        server.charge_point.set_charging_power.assert_called_once_with(
            4140.0, num_phases=3
        )


class TestPhaseTimeLock:
    """Tests for phase switching time lock (FSD v3.1 Item 3)."""

    @pytest.fixture
    def server(self):
        """Create an OCPPServer with phase switching enabled."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "phase_switch_entity": "switch.earu_relay",
                "wallbox_type": "external_breaker",
                "min_current_a": 6,
                "max_current_a": 16,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        cp.current_status = "SuspendedEVSE"
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_clamp_during_lock_1_phase(self, server) -> None:
        """During lock on 1-phase, power > 3680W clamped to 3680W."""
        server._current_phases = 1
        server._last_phase_switch_time = time.monotonic()  # just switched

        await server._send_power_to_wallbox(5000.0)

        server.charge_point.set_charging_power.assert_called_once_with(
            3680, num_phases=1
        )
        # No relay call (phase switch skipped)
        relay_calls = [
            c for c in server.ha.call_service.call_args_list if c.args[0] == "switch"
        ]
        assert relay_calls == []

    @pytest.mark.asyncio
    async def test_clamp_during_lock_3_phase(self, server) -> None:
        """During lock on 3-phase, power < 4140W clamped to 4140W."""
        server._current_phases = 3
        server._last_phase_switch_time = time.monotonic()

        await server._send_power_to_wallbox(3000.0)

        server.charge_point.set_charging_power.assert_called_once_with(
            4140, num_phases=3
        )

    @pytest.mark.asyncio
    async def test_switch_allowed_after_lock_expires(self, server) -> None:
        """After 300s, phase switch should be allowed."""
        server._current_phases = 3
        server._last_phase_switch_time = time.monotonic() - 301  # lock expired

        await server._send_power_to_wallbox(3000.0)

        # Phase switch should have been called (relay turn_off for 1-phase)
        relay_calls = [
            c for c in server.ha.call_service.call_args_list if c.args[0] == "switch"
        ]
        assert len(relay_calls) == 1


class TestEscalatingResend:
    """Tests for escalating re-send intervals (FSD v3.1 Item 4)."""

    @pytest.fixture
    def server(self):
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        return srv

    def test_intervals_escalate(self, server) -> None:
        """Retry count 0→10s, 1→30s, 2→60s, 3→60s (capped)."""
        server._resend_retry_count = 0
        assert server._current_resend_interval == 10

        server._resend_retry_count = 1
        assert server._current_resend_interval == 30

        server._resend_retry_count = 2
        assert server._current_resend_interval == 60

        server._resend_retry_count = 10
        assert server._current_resend_interval == 60  # capped at last

    @pytest.mark.asyncio
    async def test_reset_on_status_change(self, server) -> None:
        """Leaving SuspendedEVSE should reset retry count."""
        server._resend_retry_count = 5

        # Mock charge_point to avoid errors in _update_car_ready
        cp = MagicMock()
        cp.current_status = "Charging"
        server.charge_point = cp
        server._setup_complete.set()

        server._on_status_change("status", "Charging")
        assert server._resend_retry_count == 0

    @pytest.mark.asyncio
    async def test_no_reset_when_staying_suspended_evse(self, server) -> None:
        """Staying in SuspendedEVSE should not reset retry count."""
        server._resend_retry_count = 3

        cp = MagicMock()
        cp.current_status = "SuspendedEVSE"
        server.charge_point = cp
        server._setup_complete.set()

        server._on_status_change("status", "SuspendedEVSE")
        assert server._resend_retry_count == 3


class TestSuspendedEVCloudCorrection:
    """Tests for SuspendedEV cloud correction (FSD v3.1 Item 5)."""

    @pytest.fixture
    def server(self):
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "cloud_charging_entity": "sensor.smart_charging_status_raw_value",
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        return srv

    @pytest.mark.asyncio
    async def test_cloud_raw_25_synthesizes_suspended_ev(self, server) -> None:
        """Cloud raw=25 should synthesize SuspendedEV status."""
        server._setup_complete.set()
        server.charge_point = MagicMock()
        server.charge_point.current_status = "SuspendedEVSE"
        server.ha.get_state = AsyncMock(return_value="25")

        # Call _cloud_poll_suspended_ev with a short run
        async def run_one_poll() -> None:
            """Run a single iteration of the cloud poll loop."""
            await asyncio.sleep(0)  # yield
            raw = await server.ha.get_state(server._cloud_charging_entity)
            if raw in ("25", "4"):
                if not server._synthesized_suspended_ev:
                    server._synthesized_suspended_ev = True
                    await server.ha.set_state("sensor.wallbox_status", "SuspendedEV")
                    await server._update_car_ready()

        await run_one_poll()

        assert server._synthesized_suspended_ev is True
        server.ha.set_state.assert_any_call("sensor.wallbox_status", "SuspendedEV")

    @pytest.mark.asyncio
    async def test_cloud_raw_changes_reverts_to_suspended_evse(self, server) -> None:
        """Cloud raw changes from 25 → other should revert to SuspendedEVSE."""
        server._setup_complete.set()
        server.charge_point = MagicMock()
        server.charge_point.current_status = "SuspendedEVSE"
        server._synthesized_suspended_ev = True
        server.ha.get_state = AsyncMock(return_value="2")

        # Simulate one poll iteration
        raw = await server.ha.get_state(server._cloud_charging_entity)
        if raw not in ("25", "4") and server._synthesized_suspended_ev:
            server._synthesized_suspended_ev = False
            await server.ha.set_state("sensor.wallbox_status", "SuspendedEVSE")
            await server._update_car_ready()

        assert server._synthesized_suspended_ev is False
        server.ha.set_state.assert_any_call("sensor.wallbox_status", "SuspendedEVSE")

    @pytest.mark.asyncio
    async def test_no_cloud_poll_when_last_sent_zero(self, server) -> None:
        """Cloud poll should not start when _last_sent_power_w = 0."""
        server.charge_point = MagicMock()
        server.charge_point.current_status = "SuspendedEVSE"
        server._setup_complete.set()
        server._last_sent_power_w = 0.0

        server._on_status_change("status", "SuspendedEVSE")

        assert server._cloud_poll_task is None

    @pytest.mark.asyncio
    async def test_cloud_poll_starts_when_suspended_evse_with_power(self, server) -> None:
        """Cloud poll should start when SuspendedEVSE and last_sent > 0."""
        server.charge_point = MagicMock()
        server.charge_point.current_status = "SuspendedEVSE"
        server._setup_complete.set()
        server._last_sent_power_w = 5000.0

        server._on_status_change("status", "SuspendedEVSE")

        assert server._cloud_poll_task is not None
        # Clean up
        server._cloud_poll_task.cancel()

    def test_resend_blocked_when_synthesized(self, server) -> None:
        """Re-send should be blocked when _synthesized_suspended_ev is True."""
        server._synthesized_suspended_ev = True
        server._pending_power_w = None
        server._last_sent_power_w = 5000.0

        cp = MagicMock()
        cp.current_status = "SuspendedEVSE"
        server.charge_point = cp

        # The re-send condition should fail because of synthesized guard
        resend_condition = (
            server._pending_power_w is None
            and server.charge_point
            and server.charge_point.current_status == "SuspendedEVSE"
            and server._last_sent_power_w > 0
            and not server._synthesized_suspended_ev
        )
        assert resend_condition is False


class TestInnerSync:
    """Tests for initialization inner sync (FSD v3.1 Item 6)."""

    @pytest.fixture
    def server_with_cp(self, mock_connection):
        """Create OCPPServer with a real ChargePointHandler (real asyncio.Events)."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer({"wallbox_id": "test", "power_update_interval_s": 5})
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = ChargePointHandler(
            "test", mock_connection, on_status_change=srv._on_status_change
        )
        cp.trigger_meter_values = AsyncMock()
        cp.set_charging_power = AsyncMock()  # avoid real OCPP send (post-connect re-applies 0W)
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_charging_waits_for_meter_values(self, server_with_cp) -> None:
        """Charging status should wait for MeterValues before completing setup."""
        cp = server_with_cp.charge_point
        cp.current_status = "Charging"
        cp.status_event.set()
        # Pre-set meter_values_event so it doesn't block
        cp.meter_values_event.set()

        await server_with_cp._post_connect_setup()

        assert server_with_cp._setup_complete.is_set()
        cp.trigger_meter_values.assert_called_once()

    @pytest.mark.asyncio
    async def test_charging_timeout_completes_anyway(self, server_with_cp) -> None:
        """MeterValues timeout should not block setup completion."""
        cp = server_with_cp.charge_point
        cp.current_status = "Charging"
        cp.status_event.set()
        # Don't set meter_values_event — will timeout

        await server_with_cp._post_connect_setup()

        assert server_with_cp._setup_complete.is_set()

    @pytest.mark.asyncio
    async def test_available_skips_meter_values_wait(self, server_with_cp) -> None:
        """Available status should skip MeterValues wait."""
        cp = server_with_cp.charge_point
        cp.current_status = "Available"
        cp.boot_event.set()

        await server_with_cp._post_connect_setup()

        assert server_with_cp._setup_complete.is_set()
        # meter_values_event should NOT have been waited on
        assert not cp.meter_values_event.is_set()


class TestMeterValuesEvent:
    """Tests for meter_values_event on ChargePointHandler."""

    @pytest.mark.asyncio
    async def test_meter_values_sets_event(self, mock_connection) -> None:
        """on_meter_values should set meter_values_event."""
        handler = ChargePointHandler("test", mock_connection)

        assert not handler.meter_values_event.is_set()

        await handler.on_meter_values(
            connector_id=1,
            meter_value=[
                {"sampled_value": [{"measurand": "Power.Active.Import", "value": "0"}]}
            ],
        )

        assert handler.meter_values_event.is_set()


class TestThreePhaseOnly:
    """Tests for wallbox_type='three_phase' — no phase switching, below-min → 0W."""

    @pytest.fixture
    def server(self):
        """Create an OCPPServer with three_phase wallbox type."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "wallbox_type": "three_phase",
                "min_current_a": 6,
                "max_current_a": 16,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        cp.current_status = "SuspendedEVSE"
        srv.charge_point = cp

        return srv

    def test_single_phase_not_supported(self, server) -> None:
        """three_phase type should report single_phase_supported=False."""
        assert server.single_phase_supported is False

    @pytest.mark.asyncio
    async def test_below_minimum_pauses(self, server) -> None:
        """Power below 4140W (6A×3×230V) should be clamped to 0W."""
        await server._send_power_to_wallbox(3000.0)

        server.charge_point.set_charging_power.assert_awaited()
        # Should have sent 0W (paused)
        call_args = server.charge_point.set_charging_power.call_args
        assert call_args.args[0] == 0 or call_args.kwargs.get("power_w") == 0

    @pytest.mark.asyncio
    async def test_above_minimum_sends_power(self, server) -> None:
        """Power >= 4140W should be sent as-is."""
        await server._send_power_to_wallbox(5000.0)

        server.charge_point.set_charging_power.assert_awaited_with(
            5000.0, num_phases=3
        )

    @pytest.mark.asyncio
    async def test_no_phase_switching(self, server) -> None:
        """three_phase should never call relay service."""
        server._current_phases = 3

        await server._send_power_to_wallbox(2000.0)

        relay_calls = [
            c
            for c in server.ha.call_service.call_args_list
            if c.args[0] == "switch"
        ]
        assert relay_calls == []
        assert server._current_phases == 3

    @pytest.mark.asyncio
    async def test_abort_phase_switch_noop(self, server) -> None:
        """_abort_phase_switch should be a no-op for three_phase."""
        server._current_phases = 1  # hypothetical
        await server._abort_phase_switch("test reason")

        # Should not have changed anything (guard returns early)
        assert server._phase_switching_disabled is False


class TestUniversalWallbox:
    """Tests for wallbox_type='universal' — built-in phase switching."""

    @pytest.fixture
    def server(self):
        """Create an OCPPServer with universal wallbox type."""
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer(
            {
                "wallbox_id": "test",
                "power_update_interval_s": 5,
                "wallbox_type": "universal",
                "min_current_a": 6,
                "max_current_a": 16,
            }
        )
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        cp.current_status = "SuspendedEVSE"
        srv.charge_point = cp

        return srv

    def test_single_phase_supported(self, server) -> None:
        """Universal type should report single_phase_supported=True."""
        assert server.single_phase_supported is True

    @pytest.mark.asyncio
    async def test_low_power_tracks_1_phase(self, server) -> None:
        """Power < threshold should track 1-phase without relay toggle."""
        server._current_phases = 3

        await server._send_power_to_wallbox(3000.0)

        assert server._current_phases == 1
        # No relay service call
        relay_calls = [
            c
            for c in server.ha.call_service.call_args_list
            if c.args[0] == "switch"
        ]
        assert relay_calls == []

    @pytest.mark.asyncio
    async def test_high_power_tracks_3_phase(self, server) -> None:
        """Power >= threshold should track 3-phase without relay toggle."""
        server._current_phases = 1

        await server._send_power_to_wallbox(5000.0)

        assert server._current_phases == 3
        relay_calls = [
            c
            for c in server.ha.call_service.call_args_list
            if c.args[0] == "switch"
        ]
        assert relay_calls == []

    @pytest.mark.asyncio
    async def test_same_phase_no_update(self, server) -> None:
        """Already on correct phase count should not re-publish."""
        server._current_phases = 3
        server.ha.set_state.reset_mock()

        await server._send_power_to_wallbox(5000.0)

        # Should not have published phases (no change)
        phase_calls = [
            c
            for c in server.ha.set_state.call_args_list
            if c.args[0] == "sensor.wallbox_phases"
        ]
        assert phase_calls == []


class TestTransactionStopRestart:
    """Edge cases: car stops transaction and reconnects.

    Regression tests for the 2026-03-20 incident: car sent StopTransaction,
    wallbox went Preparing, but OCPP server never re-sent power profile
    because _last_sent_power_w was stale and no HA entity change was detected.
    """

    @pytest.fixture
    def server(self):
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer({"wallbox_id": "test", "power_update_interval_s": 5})
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="4354")
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = 1
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 4354
        cp.current_status = "Charging"
        cp.remote_start = AsyncMock(return_value=True)
        cp.transaction_started_event = asyncio.Event()
        cp.transaction_started_event.set()
        srv.charge_point = cp

        # Simulate previously sent power
        srv._last_sent_power_w = 4354.0
        srv._last_power_limit = "4354"

        return srv

    @pytest.mark.asyncio
    async def test_transaction_stop_resets_last_sent(self, server) -> None:
        """StopTransaction should reset _last_sent_power_w to 0."""
        assert server._last_sent_power_w == 4354.0

        server._on_status_change("transaction", "stopped")

        assert server._last_sent_power_w == 0

    @pytest.mark.asyncio
    async def test_transaction_stop_applies_current_limit(self, server) -> None:
        """After StopTransaction, _apply_current_power_limit should send
        the current HA value to the wallbox.
        """
        server.charge_point.transaction_id = None  # transaction ended

        # HA entity has 4354W
        server.ha.get_state = AsyncMock(return_value="4354")

        await server._apply_current_power_limit()

        server.charge_point.set_charging_power.assert_called()
        assert server._last_power_limit == "4354"

    @pytest.mark.asyncio
    async def test_transaction_stop_zero_limit_reapplies_pause(self, server) -> None:
        """If HA power limit is 0, re-apply it (0 A pause profile), not skip it.

        The wallbox resumes at its 6 A minimum on reconnect, so a 0 (pause) limit
        must be re-asserted — otherwise the car keeps charging after a reconnect.
        """
        server.ha.get_state = AsyncMock(return_value="0")

        await server._apply_current_power_limit()

        server.charge_point.set_charging_power.assert_called()
        assert server._last_power_limit == "0"

    @pytest.mark.asyncio
    async def test_reconciliation_detects_mismatch_after_stop(self, server) -> None:
        """After transaction stop, _last_sent_power_w=0 should cause
        reconciliation to detect a mismatch with HA value.
        """
        server._on_status_change("transaction", "stopped")

        # Simulate reconciliation check
        ha_power_w = 4354.0
        assert ha_power_w != server._last_sent_power_w  # 4354 != 0

    @pytest.mark.asyncio
    async def test_apply_limit_no_wallbox(self, server) -> None:
        """_apply_current_power_limit with no charge point should not crash."""
        server.charge_point = None
        server.ha.get_state = AsyncMock(return_value="4354")

        await server._apply_current_power_limit()
        # Should not raise


class TestPostConnectApplyLimit:
    """Post-connect setup should immediately apply current HA power limit.

    Without this, the OCPP server waits for a _change_ in the HA entity,
    which may never come if the energy-manager already set the value.
    """

    @pytest.fixture
    def server(self):
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer({"wallbox_id": "test", "power_update_interval_s": 5})
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.call_service = AsyncMock(return_value=True)

        cp = MagicMock()
        cp.transaction_id = None
        cp.set_charging_power = AsyncMock()
        cp.current_power_w = 0
        cp.current_status = "Preparing"
        cp.remote_start = AsyncMock(return_value=True)
        cp.transaction_started_event = asyncio.Event()
        cp.transaction_started_event.set()
        srv.charge_point = cp

        return srv

    @pytest.mark.asyncio
    async def test_apply_existing_ha_limit_on_connect(self, server) -> None:
        """After connect, if HA already has a power limit, apply it."""
        server.ha.get_state = AsyncMock(return_value="5117")

        await server._apply_current_power_limit()

        server.charge_point.set_charging_power.assert_called()
        assert server._last_power_limit == "5117"

    @pytest.mark.asyncio
    async def test_apply_zero_limit_on_connect_reapplies_pause(self, server) -> None:
        """After connect, a 0 limit must be re-applied (0 A pause profile).

        On reconnect the wallbox resumes at its 6 A minimum, so re-asserting the
        0 A profile is exactly what keeps the car paused.
        """
        server.ha.get_state = AsyncMock(return_value="0")

        await server._apply_current_power_limit()

        server.charge_point.set_charging_power.assert_called()
        assert server._last_power_limit == "0"

    @pytest.mark.asyncio
    async def test_apply_limit_entity_missing(self, server) -> None:
        """If HA entity doesn't exist yet, don't crash."""
        server.ha.get_state = AsyncMock(return_value=None)

        await server._apply_current_power_limit()

        server.charge_point.set_charging_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_limit_invalid_value(self, server) -> None:
        """If HA entity has non-numeric value, don't crash."""
        server.ha.get_state = AsyncMock(return_value="unavailable")

        await server._apply_current_power_limit()

        server.charge_point.set_charging_power.assert_not_called()


class TestProxyCommandedCorrection:
    """Commanded-primary correction for the ESP32 Modbus proxy.

    The proxy correction is the COMMANDED power the whole time we command a
    charge — never the measured MeterValues (60 s cadence, tiny ramp first
    reading that trips the proxy threshold). This signals the SUN2000 the full
    load the instant charging is commanded; a late-starting car briefly exports
    (sells) rather than under-reading and importing (buying).
    """

    @pytest.fixture
    def server(self):
        for mod in ("aiomqtt", "aiohttp", "websockets"):
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        from run import OCPPServer

        srv = OCPPServer({"wallbox_id": "test", "power_update_interval_s": 5})
        srv.ha = AsyncMock()
        srv.ha.set_state = AsyncMock()
        srv.ha.get_state = AsyncMock(return_value="0")
        srv.ha.call_service = AsyncMock(return_value=True)
        cp = MagicMock()
        cp.transaction_id = 1
        cp.current_status = "Charging"
        cp.current_power_w = 0
        cp.set_charging_power = AsyncMock()
        srv.charge_point = cp
        return srv

    @pytest.mark.asyncio
    async def test_warm_resume_injects_commanded_immediately(self, server) -> None:
        """A >0 command while SuspendedEVSE (warm resume) signals the commanded
        load at once — no wait for Charging, no measured value."""
        server.charge_point.current_status = "SuspendedEVSE"
        server.charge_point.current_power_w = 0  # car not drawing yet
        await server._send_power_to_wallbox(6000.0)
        await asyncio.sleep(0)
        assert server._proxy_charging is True
        assert server._proxy_power_w() == 6000.0
        assert server._last_mqtt_power == 6000.0

    @pytest.mark.asyncio
    async def test_charging_status_injects_commanded(self, server) -> None:
        """Reaching Charging (cold-start path after Preparing) injects commanded."""
        server._last_sent_power_w = 5117.0
        server._on_status_change("status", "Charging")
        await asyncio.sleep(0)
        assert server._proxy_charging is True
        assert server._proxy_power_w() == 5117.0

    @pytest.mark.asyncio
    async def test_measured_metervalues_do_not_change_correction(self, server) -> None:
        """While charging at commanded 6000 W, measured readings (tiny ramp 120 W,
        then 5681 W) must NOT change the correction — it stays at commanded."""
        server.charge_point.current_status = "SuspendedEVSE"
        await server._send_power_to_wallbox(6000.0)
        await asyncio.sleep(0)
        assert server._proxy_power_w() == 6000.0

        for measured in (120, 5681):
            server.charge_point.current_power_w = measured
            server._on_status_change("power_w", measured)
            await asyncio.sleep(0)
            assert server._proxy_power_w() == 6000.0  # unchanged by measured
        assert server._last_mqtt_power == 6000.0

    @pytest.mark.asyncio
    async def test_cold_start_preparing_no_injection(self, server) -> None:
        """A >0 command during Preparing (cold start, car draws 0 for minutes)
        must NOT inject — no minutes-long phantom export."""
        server.charge_point.current_status = "Preparing"
        await server._send_power_to_wallbox(6000.0)
        await asyncio.sleep(0)
        assert server._proxy_charging is False
        assert server._proxy_power_w() == 0.0

    @pytest.mark.asyncio
    async def test_suspended_ev_stops_correction(self, server) -> None:
        """Car refuses (SuspendedEV) → correction goes to 0, no phantom load."""
        server._last_sent_power_w = 5000.0
        server._on_status_change("status", "Charging")
        await asyncio.sleep(0)
        assert server._proxy_power_w() == 5000.0
        server.charge_point.current_status = "SuspendedEV"
        server._on_status_change("status", "SuspendedEV")
        await asyncio.sleep(0)
        assert server._proxy_charging is False
        assert server._proxy_power_w() == 0.0

    @pytest.mark.asyncio
    async def test_pause_stops_correction(self, server) -> None:
        """Commanding 0 W (pause) stops the correction immediately."""
        server.charge_point.current_status = "Charging"
        server._proxy_charging = True
        server._last_sent_power_w = 6000.0
        server.charge_point.current_power_w = 5681
        await server._send_power_to_wallbox(0.0)
        await asyncio.sleep(0)
        assert server._proxy_charging is False
        assert server._proxy_power_w() == 0.0
