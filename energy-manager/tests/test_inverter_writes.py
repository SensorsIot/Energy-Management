"""Tests for no-op suppression on Huawei inverter register writes (FSD 4.7.1).

Every `number.battery_*` entity is an inverter holding register, and each write
costs a flash-erase cycle. `HAClient.set_number` is the single choke point for
all three of them, so the "never write an unchanged value" rule is enforced
there and no call site can bypass it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ha_client import HAClient

CHARGE_ENTITY = "number.battery_maximum_charging_power"


def _state_response(value):
    """Build a mocked HA /states response carrying `value`."""
    resp = MagicMock()
    resp.json.return_value = {"state": str(value), "attributes": {}}
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture()
def client():
    return HAClient(url="http://localhost:8123", token="fake")


# ---------------------------------------------------------------------------
# The rule: an unchanged value is never sent to the inverter
# ---------------------------------------------------------------------------


class TestNoOpSuppression:
    def test_unchanged_value_sends_no_write(self, client):
        """Register already at target -> no POST at all."""
        with patch("src.ha_client.requests") as req:
            req.get.return_value = _state_response(5000)
            ok, err = client.set_number(CHARGE_ENTITY, 5000)

        assert ok is True
        assert err == ""
        req.post.assert_not_called()

    def test_changed_value_is_written(self, client):
        """Register holds a different value -> exactly one POST."""
        with patch("src.ha_client.requests") as req:
            req.get.return_value = _state_response(5000)
            ok, _ = client.set_number(CHARGE_ENTITY, 0)

        assert ok is True
        assert req.post.call_count == 1
        assert req.post.call_args.kwargs["json"] == {
            "entity_id": CHARGE_ENTITY,
            "value": 0,
        }

    def test_float_noise_counts_as_unchanged(self, client):
        """A difference below WRITE_TOLERANCE is noise, not a change."""
        with patch("src.ha_client.requests") as req:
            req.get.return_value = _state_response(90.0)
            ok, _ = client.set_number("number.battery_end_of_charge_soc", 90.000001)

        assert ok is True
        req.post.assert_not_called()

    def test_real_change_just_above_tolerance_is_written(self, client):
        with patch("src.ha_client.requests") as req:
            req.get.return_value = _state_response(90.0)
            client.set_number("number.battery_end_of_charge_soc", 90.1)

        assert req.post.call_count == 1


# ---------------------------------------------------------------------------
# Fail-open: an unreadable register is written, never silently skipped
# ---------------------------------------------------------------------------


class TestFailOpen:
    @pytest.mark.parametrize("state", ["unavailable", "unknown"])
    def test_unparseable_state_still_writes(self, client, state):
        with patch("src.ha_client.requests") as req:
            req.get.return_value = _state_response(state)
            ok, _ = client.set_number(CHARGE_ENTITY, 5000)

        assert ok is True
        assert req.post.call_count == 1

    def test_read_failure_still_writes(self, client):
        with patch("src.ha_client.requests") as req:
            req.exceptions = requests.exceptions
            req.get.side_effect = requests.ConnectionError("boom")
            ok, _ = client.set_number(CHARGE_ENTITY, 5000)

        assert ok is True
        assert req.post.call_count == 1


# ---------------------------------------------------------------------------
# Retries must not multiply one logical change into several register writes
# ---------------------------------------------------------------------------


class TestRetryDoesNotMultiplyWrites:
    def test_lost_response_is_not_reposted(self, client):
        """HA accepted the call but the response was lost.

        The retry re-reads, sees the register already at target, and returns
        success without a second write.
        """
        with patch("src.ha_client.requests") as req:
            req.Timeout = requests.Timeout
            req.ConnectionError = requests.ConnectionError
            req.HTTPError = requests.HTTPError
            # Read 1: still the old value -> write. Read 2 (retry): landed.
            req.get.side_effect = [_state_response(0), _state_response(5000)]
            req.post.side_effect = requests.Timeout()

            ok, err = client.set_number(CHARGE_ENTITY, 5000, retry_delay=0)

        assert ok is True
        assert err == ""
        assert req.post.call_count == 1, "must not re-post a write that landed"

    def test_genuine_failure_still_retries_and_reports(self, client):
        """The register really is not at target -> retry, then report failure."""
        with patch("src.ha_client.requests") as req:
            req.Timeout = requests.Timeout
            req.ConnectionError = requests.ConnectionError
            req.HTTPError = requests.HTTPError
            req.get.return_value = _state_response(0)
            req.post.side_effect = requests.Timeout()

            ok, err = client.set_number(CHARGE_ENTITY, 5000, max_retries=3, retry_delay=0)

        assert ok is False
        assert "Timeout" in err
        assert req.post.call_count == 3


# ---------------------------------------------------------------------------
# Restart amplification: cold in-memory caches must not re-write the registers
# ---------------------------------------------------------------------------


class TestRestartAmplification:
    def test_cold_cache_does_not_rewrite_unchanged_register(self):
        """Regression: the add-on restarting re-wrote all three registers.

        `_last_charge_power_w` / `_last_soc_ceiling` are in-memory only, so
        after a restart they are None and the change-gate cannot suppress the
        first write. The choke-point guard has to catch it instead.
        """
        from run import EnergyManager

        options = {
            "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "t"},
            "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
            "battery": {"capacity_kwh": 10.0, "max_charge_w": 5000},
            "tariff": {},
            "ev_charging": {"enabled": False},
            "schedule": {"update_interval_minutes": 15},
        }
        with patch("run.ForecastReader"), \
             patch("run.SimulationWriter"), \
             patch("run.init_telegram"):
            mgr = EnergyManager(options)

        # Fresh start: nothing cached yet, inverter already at 5000 W.
        assert mgr._last_charge_power_w is None

        with patch("src.ha_client.requests") as req:
            req.get.return_value = _state_response(5000)
            mgr._apply_charge_control(True, "startup")

        req.post.assert_not_called()
        assert mgr._last_charge_power_w == 5000


# ---------------------------------------------------------------------------
# Post-write verification on the discharge register
# ---------------------------------------------------------------------------


DISCHARGE_ENTITY = "number.battery_maximum_discharging_power"

_MANAGER_OPTIONS = {
    "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "t"},
    "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
    "battery": {"capacity_kwh": 10.0, "max_discharge_w": 5000},
    "tariff": {},
    "ev_charging": {"enabled": False},
    "schedule": {"update_interval_minutes": 15},
}


@pytest.fixture()
def manager():
    """An EnergyManager whose HA client is a mock and whose polls do not sleep."""
    from run import EnergyManager

    with patch("run.ForecastReader"), \
         patch("run.SimulationWriter"), \
         patch("run.init_telegram"):
        mgr = EnergyManager(_MANAGER_OPTIONS)

    mgr.ha_client = MagicMock()
    mgr.ha_client.token = "fake"
    mgr.ha_client.set_battery_discharge_power.return_value = (True, "")
    mgr.discharge_control_entity = DISCHARGE_ENTITY
    return mgr


class TestDischargeVerification:
    """The register appears in the HA state machine *after* the Modbus write.

    The Huawei Solar integration republishes the entity asynchronously, measured
    at ~2.4 s on 2026-10-22. A single read 1 s after the POST therefore returned
    the pre-write value and reported a false mismatch at both tariff flips every
    day. Verification polls instead (FSD 4.7.1).
    """

    def test_verification_waits_out_the_state_machine_lag(self, manager) -> None:
        """Stale reads are not a mismatch while the poll window is open."""
        manager.ha_client.get_battery_discharge_power.side_effect = [
            5000.0,  # pre-write read: the register really is at 5000
            5000.0,  # verify poll 1: integration has not republished yet
            5000.0,  # verify poll 2: still stale
            0.0,     # verify poll 3: caught up
        ]

        with patch("run.notify_error") as notify, patch("run.time.sleep"):
            manager.control_battery(False)

        notify.assert_not_called()
        assert manager.last_discharge_allowed is False

    def test_verification_alerts_when_the_register_never_arrives(self, manager) -> None:
        """A register that stays wrong past the window is a real mismatch."""
        manager.ha_client.get_battery_discharge_power.return_value = 5000.0

        with patch("run.notify_error") as notify, patch("run.time.sleep"):
            manager.control_battery(False)

        assert notify.call_count == 1
        assert "Verification Failed" in notify.call_args.kwargs["title"]
        # The gate stays untouched so the next cycle retries.
        assert manager.last_discharge_allowed is None

    def test_successful_flip_is_not_written_twice(self, manager) -> None:
        """Regression: a false mismatch left the gate unset, so the second
        caller of `_update_discharge_control` wrote the same value again —
        two flash cycles per tariff flip instead of one.
        """
        manager.ha_client.get_battery_discharge_power.side_effect = [
            5000.0,  # caller 1 pre-write read
            5000.0,  # verify poll 1 (stale — this is what used to alert)
            5000.0,  # verify poll 2 (still stale)
            0.0,     # verify poll 3 (caught up)
        ]
        manager._discharge_blocked_by_protection = True
        assert manager.last_discharge_allowed is None

        with patch("run.notify_error"), patch("run.time.sleep"):
            manager._update_discharge_control()   # protection flag flipped
            manager._update_discharge_control()   # EV path, same tick

        assert manager.ha_client.set_battery_discharge_power.call_count == 1


class TestUnreadableRegisterStorm:
    """Fail-open must not become a write-per-minute loop.

    Fail-open exists so an unreadable register is never stranded, but the
    60 s reconcile cadence re-applied it indefinitely: on 2026-10-21 the Huawei
    integration was `unavailable` for 14 minutes and the register took 14 writes
    of a value it already held, none of which could land (FSD 4.7.1).
    """

    def test_unreadable_register_skips_the_reconcile_write(self, manager) -> None:
        """Nothing changed and nothing can be read -> no write."""
        manager.ha_client.get_battery_discharge_power.return_value = None

        with patch("run.notify_error") as notify, patch("run.time.sleep"):
            manager.control_battery(False, force=False)

        manager.ha_client.set_battery_discharge_power.assert_not_called()
        notify.assert_not_called()

    def test_unreadable_register_still_writes_on_a_decision_change(self, manager) -> None:
        """A real decision change still fails open, so nothing is stranded."""
        manager.ha_client.get_battery_discharge_power.return_value = None

        with patch("run.notify_error"), patch("run.time.sleep"):
            manager.control_battery(False, force=True)

        assert manager.ha_client.set_battery_discharge_power.call_count == 1

    def test_reconcile_corrects_once_the_register_is_readable_again(self, manager) -> None:
        """The skip self-heals: real drift is still corrected."""
        manager.ha_client.get_battery_discharge_power.side_effect = [
            None,    # unavailable -> reconcile skipped
            5000.0,  # readable again, and drifted
            0.0,     # verify
        ]

        with patch("run.notify_error"), patch("run.time.sleep"):
            manager.control_battery(False, force=False)
            manager.control_battery(False, force=False)

        assert manager.ha_client.set_battery_discharge_power.call_count == 1


class TestConcurrentCallers:
    """`optimization` (15 min) and `ev_charging` (10 s) are separate APScheduler
    jobs on separate threads, and `max_instances=1` is per job. Both set a
    discharge flag and call `_update_discharge_control`, so without a lock both
    evaluate the change-gate before either advances it and both write — two
    flash cycles per decision flip, observed live 2026-10-23 and 10-24.
    """

    def test_concurrent_callers_write_once(self, manager) -> None:
        import threading
        import time as _t

        written = threading.Event()

        def slow_set(entity_id, value, max_retries=5):
            # Hold the window open the way a real HA round trip does.
            _t.sleep(0.2)
            written.set()
            return True, ""

        def read(entity_id):
            return 0.0 if written.is_set() else 5000.0

        manager.ha_client.set_battery_discharge_power.side_effect = slow_set
        manager.ha_client.get_battery_discharge_power.side_effect = read
        manager._discharge_blocked_by_protection = True

        threads = [
            threading.Thread(target=manager._update_discharge_control) for _ in range(2)
        ]
        with patch("run.notify_error") as notify:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

        assert manager.ha_client.set_battery_discharge_power.call_count == 1
        assert manager.last_discharge_allowed is False
        notify.assert_not_called()
