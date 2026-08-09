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
