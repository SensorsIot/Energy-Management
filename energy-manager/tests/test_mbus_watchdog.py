"""Tests for the M-Bus grid-meter staleness watchdog (FSD 4.7.5).

The watchdog is the fix for a day-long M-Bus outage that went unnoticed because
the control loop's 20 s DTSU fallback is silent. It must:
  - stay quiet during normal fresh operation and brief gaps (the fallback);
  - raise exactly one "stale" edge once continuously stale past the threshold;
  - raise exactly one "recovered" edge when the meter returns, and only if an
    alert was actually sent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from run import EnergyManager
from src.mbus_watchdog import MbusWatchdog

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
    with patch("run.ForecastReader"), patch("run.SimulationWriter"), patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)
    mgr.ha_client = MagicMock()
    return mgr


class TestFreshOperation:
    def test_fresh_from_start_is_silent(self) -> None:
        wd = MbusWatchdog(alert_after_s=300)
        assert wd.update(fresh=True, now_ts=1000.0) == ""

    def test_continuous_fresh_never_alerts(self) -> None:
        wd = MbusWatchdog(alert_after_s=300)
        for t in range(0, 10000, 10):
            assert wd.update(fresh=True, now_ts=float(t)) == ""


class TestBriefGap:
    def test_gap_shorter_than_threshold_is_silent(self) -> None:
        """A brief stale stretch (the silent DTSU fallback) must not alert."""
        wd = MbusWatchdog(alert_after_s=300)
        assert wd.update(fresh=False, now_ts=0.0) == ""
        assert wd.update(fresh=False, now_ts=120.0) == ""  # 2 min < 5 min
        assert wd.update(fresh=True, now_ts=130.0) == ""  # recovered, but never alerted
        assert wd.stale_since is None


class TestProlongedStaleness:
    def test_alerts_once_past_threshold(self) -> None:
        wd = MbusWatchdog(alert_after_s=300)
        assert wd.update(fresh=False, now_ts=0.0) == ""  # episode starts
        assert wd.update(fresh=False, now_ts=299.0) == ""  # not yet
        assert wd.update(fresh=False, now_ts=300.0) == "stale"  # threshold reached
        assert wd.update(fresh=False, now_ts=900.0) == ""  # one-shot: no repeat
        assert wd.update(fresh=False, now_ts=86400.0) == ""  # still silent a day later

    def test_recovery_after_alert(self) -> None:
        wd = MbusWatchdog(alert_after_s=300)
        wd.update(fresh=False, now_ts=0.0)
        assert wd.update(fresh=False, now_ts=300.0) == "stale"
        assert wd.update(fresh=True, now_ts=350.0) == "recovered"
        assert wd.update(fresh=True, now_ts=360.0) == ""  # only one recovery edge
        assert wd.stale_since is None

    def test_new_episode_after_recovery_alerts_again(self) -> None:
        wd = MbusWatchdog(alert_after_s=300)
        wd.update(fresh=False, now_ts=0.0)
        assert wd.update(fresh=False, now_ts=300.0) == "stale"
        assert wd.update(fresh=True, now_ts=310.0) == "recovered"
        # A second outage must alert again.
        assert wd.update(fresh=False, now_ts=400.0) == ""
        assert wd.update(fresh=False, now_ts=700.0) == "stale"

    def test_stale_seconds_reports_episode_length(self) -> None:
        wd = MbusWatchdog(alert_after_s=300)
        assert wd.stale_seconds(500.0) == 0.0  # fresh
        wd.update(fresh=False, now_ts=100.0)
        assert wd.stale_seconds(460.0) == 360.0


class TestReaderDisabled:
    """`sensors.mbus_enabled: false` (FSD 4.7.5) takes the reader out of
    service: the entity is never read, the watchdog is never fed, and every
    grid read comes from the DTSU meter.
    """

    def test_enabled_by_default(self, manager) -> None:
        assert manager.mbus_enabled is True

    def test_disabled_never_reads_the_mbus_entity(self, manager) -> None:
        manager.mbus_enabled = False
        manager.ha_client.get_sensor_value.return_value = -1234.0

        assert manager._read_grid_power() == -1234.0

        manager.ha_client.get_state.assert_not_called()
        manager.ha_client.get_sensor_value.assert_called_once_with(
            manager.dtsu_grid_power_entity
        )

    def test_disabled_never_feeds_the_watchdog(self, manager) -> None:
        """No watchdog input → no staleness alert for a meter known to be out."""
        manager.mbus_enabled = False
        manager.ha_client.get_sensor_value.return_value = 0.0
        manager._mbus_watchdog = MagicMock()

        for _ in range(50):
            manager._read_grid_power()

        manager._mbus_watchdog.update.assert_not_called()

    def test_disabled_falls_back_to_zero_when_dtsu_unavailable(self, manager) -> None:
        """Both meters gone → 0, the same fail-safe as the enabled path."""
        manager.mbus_enabled = False
        manager.ha_client.get_sensor_value.return_value = None
        assert manager._read_grid_power() == 0

    def test_enabled_still_prefers_a_fresh_mbus_reading(self, manager) -> None:
        """Regression: the switch must not disturb the normal path."""
        manager.mbus_enabled = True
        manager.ha_client.get_state.return_value = {
            "state": "-500.0",
            "last_updated": datetime.now(UTC).isoformat(),
        }
        manager.ha_client.get_sensor_value.return_value = 999.0

        assert manager._read_grid_power() == -500.0
