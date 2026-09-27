"""EV-23…26: remaining solar energy and the protected power-step boundary."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run import EnergyManager
from src.battery_optimizer import BatteryOptimizer
from src.ev_charging import build_solar_candidates, power_steps_for_phases


def forecast(times, energy):
    return pd.DataFrame({"net_energy_wh": energy}, index=pd.to_datetime(times, utc=True))


@pytest.mark.parametrize("energy, expected", [(1250, 85.7916667), (5000, 85.8333333)])
def test_only_remaining_minute_counts_with_power_limit(energy, expected):
    now = datetime(2026, 9, 25, 13, 14, tzinfo=UTC)
    fc = forecast(["2026-09-25T13:00Z", "2026-09-25T13:15Z"], [energy, 0])
    before = fc.copy(deep=True)
    reaches, peak, _ = BatteryOptimizer().reaches_target_today(85, fc, now, 95)
    assert not reaches
    assert peak == pytest.approx(expected)
    pd.testing.assert_frame_equal(fc, before)


def test_elapsed_slots_are_not_replayed():
    now = datetime(2026, 9, 25, 13, 15, tzinfo=UTC)
    fc = forecast(["2026-09-25T13:00Z", "2026-09-25T13:15Z"], [1250, 0])
    reaches, peak, _ = BatteryOptimizer().reaches_target_today(85, fc, now, 95)
    assert not reaches
    assert peak == 85


def test_final_slot_counts_but_tomorrow_does_not():
    now = datetime(2026, 9, 25, 21, 45, tzinfo=UTC)  # 23:45 Swiss
    fc = forecast(["2026-09-25T21:45Z", "2026-09-25T22:00Z"], [1000, 5000])
    opt = BatteryOptimizer()
    reaches, peak, _ = opt.reaches_target_today(85, fc, now, 94)
    assert reaches
    assert peak == pytest.approx(94.5)
    assert not opt.reaches_target_today(85, fc, now, 95)[0]


@pytest.mark.parametrize("phases,surplus", [(3, 3749), (1, 1300)])
@pytest.mark.parametrize("allowed,suppressed", [(False, False), (True, True), (True, False)])
def test_minimum_step_is_a_battery_draw(phases, surplus, allowed, suppressed):
    steps = power_steps_for_phases(phases)
    candidates, _ = build_solar_candidates(
        threshold=1200,
        step_up_allowed=allowed,
        both_full_by_evening=suppressed,
        steps=steps,
        surplus_w=surplus,
    )
    assert candidates == ([steps[0]] if allowed and not suppressed else [])


@pytest.fixture
def manager():
    options = {
        "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "test"},
        "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
        "battery": {"capacity_kwh": 10},
        "ev_charging": {"enabled": True},
    }
    with patch("run.ForecastReader"), patch("run.SimulationWriter"), patch("run.init_telegram"):
        mgr = EnergyManager(options)
    mgr.ha_client = MagicMock()
    for name in (
        "_refresh_runtime_settings",
        "write_energy_balance",
        "write_decision",
        "_update_discharge_control",
        "control_battery_charge",
        "publish_battery_decision",
        "calculate_appliance_signal",
        "_evaluate_both_full_by_evening",
    ):
        setattr(mgr, name, MagicMock())
    mgr.get_current_soc = MagicMock(return_value=50)
    mgr.forecast_reader.get_forecast_age_seconds.return_value = 0
    return mgr


def test_optimizer_caches_p10_pv_p90_load_for_ev(manager):
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    times = pd.date_range(start, periods=200, freq="15min")

    def read(**kwargs):
        conservative = (
            kwargs.get("pv_percentile") == "p10" and kwargs.get("load_percentile") == "p90"
        )
        return pd.DataFrame({"net_energy_wh": 0 if conservative else 1000}, index=times)

    manager.forecast_reader.get_combined_forecast.side_effect = read
    manager.run_optimization()
    assert manager._latest_forecast.net_energy_wh.eq(0).all()
    manager.publish_battery_decision.assert_called_once()


@pytest.mark.parametrize(
    "soc,surplus,energy,expected",
    [
        (85, 5000, 0, 0),  # Conservative target shortfall stops the car.
        (19, 3749, 1250, 0),  # Below floor: the minimum step is unaffordable.
        (50, 3749, 1250, 3962),  # Protected: bridge to the first step only.
    ],
)
def test_live_controller_enforces_target_and_step_floor(manager, soc, surplus, energy, expected):
    now = datetime(2026, 9, 25, 13, 0, tzinfo=UTC)
    manager._latest_forecast = forecast(pd.date_range(now, periods=16, freq="15min"), energy)
    manager._battery_min_soc_forecast = 80
    manager.ha_client.get_input_select.return_value = "solar"
    manager.ha_client.get_state.side_effect = lambda entity: {
        "state": "Charging" if entity == manager.ev_wallbox_status_entity else "on"
    }
    values = {
        manager.soc_entity: soc,
        manager.surplus_power_entity: surplus,
        manager.pv_power_entity: 5500,
        manager.ev_min_solar_power_entity: 3200,
    }
    manager.ha_client.get_sensor_value.side_effect = values.get
    manager._read_grid_power = MagicMock(return_value=0)
    with patch("run.datetime") as clock:
        clock.now.return_value = now
        manager.control_ev_charging()
    calls = manager.ha_client.set_sensor_state.call_args_list
    target = next(c for c in calls if c.args[0] == "sensor.ev_target_power")
    assert target.args[1] == expected


@pytest.mark.parametrize("failure", [pd.DataFrame(), RuntimeError("forecast unavailable")])
def test_failed_refresh_does_not_reuse_optimistic_cache(manager, failure):
    manager._latest_forecast = forecast(["2026-09-25T13:00Z"], [1000])
    if isinstance(failure, Exception):
        manager.forecast_reader.get_combined_forecast.side_effect = failure
    else:
        manager.forecast_reader.get_combined_forecast.return_value = failure
    manager.run_optimization()
    assert manager._latest_forecast is None


def test_partial_deficit_respects_remaining_discharge_time():
    now = datetime(2026, 9, 25, 13, 14, tzinfo=UTC)
    fc = forecast(["2026-09-25T13:00Z", "2026-09-25T13:15Z"], [-5000, 2000])
    _, peak, _ = BatteryOptimizer().reaches_target_today(50, fc, now, 65)
    assert peak == pytest.approx(50 - (5000 / 60) / 100 + 1250 / 100)


@pytest.mark.parametrize("times", [[], ["2026-09-25T12:45Z"], ["2026-09-25T22:00Z"]])
def test_no_remaining_today_forecast_fails_closed(times):
    now = datetime(2026, 9, 25, 13, 0, tzinfo=UTC)
    fc = forecast(times, [1250] * len(times))
    assert BatteryOptimizer().reaches_target_today(50, fc, now, 90) == (False, None, None)


@pytest.mark.parametrize("hour, allowed", [(0, False), (8, True)])
def test_discharge_uses_conservative_forecast_and_preserves_tariff(manager, hour, allowed):
    now = datetime(2026, 9, 28, hour, tzinfo=UTC)
    times = pd.date_range(now, periods=200, freq="15min")

    def read(**kwargs):
        conservative = (
            kwargs.get("pv_percentile") == "p10" and kwargs.get("load_percentile") == "p90"
        )
        return pd.DataFrame({"net_energy_wh": -300 if conservative else 1000}, index=times)

    manager.forecast_reader.get_combined_forecast.side_effect = read
    with patch("run.datetime") as clock:
        clock.now.return_value = now
        manager.run_optimization()
    decision = manager.publish_battery_decision.call_args.args[0]
    assert decision.discharge_allowed is allowed
    planned = manager.simulation_writer.write_soc_forecast.call_args.args[0]
    assert planned.net_wh.eq(-300).all()
    assert manager.write_energy_balance.call_args.args[0].net_energy_wh.eq(1000).all()
