"""Tests for the M-Bus grid-meter staleness watchdog (FSD 4.7.5).

The watchdog is the fix for a day-long M-Bus outage that went unnoticed because
the control loop's 20 s DTSU fallback is silent. It must:
  - stay quiet during normal fresh operation and brief gaps (the fallback);
  - raise exactly one "stale" edge once continuously stale past the threshold;
  - raise exactly one "recovered" edge when the meter returns, and only if an
    alert was actually sent.
"""

from __future__ import annotations

from src.mbus_watchdog import MbusWatchdog


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
