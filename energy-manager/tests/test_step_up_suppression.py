"""Tests for Topic 2 Rule 4 — step-up suppression (FSD 4.3.7).

Step-up (one amp level above surplus, drawing the gap from the home battery) is
pointless when the conservative **p10** forecast already fills both the home
battery and the car by evening: the car lands at the same SOC either way, and
routing the gap through the home battery only pays its round-trip loss.

The rule is split across two cadences:

- `_evaluate_both_full_by_evening` (15 min) runs the p10 simulation and caches
  the battery verdict plus the **energy** the car can still receive by evening;
- `_step_up_suppressed` (10 s) combines that with the **live** car SOC and
  car-side target, so a mid-cycle change to either takes effect at once.

Both fail **open** (no suppression, Rule 3's floor gate governs alone).
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run import EnergyManager, SWISS_TZ


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
    with patch("run.ForecastReader"), \
         patch("run.SimulationWriter"), \
         patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)

    mgr.ha_client = MagicMock()
    mgr.smart_car_enabled = True
    mgr.smart_car_capacity_kwh = 50.0
    mgr.smart_car_charge_efficiency = 1.0
    mgr.capacity_wh = 10_000
    mgr._battery_target_soc = 90.0
    mgr._forecast_fresh = True
    return mgr


def forecast_from(net_wh_per_period: list[float], start_hour: int = 8):
    """Build a forecast frame of 15-min periods starting today at `start_hour`."""
    start = datetime.now(UTC).replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )
    idx = [start + timedelta(minutes=15 * i) for i in range(len(net_wh_per_period))]
    return pd.DataFrame({"net_energy_wh": net_wh_per_period}, index=idx)


class TestFifteenMinuteSimulation:
    """`_evaluate_both_full_by_evening` caches the battery verdict + car energy."""

    def test_caches_battery_verdict_and_car_energy(self, manager) -> None:
        # House starts at 50% (5 kWh), 9 kWh ceiling → 4 kWh headroom.
        # 24 kWh total: 4 kWh fills the house to its 90% target, 20 kWh to the car.
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._battery_full_by_evening is True
        assert manager._car_kwh_by_eod == pytest.approx(20.0)

    def test_battery_short_is_recorded(self, manager) -> None:
        # 2 kWh only: house climbs 50%→70%, short of its 90% target.
        manager._evaluate_both_full_by_evening(forecast_from([1000] * 2), 50.0)
        assert manager._battery_full_by_evening is False
        assert "short" in manager._both_full_reason

    def test_battery_peak_not_eod_counts(self, manager) -> None:
        """The battery hits its target midday then discharges into the evening —
        that still counts as reached, so an evening drain must not un-suppress."""
        manager._evaluate_both_full_by_evening(
            forecast_from([2000] * 12 + [-3000] * 4), 50.0
        )
        assert manager._battery_full_by_evening is True

    def test_periods_after_midnight_are_ignored(self, manager) -> None:
        """Tomorrow's sun must not count toward 'by evening'."""
        cutoff = (
            datetime.now(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        before = [cutoff - timedelta(minutes=30), cutoff - timedelta(minutes=15)]
        after = [cutoff + timedelta(minutes=15 * i) for i in range(1, 9)]
        frame = pd.DataFrame(
            {"net_energy_wh": [1000, 1000] + [20000] * 8},
            index=before + after,
        )
        manager._evaluate_both_full_by_evening(frame, 50.0)
        # Only the 2 kWh before the cutoff is allocated, and it all fits in the
        # house headroom — the car gains nothing from tomorrow's block.
        assert manager._car_kwh_by_eod == pytest.approx(0.0)

    def test_unavailable_inputs_clear_the_cache(self, manager) -> None:
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._car_kwh_by_eod is not None
        manager._forecast_fresh = False
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._car_kwh_by_eod is None
        assert manager._both_full_reason == "no fresh p10 forecast"

    @pytest.mark.parametrize(
        "setup,reason",
        [
            (lambda m: setattr(m, "_forecast_fresh", False), "no fresh p10 forecast"),
            (lambda m: setattr(m, "smart_car_enabled", False), "car sim unavailable"),
        ],
    )
    def test_fails_to_cache(self, manager, setup, reason) -> None:
        setup(manager)
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._car_kwh_by_eod is None
        assert manager._both_full_reason == reason

    def test_no_house_soc(self, manager) -> None:
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), None)
        assert manager._car_kwh_by_eod is None

    def test_empty_and_none_forecast(self, manager) -> None:
        manager._evaluate_both_full_by_evening(pd.DataFrame(), 50.0)
        assert manager._car_kwh_by_eod is None
        manager._evaluate_both_full_by_evening(None, 50.0)
        assert manager._car_kwh_by_eod is None


class TestLiveCheck:
    """`_step_up_suppressed` re-evaluates the car side every 10 s."""

    @pytest.fixture()
    def primed(self, manager):
        """Battery reaches its target; the car can still gain 20 kWh (= 40 pts)."""
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._battery_full_by_evening is True
        assert manager._car_kwh_by_eod == pytest.approx(20.0)
        return manager

    def test_car_reaches_target_suppresses(self, primed) -> None:
        # 50% + 40 pts = 90% ≥ 80% target.
        suppressed, reason = primed._step_up_suppressed(50.0, 80.0)
        assert suppressed is True
        assert "car EOD 90%/80% (ok)" in reason

    def test_car_short_of_target_allows_step_up(self, primed) -> None:
        # Same forecast, target raised to 100% → 90% is short.
        suppressed, reason = primed._step_up_suppressed(50.0, 100.0)
        assert suppressed is False
        assert "car EOD 90%/100% (short)" in reason

    def test_raising_the_target_flips_the_verdict_without_resimulating(
        self, primed
    ) -> None:
        """The regression this split fixes: the driver raises the car's charge
        limit mid-cycle and step-up is released on the next 10-s tick.

        Car at 55% gaining 40 pts lands at 95% — enough for a 90% target, short
        of a 100% one."""
        assert primed._step_up_suppressed(55.0, 90.0)[0] is True
        assert primed._step_up_suppressed(55.0, 100.0)[0] is False
        # No new simulation was needed.
        assert primed._car_kwh_by_eod == pytest.approx(20.0)

    def test_falling_car_soc_flips_the_verdict(self, primed) -> None:
        """A lower live SOC needs more energy to reach the same target."""
        assert primed._step_up_suppressed(50.0, 85.0)[0] is True
        assert primed._step_up_suppressed(40.0, 85.0)[0] is False

    def test_car_eod_is_capped_at_100(self, primed) -> None:
        # 90% + 40 pts would be 130%; a 100% target must still be reachable.
        assert primed._step_up_suppressed(90.0, 100.0)[0] is True

    def test_battery_short_never_suppresses(self, manager) -> None:
        manager._evaluate_both_full_by_evening(forecast_from([1000] * 2), 50.0)
        assert manager._battery_full_by_evening is False
        suppressed, reason = manager._step_up_suppressed(50.0, 60.0)
        assert suppressed is False
        assert "short" in reason


class TestLiveCheckFailsOpen:
    """Any missing input leaves Rule 3's floor gate governing alone."""

    def test_no_simulation_yet(self, manager) -> None:
        assert manager._car_kwh_by_eod is None
        assert manager._step_up_suppressed(50.0, 80.0)[0] is False

    def test_car_soc_unknown(self, manager) -> None:
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._step_up_suppressed(None, 80.0) == (False, "car SOC/target unknown")

    def test_car_target_unknown(self, manager) -> None:
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        assert manager._step_up_suppressed(50.0, None) == (False, "car SOC/target unknown")

    def test_stale_forecast_then_live_check(self, manager) -> None:
        manager._forecast_fresh = False
        manager._evaluate_both_full_by_evening(forecast_from([2000] * 12), 50.0)
        suppressed, reason = manager._step_up_suppressed(50.0, 80.0)
        assert suppressed is False
        assert reason == "no fresh p10 forecast"
