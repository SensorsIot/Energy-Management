"""EV-23…26: remaining solar energy and the protected power-step boundary."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run import EnergyManager
from src.battery_optimizer import BatteryOptimizer
from src.ev_charging import amp_steps, build_solar_candidates


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


# Surplus just below the 6 A step on each cable (6 A x 637 = 3822, x 230 = 1380),
# so taking the minimum step necessarily draws the shortfall from the battery.
@pytest.mark.parametrize("watts_per_amp,surplus", [(637, 3749), (230, 1300)])
@pytest.mark.parametrize("allowed,suppressed", [(False, False), (True, True), (True, False)])
def test_minimum_step_is_a_battery_draw(watts_per_amp, surplus, allowed, suppressed):
    steps = amp_steps(6, 16)
    candidates, _ = build_solar_candidates(
        threshold=1200,
        step_up_allowed=allowed,
        both_full_by_evening=suppressed,
        steps=steps,
        watts_per_amp=watts_per_amp,
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
        (50, 3749, 1250, 3822),  # Protected: bridge to the first step (6 A) only.
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
        pv, load = kwargs.get("pv_percentile"), kwargs.get("load_percentile")
        if pv == "p10" and load == "p50":
            net = -300
        elif pv == "p10" and load == "p90":
            net = -5000  # EV-gate input only; must not drive discharge.
        else:
            net = 1000
        return pd.DataFrame({"net_energy_wh": net}, index=times)

    manager.forecast_reader.get_combined_forecast.side_effect = read
    with patch("run.datetime") as clock:
        clock.now.return_value = now
        manager.run_optimization()
    decision = manager.publish_battery_decision.call_args.args[0]
    assert decision.discharge_allowed is allowed
    planned = manager.simulation_writer.write_soc_forecast.call_args.args[0]
    assert planned.net_wh.eq(-300).all()
    assert manager.write_energy_balance.call_args.args[0].net_energy_wh.eq(1000).all()


@pytest.mark.parametrize("target", [90, 100])
def test_ev_target_hysteresis_retains_pause_until_two_percent_recovery(manager, target):
    now = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
    manager._battery_target_soc = target
    manager._latest_forecast = forecast(
        pd.date_range(now, periods=2, freq="15min"), [750 / 0.95, 750 / 0.95]
    )
    manager._battery_min_soc_forecast = 80
    manager.ha_client.get_input_select.return_value = "solar"
    manager.ha_client.get_state.side_effect = lambda entity: {
        "state": "Charging" if entity == manager.ev_wallbox_status_entity else "on"
    }
    values = {
        manager.surplus_power_entity: 5000,
        manager.pv_power_entity: 5500,
        manager.ev_min_solar_power_entity: 3200,
    }
    manager.ha_client.get_sensor_value.side_effect = values.get
    manager._read_grid_power = MagicMock(return_value=0)
    # 15 percentage points of future charge. Once paused, reaching the target
    # exactly must not restart the car; two points of extra energy releases it.
    for soc, expected in [
        (target - 16, 0),
        (target - 15, 0),
        (target - 13, 5096),
        (target - 15, 5096),
        (target - 16, 0),
    ]:
        values[manager.soc_entity] = soc
        manager.ha_client.set_sensor_state.reset_mock()
        with patch("run.datetime") as clock:
            clock.now.return_value = now
            manager.control_ev_charging()
        calls = manager.ha_client.set_sensor_state.call_args_list
        power = next(c.args[1] for c in calls if c.args[0] == "sensor.ev_target_power")
        assert power == expected


@pytest.mark.parametrize(
    "soc,phases,stop", [(50, 3, 3200), (19, 3, 3822), (100, 3, 3200), (19, 1, 1380)]
)
def test_solar_start_stop_hysteresis(manager, soc, phases, stop):
    now = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
    manager._latest_forecast = forecast(pd.date_range(now, periods=16, freq="15min"), 1250)
    manager._battery_min_soc_forecast = 80
    manager.ha_client.get_input_select.return_value = "solar"
    manager.ha_client.get_state.side_effect = lambda entity: {
        "state": "Charging" if entity == manager.ev_wallbox_status_entity else "on"
    }
    values = {
        manager.soc_entity: soc,
        manager.pv_power_entity: 5500,
        manager.ev_min_solar_power_entity: 3200,
        "sensor.wallbox_phases": phases,
        "sensor.wallbox_min_current_a": 6,
        "sensor.wallbox_max_current_a": 16,
        "sensor.wallbox_watts_per_amp": 230 if phases == 1 else 637,
    }
    manager.ha_client.get_sensor_value.side_effect = values.get
    manager._read_grid_power = MagicMock(return_value=0)
    for surplus, charging in [
        (stop + 299, False),
        (stop + 300, True),
        (stop, True),
        (stop - 1, False),
        (stop + 299, False),
        (stop + 300, True),
    ]:
        values[manager.surplus_power_entity] = surplus
        # Supply a settled average to isolate hysteresis from smoothing.
        manager._surplus_samples = [surplus, surplus]
        manager.ha_client.set_sensor_state.reset_mock()
        with patch("run.datetime") as clock:
            clock.now.return_value = now
            manager.control_ev_charging()
        calls = manager.ha_client.set_sensor_state.call_args_list
        power = next(c.args[1] for c in calls if c.args[0] == "sensor.ev_target_power")
        assert (power > 0) is charging
        if soc < 20:
            assert power <= surplus
    # A battery target shortfall must still stop an active solar session.
    if soc < 100:
        manager._latest_forecast = forecast([now], [0])
        manager.ha_client.set_sensor_state.reset_mock()
        with patch("run.datetime") as clock:
            clock.now.return_value = now
            manager.control_ev_charging()
        calls = manager.ha_client.set_sensor_state.call_args_list
        assert next(c.args[1] for c in calls if c.args[0] == "sensor.ev_target_power") == 0


@pytest.mark.parametrize(
    "soc,minimum,suppressed,expected", [
        (99, 100, False, 3000),
        (19, 100, False, 4122),
        (99, 10, False, 4122),
        (99, 100, True, 4122),
        (100, 100, True, 3000),
    ]
)
def test_threshold_below_configured_surplus_uses_battery_support(
    manager, soc, minimum, suppressed, expected
):
    """EV-29: low surplus must not imply battery support is unavailable."""
    manager._battery_min_soc_forecast = minimum
    manager._step_up_suppressed = MagicMock(return_value=(suppressed, "test"))
    manager.ha_client.get_input_select.return_value = "solar"
    manager.ha_client.get_state.return_value = {"state": "on"}
    values = {
        manager.soc_entity: soc,
        manager.pv_power_entity: 5500,
        manager.surplus_power_entity: 2366,
        manager.ev_min_solar_power_entity: 2700,
        "sensor.wallbox_phases": 3,
        "sensor.wallbox_min_current_a": 6,
        "sensor.wallbox_max_current_a": 16,
        "sensor.wallbox_watts_per_amp": 637,
    }
    manager.ha_client.get_sensor_value.side_effect = values.get
    manager._read_grid_power = MagicMock(return_value=0)
    manager.control_ev_charging()
    call = next(c for c in manager.ha_client.set_sensor_state.call_args_list
                if c.args[0] == "sensor.ev_target_power")
    assert call.args[1] == 0
    assert call.kwargs["attributes"]["threshold_w"] == expected


@pytest.mark.parametrize(
    "phases,watts_per_amp,slider,expected_a",
    [
        # 1-phase: the slider is above what one phase can take, so the command
        # saturates at the wallbox maximum.
        (1, 230, 6800, 16),
        (1, 230, 1500, 6),
        # 3-phase: the slider genuinely selects a step. Regression — 1.9.32/33
        # commanded the maximum here, ignoring the slider and drawing ~3.4 kW
        # more than asked.
        (3, 637, 6800, 10),
        (3, 637, 11000, 16),
        (3, 637, 4000, 6),
    ],
)
def test_manual_mode_honours_the_power_slider(
    manager, phases, watts_per_amp, slider, expected_a
):
    """IMMEDIATE commands the highest amp step that fits the user's slider.

    Floored, never rounded up, so the command cannot exceed the power asked for.
    """
    manager.ha_client.get_input_select.return_value = "immediate"
    manager.ha_client.get_state.side_effect = lambda entity: {
        "state": "Charging" if entity == manager.ev_wallbox_status_entity else "on"
    }
    values = {
        manager.soc_entity: 50,
        manager.pv_power_entity: 0,
        manager.surplus_power_entity: 0,
        manager.manual_power_entity: slider,
        "sensor.wallbox_phases": phases,
        "sensor.wallbox_min_current_a": 6,
        "sensor.wallbox_max_current_a": 16,
        "sensor.wallbox_watts_per_amp": watts_per_amp,
        "sensor.wallbox_max_power_w": 16 * watts_per_amp,
    }
    manager.ha_client.get_sensor_value.side_effect = values.get
    manager._read_grid_power = MagicMock(return_value=0)

    manager.control_ev_charging()

    sent = [c for c in manager.ha_client.set_sensor_state.call_args_list
            if c.args[0] == manager.wallbox_current_limit_entity]
    assert sent, "no current limit was written"
    assert sent[-1].args[1] == expected_a
    # Never more than the slider asked for (unless saturated at the maximum).
    assert expected_a * watts_per_amp <= slider or expected_a == 16
