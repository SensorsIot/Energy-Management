"""
Passive integration-test observer for EV charging.

Watches every 10 s control_ev_charging() cycle and checks off 24 test
cases (11 normal operation, 13 edge cases) as they naturally occur during
daily operation.  Results are persisted to a JSON file and Telegram
notifications are sent on status changes.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.ev_state_machine import EVInputs, EVOutput, EVState
from src.notifications import notify_error, notify_info

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CycleSnapshot:
    """All data from one control_ev_charging() cycle."""

    inputs: EVInputs
    output: EVOutput
    prev_state: EVState
    discharge_blocked_by_ev: bool
    last_power_limit_sent: float | None
    wb_connected: bool
    idle_since: datetime | None
    excess_w: float
    ts: datetime


@dataclass
class TestResult:
    """Persistent per-test state."""

    test_id: str
    name: str
    category: str  # "normal" | "edge"
    status: str = "pending"  # "pending" | "passed" | "failed"
    pass_count: int = 0
    fail_count: int = 0
    last_checked: str | None = None
    last_passed: str | None = None
    last_failed: str | None = None
    evidence: str = ""


# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------

@dataclass
class _TestDef:
    test_id: str
    name: str
    category: str
    detector: str  # method name on IntegrationObserver


_TEST_DEFS: list[_TestDef] = [
    # Normal operation
    _TestDef("NO-01", "NORMAL stays when wallbox unavailable", "normal", "_detect_no01"),
    _TestDef("NO-02", "NORMAL->SOLAR on excess>=min", "normal", "_detect_no02"),
    _TestDef("NO-03", "SOLAR power tracks excess", "normal", "_detect_no03"),
    _TestDef("NO-04", "SOLAR holds min when excess<min", "normal", "_detect_no04"),
    _TestDef("NO-06", "NORMAL->IMMEDIATE", "normal", "_detect_no06"),
    _TestDef("NO-07", "IMMEDIATE->NORMAL mode change", "normal", "_detect_no07"),
    _TestDef("NO-08", "Immediate->solar sends 0W", "normal", "_detect_no08"),
    _TestDef("NO-09", "NORMAL->CHEAP", "normal", "_detect_no09"),
    _TestDef("NO-10", "CHEAP charges at max (cheap tariff)", "normal", "_detect_no10"),
    _TestDef("NO-11", "CHEAP pauses (expensive tariff)", "normal", "_detect_no11"),
    _TestDef("NO-12", "IMMEDIATE blocks discharge", "normal", "_detect_no12"),
    # Edge cases
    _TestDef("EC-01", "SOLAR entered without battery protection", "edge", "_detect_ec01"),
    _TestDef("EC-02", "SOLAR does NOT block discharge", "edge", "_detect_ec02"),
    _TestDef("EC-03", "CHEAP blocks discharge when charging", "edge", "_detect_ec03"),
    _TestDef("EC-04", "CHEAP unblocks at expensive tariff", "edge", "_detect_ec04"),
    _TestDef("EC-08", "SOLAR->IMMEDIATE", "edge", "_detect_ec08"),
    _TestDef("EC-09", "SOLAR->CHEAP", "edge", "_detect_ec09"),
    _TestDef("EC-10", "Phase-gap snap down (batt<100%)", "edge", "_detect_ec10"),
    _TestDef("EC-11", "Phase-gap snap up (batt=100%)", "edge", "_detect_ec11"),
    _TestDef("EC-12", "Power limit sent only on change", "edge", "_detect_ec12"),
    _TestDef("EC-13", "Auto-revert: mode resets to solar", "edge", "_detect_ec13"),
    _TestDef("EC-14", "Faulted/Unknown -> NORMAL", "edge", "_detect_ec14"),
    _TestDef("EC-15", "CHEAP->NORMAL clears discharge", "edge", "_detect_ec15"),
    _TestDef("EC-16", "Idle detection exits to NORMAL", "edge", "_detect_ec16"),
]


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------

_REPORT_VERSION = "2"  # bump to invalidate stale results after formula changes


class IntegrationObserver:
    """Passive observer that checks off integration tests as they occur."""

    def __init__(self, report_path: str = "/config/ev_integration_tests.json") -> None:
        self._report_path = Path(report_path)
        self._results: dict[str, TestResult] = {}
        self._prev: CycleSnapshot | None = None

        # Initialise results from definitions
        for td in _TEST_DEFS:
            self._results[td.test_id] = TestResult(
                test_id=td.test_id,
                name=td.name,
                category=td.category,
            )

        # Load previous results from disk (preserves status across restarts)
        self._load_report()

    # -- public API --

    def observe(self, snapshot: CycleSnapshot) -> None:
        """Called each cycle.  Never raises."""
        try:
            self._observe_inner(snapshot)
        except Exception:
            logger.debug("Integration observer error", exc_info=True)

    # -- internals --

    def _observe_inner(self, snapshot: CycleSnapshot) -> None:
        changed = False
        now_iso = snapshot.ts.isoformat()

        for td in _TEST_DEFS:
            detector = getattr(self, td.detector)
            try:
                result = detector(self._prev, snapshot)
            except Exception:
                result = None

            if result is None:
                continue  # preconditions not met

            tr = self._results[td.test_id]
            tr.last_checked = now_iso

            if result:
                tr.pass_count += 1
                tr.last_passed = now_iso
                tr.evidence = self._evidence(snapshot)
                if tr.status == "pending":
                    tr.status = "passed"
                    changed = True
                    notify_info(
                        f"[PASS] {td.test_id}: {td.name}",
                        tr.evidence,
                        silent=True,
                    )
                elif tr.status == "failed":
                    tr.status = "passed"
                    changed = True
                    notify_info(
                        f"[RECOVERY] {td.test_id}: {td.name}",
                        tr.evidence,
                        silent=True,
                    )
            else:
                tr.fail_count += 1
                tr.last_failed = now_iso
                tr.evidence = self._evidence(snapshot)
                if tr.status == "pending":
                    tr.status = "failed"
                    changed = True
                    notify_error(
                        f"[FAIL] {td.test_id}: {td.name}",
                        tr.evidence,
                    )
                elif tr.status == "passed":
                    tr.status = "failed"
                    changed = True
                    notify_error(
                        f"[REGRESSION] {td.test_id}: {td.name}",
                        tr.evidence,
                    )

        self._prev = snapshot
        if changed:
            self._save_report()

    @staticmethod
    def _evidence(s: CycleSnapshot) -> str:
        i = s.inputs
        o = s.output
        return (
            f"state={o.state.value} prev={s.prev_state.value} "
            f"power={o.target_power_w:.0f}W mode={i.charging_mode} "
            f"excess={s.excess_w:.0f}W "
            f"blocked={s.discharge_blocked_by_ev} "
            f"last_sent={s.last_power_limit_sent}"
        )

    # -- persistence --

    def _save_report(self) -> None:
        try:
            tests = {tid: asdict(tr) for tid, tr in self._results.items()}
            passed = sum(1 for tr in self._results.values() if tr.status == "passed")
            failed = sum(1 for tr in self._results.values() if tr.status == "failed")
            pending = sum(1 for tr in self._results.values() if tr.status == "pending")
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "version": _REPORT_VERSION,
                "summary": {
                    "total": len(self._results),
                    "passed": passed,
                    "failed": failed,
                    "pending": pending,
                },
                "tests": tests,
            }
            # Atomic write via temp file + rename
            parent = self._report_path.parent
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=parent, suffix=".tmp", delete=False
            ) as tmp:
                json.dump(report, tmp, indent=2)
                tmp_path = Path(tmp.name)
            tmp_path.replace(self._report_path)
            logger.debug("Integration test report saved (%d passed, %d failed, %d pending)",
                         passed, failed, pending)
        except Exception:
            logger.debug("Failed to save integration test report", exc_info=True)

    def _load_report(self) -> None:
        try:
            if not self._report_path.exists():
                return
            data = json.loads(self._report_path.read_text())
            if data.get("version") != _REPORT_VERSION:
                logger.info(
                    "Report version mismatch (got %s, want %s) — starting fresh",
                    data.get("version"), _REPORT_VERSION,
                )
                return
            for tid, saved in data.get("tests", {}).items():
                if tid in self._results:
                    tr = self._results[tid]
                    tr.status = saved.get("status", "pending")
                    tr.pass_count = saved.get("pass_count", 0)
                    tr.fail_count = saved.get("fail_count", 0)
                    tr.last_checked = saved.get("last_checked")
                    tr.last_passed = saved.get("last_passed")
                    tr.last_failed = saved.get("last_failed")
                    tr.evidence = saved.get("evidence", "")
            logger.info("Loaded integration test report from %s", self._report_path)
        except Exception:
            logger.debug("Could not load integration test report", exc_info=True)

    # ===================================================================
    # Detectors
    #
    # Each returns True (pass), False (fail), or None (preconditions not met).
    # Arguments: prev (previous CycleSnapshot or None), curr (current).
    # ===================================================================

    # --- Normal Operation ---

    def _detect_no01(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-01: NORMAL stays when wallbox unavailable."""
        i = curr.inputs
        if i.charging_mode != "solar" or i.wallbox_available:
            return None
        return curr.output.state == EVState.NORMAL and curr.output.target_power_w == 0

    def _detect_no02(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-02: NORMAL->SOLAR on excess>=min."""
        i = curr.inputs
        if curr.prev_state != EVState.NORMAL:
            return None
        if i.charging_mode != "solar" or not i.wallbox_available:
            return None
        if curr.excess_w < i.min_power_w:
            return None
        return curr.output.state == EVState.SOLAR

    def _detect_no03(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-03: SOLAR power tracks excess."""
        i = curr.inputs
        if curr.output.state != EVState.SOLAR:
            return None
        if curr.excess_w < i.min_power_w:
            return None
        p = curr.output.target_power_w
        return i.min_power_w <= p <= i.max_power_w

    def _detect_no04(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-04: SOLAR holds min when excess<min."""
        i = curr.inputs
        if curr.output.state != EVState.SOLAR:
            return None
        if curr.excess_w >= i.min_power_w:
            return None
        return curr.output.target_power_w == i.min_power_w

    def _detect_no06(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-06: NORMAL->IMMEDIATE."""
        i = curr.inputs
        if curr.prev_state != EVState.NORMAL:
            return None
        if i.charging_mode != "immediate" or not i.wallbox_available:
            return None
        return (
            curr.output.state == EVState.IMMEDIATE
            and curr.output.target_power_w == i.max_power_w
        )

    def _detect_no07(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-07: IMMEDIATE->NORMAL mode change."""
        i = curr.inputs
        if curr.prev_state != EVState.IMMEDIATE:
            return None
        if i.charging_mode == "immediate":
            return None
        return curr.output.state == EVState.NORMAL and curr.output.target_power_w == 0

    def _detect_no08(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-08: Immediate->solar sends 0W (bug-fix check)."""
        i = curr.inputs
        if curr.prev_state != EVState.IMMEDIATE:
            return None
        if i.charging_mode != "solar":
            return None
        return curr.output.state == EVState.NORMAL and curr.output.target_power_w == 0

    def _detect_no09(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-09: NORMAL->CHEAP."""
        i = curr.inputs
        if curr.prev_state != EVState.NORMAL:
            return None
        if i.charging_mode != "cheap" or not i.wallbox_available:
            return None
        return curr.output.state == EVState.CHEAP

    def _detect_no10(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-10: CHEAP charges at max (cheap tariff)."""
        i = curr.inputs
        if curr.output.state != EVState.CHEAP:
            return None
        if not i.is_cheap_tariff:
            return None
        return curr.output.target_power_w == i.max_power_w

    def _detect_no11(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-11: CHEAP pauses (expensive tariff)."""
        i = curr.inputs
        if curr.output.state != EVState.CHEAP:
            return None
        if i.is_cheap_tariff:
            return None
        return curr.output.target_power_w == 0

    def _detect_no12(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """NO-12: IMMEDIATE blocks discharge."""
        if curr.output.state != EVState.IMMEDIATE:
            return None
        if curr.output.target_power_w <= 0:
            return None
        return curr.discharge_blocked_by_ev is True

    # --- Edge Cases ---

    def _detect_ec01(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-01: SOLAR entered without battery protection."""
        i = curr.inputs
        if curr.prev_state != EVState.NORMAL:
            return None
        if i.battery_protection_passed:
            return None  # only fires when protection is False
        if curr.excess_w < i.min_power_w:
            return None
        # Solar should still be entered (protection is informational, not blocking)
        return curr.output.state == EVState.SOLAR

    def _detect_ec02(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-02: SOLAR does NOT block discharge."""
        if curr.output.state != EVState.SOLAR:
            return None
        return curr.discharge_blocked_by_ev is False

    def _detect_ec03(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-03: CHEAP blocks discharge when charging."""
        i = curr.inputs
        if curr.output.state != EVState.CHEAP:
            return None
        if not i.is_cheap_tariff:
            return None
        if curr.output.target_power_w <= 0:
            return None
        return curr.discharge_blocked_by_ev is True

    def _detect_ec04(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-04: CHEAP unblocks at expensive tariff."""
        i = curr.inputs
        if curr.output.state != EVState.CHEAP:
            return None
        if i.is_cheap_tariff:
            return None
        if curr.output.target_power_w != 0:
            return None
        return curr.discharge_blocked_by_ev is False

    def _detect_ec08(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-08: SOLAR->IMMEDIATE."""
        i = curr.inputs
        if curr.prev_state != EVState.SOLAR:
            return None
        if i.charging_mode != "immediate":
            return None
        return (
            curr.output.state == EVState.IMMEDIATE
            and curr.output.target_power_w == i.max_power_w
        )

    def _detect_ec09(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-09: SOLAR->CHEAP."""
        i = curr.inputs
        if curr.prev_state != EVState.SOLAR:
            return None
        if i.charging_mode != "cheap":
            return None
        return curr.output.state == EVState.CHEAP

    def _detect_ec10(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-10: Phase-gap snap down (batt<100%)."""
        i = curr.inputs
        if curr.output.state != EVState.SOLAR:
            return None
        if not (3700 < curr.excess_w < 4140):
            return None
        if i.battery_soc >= 100:
            return None
        return curr.output.target_power_w == 3700

    def _detect_ec11(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-11: Phase-gap snap up (batt=100%)."""
        i = curr.inputs
        if curr.output.state != EVState.SOLAR:
            return None
        if not (3700 < curr.excess_w < 4140):
            return None
        if i.battery_soc < 100:
            return None
        return curr.output.target_power_w == 4140

    def _detect_ec12(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-12: Power limit sent only on change."""
        if prev is None:
            return None
        if curr.output.target_power_w != prev.output.target_power_w:
            return None  # power changed — not the scenario we're testing
        # Power unchanged: last_sent should equal the previous value (no new send)
        return curr.last_power_limit_sent == prev.last_power_limit_sent

    def _detect_ec13(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-13: Auto-revert — mode resets to solar after idle timeout."""
        if prev is None:
            return None
        prev_mode = prev.inputs.charging_mode
        curr_mode = curr.inputs.charging_mode
        if prev_mode not in ("immediate", "cheap"):
            return None
        if curr_mode != "solar":
            return None
        if curr.idle_since is None:
            return None
        idle_s = (curr.ts - curr.idle_since).total_seconds()
        if idle_s < 5 * 60:
            return None
        return curr.output.state == EVState.NORMAL

    def _detect_ec14(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-14: Faulted/Unknown wallbox status -> NORMAL."""
        i = curr.inputs
        if i.wallbox_status not in ("Faulted", "Unknown"):
            return None
        return curr.output.state == EVState.NORMAL and curr.output.target_power_w == 0

    def _detect_ec15(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-15: CHEAP->NORMAL clears discharge block."""
        i = curr.inputs
        if curr.prev_state != EVState.CHEAP:
            return None
        if i.charging_mode == "cheap":
            return None
        return (
            curr.output.state == EVState.NORMAL
            and curr.output.target_power_w == 0
            and curr.discharge_blocked_by_ev is False
        )

    def _detect_ec16(self, prev: CycleSnapshot | None, curr: CycleSnapshot) -> bool | None:
        """EC-16: Idle detection exits to NORMAL."""
        i = curr.inputs
        if curr.prev_state not in (EVState.SOLAR, EVState.CHEAP, EVState.IMMEDIATE):
            return None
        if not i.wallbox_idle:
            return None
        return curr.output.state == EVState.NORMAL and curr.output.target_power_w == 0
