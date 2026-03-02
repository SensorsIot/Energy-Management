"""
Tests for the passive integration-test observer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from src.ev_state_machine import EVInputs, EVOutput, EVState
from src.integration_observer import (
    CycleSnapshot,
    IntegrationObserver,
    _REPORT_VERSION,
    _TEST_DEFS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**overrides) -> EVInputs:
    defaults = dict(
        wallbox_available=True,
        wallbox_power_w=0,
        wallbox_status="Preparing",
        wallbox_idle=False,
        battery_soc=70.0,
        charging_mode="solar",
        is_cheap_tariff=False,
        grid_power_w=-5000.0,
        surplus_power_w=5000.0,
        pv_power_w=8000.0,
        load_power_w=3000.0,
        min_power_w=1400.0,
        manual_power_w=11000.0,
        ev_charging_power_w=0.0,
        ev_forecasted_power_w=0.0,
        battery_protection_passed=True,
    )
    defaults.update(overrides)
    return EVInputs(**defaults)


def _make_snapshot(
    *,
    inputs: EVInputs | None = None,
    output: EVOutput | None = None,
    prev_state: EVState = EVState.IDLE,
    discharge_blocked: bool = False,
    last_sent: float | None = None,
    wb_connected: bool = True,
    idle_since: datetime | None = None,
    excess: float = 5000.0,
    ts: datetime | None = None,
) -> CycleSnapshot:
    if inputs is None:
        inputs = _make_inputs()
    if output is None:
        output = EVOutput(EVState.IDLE, 0, "test")
    if ts is None:
        ts = datetime.now(timezone.utc)
    return CycleSnapshot(
        inputs=inputs,
        output=output,
        prev_state=prev_state,
        discharge_blocked_by_ev=discharge_blocked,
        last_power_limit_sent=last_sent,
        wb_connected=wb_connected,
        idle_since=idle_since,
        excess_w=excess,
        ts=ts,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_all_23_tests_registered(self):
        assert len(_TEST_DEFS) == 23

    def test_initial_status_pending(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "report.json"))
        for tr in obs._results.values():
            assert tr.status == "pending"

    def test_test_ids_unique(self):
        ids = [td.test_id for td in _TEST_DEFS]
        assert len(ids) == len(set(ids))

    def test_categories(self):
        normal = [td for td in _TEST_DEFS if td.category == "normal"]
        edge = [td for td in _TEST_DEFS if td.category == "edge"]
        assert len(normal) == 11
        assert len(edge) == 12


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        path = str(tmp_path / "report.json")
        obs = IntegrationObserver(report_path=path)

        # Trigger NO-01: IDLE stays when wallbox unavailable
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_available=False, charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV charging"),
        )
        obs.observe(snap)

        # Verify saved
        data = json.loads(Path(path).read_text())
        assert data["tests"]["NO-01"]["status"] == "passed"
        assert data["summary"]["passed"] >= 1

        # Reload into fresh observer
        obs2 = IntegrationObserver(report_path=path)
        assert obs2._results["NO-01"].status == "passed"
        assert obs2._results["NO-01"].pass_count == 1

    def test_missing_report_file(self, tmp_path):
        path = str(tmp_path / "nonexistent" / "report.json")
        obs = IntegrationObserver(report_path=path)
        assert obs._results["NO-01"].status == "pending"

    def test_version_mismatch_resets_results(self, tmp_path):
        """Old report with different version is ignored — all tests start pending."""
        path = tmp_path / "report.json"
        old_report = {
            "version": "0-stale",
            "summary": {"total": 23, "passed": 10, "failed": 0, "pending": 13},
            "tests": {
                "NO-01": {"test_id": "NO-01", "name": "x", "category": "normal",
                          "status": "passed", "pass_count": 42},
            },
        }
        path.write_text(json.dumps(old_report))
        obs = IntegrationObserver(report_path=str(path))
        assert obs._results["NO-01"].status == "pending"
        assert obs._results["NO-01"].pass_count == 0

    def test_current_version_loads_normally(self, tmp_path):
        """Report with matching version is loaded."""
        path = tmp_path / "report.json"
        report = {
            "version": _REPORT_VERSION,
            "summary": {"total": 23, "passed": 1, "failed": 0, "pending": 22},
            "tests": {
                "NO-01": {"test_id": "NO-01", "name": "x", "category": "normal",
                          "status": "passed", "pass_count": 5},
            },
        }
        path.write_text(json.dumps(report))
        obs = IntegrationObserver(report_path=str(path))
        assert obs._results["NO-01"].status == "passed"
        assert obs._results["NO-01"].pass_count == 5


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class TestNotifications:
    @patch("src.integration_observer.notify_info")
    def test_first_pass_sends_silent_info(self, mock_info, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_available=False, charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV charging"),
        )
        obs.observe(snap)
        mock_info.assert_called()
        call_args = mock_info.call_args
        assert "[PASS] NO-01" in call_args[0][0]
        assert call_args[1].get("silent", call_args[0][2] if len(call_args[0]) > 2 else True)

    @patch("src.integration_observer.notify_error")
    @patch("src.integration_observer.notify_info")
    def test_regression_sends_error(self, mock_info, mock_error, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        # First: pass NO-12
        snap_pass = _make_snapshot(
            inputs=_make_inputs(charging_mode="immediate"),
            output=EVOutput(EVState.IMMEDIATE, 11000, "Immediate"),
            prev_state=EVState.IMMEDIATE,
            discharge_blocked=True,
        )
        obs.observe(snap_pass)
        assert obs._results["NO-12"].status == "passed"

        # Second: fail NO-12 (blocked should be True but we set False)
        snap_fail = _make_snapshot(
            inputs=_make_inputs(charging_mode="immediate"),
            output=EVOutput(EVState.IMMEDIATE, 11000, "Immediate"),
            prev_state=EVState.IMMEDIATE,
            discharge_blocked=False,
        )
        obs.observe(snap_fail)
        assert obs._results["NO-12"].status == "failed"
        # Should have called notify_error with REGRESSION
        regression_calls = [
            c for c in mock_error.call_args_list
            if "REGRESSION" in str(c)
        ]
        assert len(regression_calls) >= 1

    @patch("src.integration_observer.notify_info")
    def test_recovery_sends_silent_info(self, mock_info, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        # Force failed state
        obs._results["NO-12"].status = "failed"

        snap = _make_snapshot(
            inputs=_make_inputs(charging_mode="immediate"),
            output=EVOutput(EVState.IMMEDIATE, 11000, "Immediate"),
            prev_state=EVState.IMMEDIATE,
            discharge_blocked=True,
        )
        obs.observe(snap)
        assert obs._results["NO-12"].status == "passed"
        recovery_calls = [c for c in mock_info.call_args_list if "RECOVERY" in str(c)]
        assert len(recovery_calls) >= 1


# ---------------------------------------------------------------------------
# Precondition skips (None return = no false positives)
# ---------------------------------------------------------------------------

class TestPreconditions:
    def test_no02_skips_when_not_idle(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            prev_state=EVState.SOLAR,
            output=EVOutput(EVState.SOLAR, 5000, "Solar"),
        )
        result = obs._detect_no02(None, snap)
        assert result is None

    def test_ec12_skips_without_prev(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot()
        result = obs._detect_ec12(None, snap)
        assert result is None

    def test_ec13_skips_when_not_reverting(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        prev = _make_snapshot(inputs=_make_inputs(charging_mode="solar"))
        curr = _make_snapshot(inputs=_make_inputs(charging_mode="solar"))
        result = obs._detect_ec13(prev, curr)
        assert result is None


# ---------------------------------------------------------------------------
# Exception safety
# ---------------------------------------------------------------------------

class TestExceptionSafety:
    @patch("src.integration_observer.notify_info", side_effect=Exception("boom"))
    def test_observe_never_raises(self, mock_info, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_available=False, charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV charging"),
        )
        # Should not raise even if notification explodes
        obs.observe(snap)


# ---------------------------------------------------------------------------
# Individual detector spot-checks
# ---------------------------------------------------------------------------

class TestDetectors:
    def test_no01_pass(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_available=False, charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV"),
        )
        assert obs._detect_no01(None, snap) is True

    def test_no01_skip_when_available(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_available=True, charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV"),
        )
        assert obs._detect_no01(None, snap) is None

    def test_no02_pass_charging_power(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=3000),
            output=EVOutput(EVState.SOLAR, 3000, "Solar"),
            prev_state=EVState.IDLE,
        )
        assert obs._detect_no02(None, snap) is True

    def test_no02_skip_power_zero(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=0),
            output=EVOutput(EVState.IDLE, 0, "No charging"),
            prev_state=EVState.IDLE,
        )
        assert obs._detect_no02(None, snap) is None

    def test_no05_pass(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=5000),
            output=EVOutput(EVState.SOLAR, 5000, "Solar"),
        )
        assert obs._detect_no05(None, snap) is True

    def test_no05_fail_mismatch(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=5000),
            output=EVOutput(EVState.SOLAR, 3000, "Solar mismatch"),
        )
        assert obs._detect_no05(None, snap) is False

    def test_no05_skip_not_solar(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=5000),
            output=EVOutput(EVState.IDLE, 0, "Normal"),
        )
        assert obs._detect_no05(None, snap) is None

    def test_no13_pass(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        prev = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 3000, "Solar"),
        )
        curr = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=0),
            output=EVOutput(EVState.IDLE, 0, "No power"),
            prev_state=EVState.SOLAR,
        )
        assert obs._detect_no13(prev, curr) is True

    def test_no13_skip_idle(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        now = datetime.now(timezone.utc)
        prev = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 3000, "Solar"),
        )
        curr = _make_snapshot(
            inputs=_make_inputs(ev_charging_power_w=0, wallbox_idle=True),
            output=EVOutput(EVState.IDLE, 0, "Idle"),
            prev_state=EVState.SOLAR,
            ts=now,
        )
        assert obs._detect_no13(prev, curr) is None

    def test_ec05_pass(self, tmp_path):
        """Battery protection blocks: ev_forecasted_power_w>0 but ev_charging_power_w=0."""
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(
                battery_protection_passed=False,
                ev_forecasted_power_w=3000,
                ev_charging_power_w=0,
            ),
            output=EVOutput(EVState.IDLE, 0, "Blocked by protection"),
            prev_state=EVState.IDLE,
        )
        assert obs._detect_ec05(None, snap) is True

    def test_ec05_skip_protection_passed(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(
                battery_protection_passed=True,
                ev_forecasted_power_w=3000,
                ev_charging_power_w=3000,
            ),
            output=EVOutput(EVState.SOLAR, 3000, "Solar"),
            prev_state=EVState.IDLE,
        )
        assert obs._detect_ec05(None, snap) is None

    def test_ec06_pass(self, tmp_path):
        """Protection failure while in SOLAR → exits to IDLE."""
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        prev = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 3000, "Solar"),
            prev_state=EVState.SOLAR,
        )
        curr = _make_snapshot(
            inputs=_make_inputs(battery_protection_passed=False, ev_charging_power_w=0),
            output=EVOutput(EVState.IDLE, 0, "Protection exit"),
            prev_state=EVState.SOLAR,
        )
        assert obs._detect_ec06(prev, curr) is True

    def test_ec02_pass(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 5000, "Solar"),
            discharge_blocked=False,
        )
        assert obs._detect_ec02(None, snap) is True

    def test_ec02_fail(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 5000, "Solar"),
            discharge_blocked=True,
        )
        assert obs._detect_ec02(None, snap) is False

    def test_ec12_pass_no_change(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        prev = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 5000, "Solar"),
            last_sent=5000,
        )
        curr = _make_snapshot(
            output=EVOutput(EVState.SOLAR, 5000, "Solar"),
            last_sent=5000,
        )
        assert obs._detect_ec12(prev, curr) is True

    def test_ec14_faulted(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_status="Faulted"),
            output=EVOutput(EVState.IDLE, 0, "No EV"),
        )
        assert obs._detect_ec14(None, snap) is True

    def test_ec15_pass(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV"),
            prev_state=EVState.CHEAP,
            discharge_blocked=False,
        )
        assert obs._detect_ec15(None, snap) is True

    def test_ec13_auto_revert(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        now = datetime.now(timezone.utc)
        prev = _make_snapshot(
            inputs=_make_inputs(charging_mode="immediate"),
            ts=now - timedelta(minutes=6),
        )
        curr = _make_snapshot(
            inputs=_make_inputs(charging_mode="solar"),
            output=EVOutput(EVState.IDLE, 0, "No EV"),
            idle_since=now - timedelta(minutes=6),
            ts=now,
        )
        assert obs._detect_ec13(prev, curr) is True

    def test_ec16_idle_exits_solar(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_idle=True),
            output=EVOutput(EVState.IDLE, 0, "Car finished"),
            prev_state=EVState.SOLAR,
        )
        assert obs._detect_ec16(None, snap) is True

    def test_ec16_skips_when_not_idle(self, tmp_path):
        obs = IntegrationObserver(report_path=str(tmp_path / "r.json"))
        snap = _make_snapshot(
            inputs=_make_inputs(wallbox_idle=False),
            output=EVOutput(EVState.SOLAR, 1400, "Solar"),
            prev_state=EVState.SOLAR,
        )
        assert obs._detect_ec16(None, snap) is None
