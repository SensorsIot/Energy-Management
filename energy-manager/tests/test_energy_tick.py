"""Tests for the single control tick and the guarded register writes.

Battery protection and EV charging both block home-battery discharge, so they
share the discharge decision. As two scheduler jobs they ran on two threads and
both could read the change-gate before either advanced it, so both wrote the
same register. One job removes that by construction (FSD 4.2.2, 4.7.1).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from run import EnergyManager

OPTIONS = {
    "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "t"},
    "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
    "battery": {"capacity_kwh": 10.0, "max_discharge_w": 5000, "max_charge_w": 5000},
    "tariff": {},
    "ev_charging": {"enabled": True},
    "schedule": {"update_interval_minutes": 15},
}

DISCHARGE = "number.battery_maximum_discharging_power"


def _manager(**overrides):
    options = {**OPTIONS, **overrides}
    with patch("run.ForecastReader"), \
         patch("run.SimulationWriter"), \
         patch("run.init_telegram"):
        mgr = EnergyManager(options)
    mgr.ha_client = MagicMock()
    mgr.ha_client.token = "fake"
    mgr.ha_client.last_write_sent = True
    mgr.ha_client.set_battery_discharge_power.return_value = (True, "")
    mgr.ha_client.set_number.return_value = (True, "")
    mgr.discharge_control_entity = DISCHARGE
    return mgr


@pytest.fixture()
def manager():
    return _manager()


# ===================================================================
# One job, both paths
# ===================================================================


class TestSingleControlJob:
    def test_only_one_control_job_is_scheduled(self, manager) -> None:
        """Regression: two interval jobs meant two threads and a race."""
        manager.run_optimization = MagicMock()
        manager.control_ev_charging = MagicMock()
        manager._publish_initial_sensors = MagicMock()
        manager.scheduler = MagicMock()

        manager.start()

        ids = {c.kwargs.get("id") for c in manager.scheduler.add_job.call_args_list}
        assert "energy_tick" in ids
        assert "optimization" not in ids
        assert "ev_charging" not in ids

    def test_optimization_waits_for_its_own_interval(self, manager) -> None:
        """The EV path ticks every 10 s; the battery path must not."""
        manager.run_optimization = MagicMock()
        manager.control_ev_charging = MagicMock()
        manager._last_optimization_at = 1.0

        with patch("run.time.monotonic", return_value=2.0):
            manager.energy_tick()
            manager.energy_tick()

        manager.run_optimization.assert_not_called()
        assert manager.control_ev_charging.call_count == 2

    def test_optimization_runs_once_its_interval_elapses(self, manager) -> None:
        manager.run_optimization = MagicMock()
        manager.control_ev_charging = MagicMock()
        manager._last_optimization_at = 0.0

        with patch("run.time.monotonic", return_value=15 * 60 + 1):
            manager.energy_tick()

        assert manager.run_optimization.call_count == 1

    def test_a_failing_optimization_still_leaves_the_ev_controlled(self, manager) -> None:
        """One job must not mean one point of failure."""
        manager.run_optimization = MagicMock(side_effect=RuntimeError("boom"))
        manager.control_ev_charging = MagicMock()
        manager._last_optimization_at = 0.0

        with patch("run.time.monotonic", return_value=15 * 60 + 1):
            manager.energy_tick()  # must not raise

        manager.control_ev_charging.assert_called_once()

    def test_a_failing_ev_path_does_not_escape_the_tick(self, manager) -> None:
        manager.run_optimization = MagicMock()
        manager.control_ev_charging = MagicMock(side_effect=RuntimeError("boom"))
        manager._last_optimization_at = 0.0

        with patch("run.time.monotonic", return_value=15 * 60 + 1):
            manager.energy_tick()  # must not raise

        manager.run_optimization.assert_called_once()

    def test_ev_path_is_skipped_when_ev_is_disabled(self) -> None:
        mgr = _manager(ev_charging={"enabled": False})
        mgr.run_optimization = MagicMock()
        mgr.control_ev_charging = MagicMock()

        mgr.energy_tick()

        mgr.control_ev_charging.assert_not_called()


# ===================================================================
# The write-rate guard, where it meets the registers
# ===================================================================


class TestGuardedDischargeWrites:
    def test_a_release_inside_the_dead_zone_is_held(self, manager) -> None:
        manager.ha_client.get_battery_discharge_power.return_value = 0.0
        manager._write_guard.record(DISCHARGE)  # a write just happened

        with patch("run.notify_error"), patch("run.time.sleep"):
            manager.control_battery(True)  # wants 5000 — permissive

        manager.ha_client.set_battery_discharge_power.assert_not_called()

    def test_a_held_write_leaves_the_gate_alone_so_it_retries(self, manager) -> None:
        manager.ha_client.get_battery_discharge_power.return_value = 0.0
        manager._write_guard.record(DISCHARGE)

        with patch("run.notify_error"), patch("run.time.sleep"):
            manager.control_battery(True)

        assert manager.last_discharge_allowed is not True

    def test_a_block_inside_the_dead_zone_still_writes(self, manager) -> None:
        """Deferring a block is what drained the battery into the car."""
        manager.ha_client.get_battery_discharge_power.side_effect = [5000.0, 0.0]
        manager._write_guard.record(DISCHARGE)

        with patch("run.notify_error"), patch("run.time.sleep"):
            manager.control_battery(False)  # wants 0 — protective

        assert manager.ha_client.set_battery_discharge_power.call_count == 1
        assert manager.last_discharge_allowed is False

    def test_a_held_write_sends_no_failure_notification(self, manager) -> None:
        """Being held back is not an error."""
        manager.ha_client.get_battery_discharge_power.return_value = 0.0
        manager._write_guard.record(DISCHARGE)

        with patch("run.notify_error") as notify, patch("run.time.sleep"):
            manager.control_battery(True)

        notify.assert_not_called()


class TestGuardedChargeWrites:
    def test_releasing_to_full_power_is_never_held(self, manager) -> None:
        """A charge limit stuck at 0 means a battery that never fills."""
        manager._write_guard.record(manager.charge_control_entity)
        manager._last_charge_power_w = 0

        manager._apply_charge_control(True, "test")

        assert manager.ha_client.set_number.call_count == 1

    def test_deferring_inside_the_dead_zone_is_held(self, manager) -> None:
        manager._write_guard.record(manager.charge_control_entity)
        manager._last_charge_power_w = manager.charge_max_w

        manager._apply_charge_control(False, "test")

        manager.ha_client.set_number.assert_not_called()
        # Cache untouched, so the next cycle re-applies.
        assert manager._last_charge_power_w == manager.charge_max_w


class TestBudgetAccounting:
    def test_a_deduped_no_op_costs_no_budget(self, manager) -> None:
        """Only writes that reached the inverter may count against the budget."""
        manager.ha_client.last_write_sent = False

        manager._guarded_register_write(
            DISCHARGE, 0, protective=True, label="test",
            writer=manager.ha_client.set_number,
        )

        assert manager._write_guard.writes_last_day(DISCHARGE) == 0

    def test_a_real_write_counts(self, manager) -> None:
        manager.ha_client.last_write_sent = True

        manager._guarded_register_write(
            DISCHARGE, 0, protective=True, label="test",
            writer=manager.ha_client.set_number,
        )

        assert manager._write_guard.writes_last_day(DISCHARGE) == 1

    def test_crossing_the_warning_threshold_notifies_once(self, manager) -> None:
        manager.ha_client.last_write_sent = True
        manager._write_guard.warn_per_day = 3

        with patch("run.notify_warning") as notify:
            for _ in range(6):
                manager._guarded_register_write(
                    DISCHARGE, 0, protective=True, label="test",
                    writer=manager.ha_client.set_number,
                )

        assert notify.call_count == 1
