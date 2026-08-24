"""Tests for the export-peak-shaving day mode + gate (FSD 4.2.3).

The shave-vs-car-day choice is decided once per day at shaving_decision_hour
and latched (`_update_shaving_day_mode`); `_charge_gate_active()` is true only
on a shaving day. The sole criterion is whether the EV is **full** — its SOC
(`smart_battery_last_known`) at/above its charging target
(`smart_charging_max_last_known`). Connection is deliberately NOT a criterion:
the EV can come and go at any time, so only its charge level decides — a full
car (here or away) won't need the surplus, while a car below target will (now
or on return), so the battery banks greedily. The last-known SOC makes this
valid whether or not the car is plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from run import EnergyManager
from src.battery_optimizer import BatteryOptimizer

MINIMAL_OPTIONS = {
    "influxdb": {"host": "localhost", "port": 8087, "token": "x", "org": "test"},
    "home_assistant": {"url": "http://localhost:8123", "token": "fake"},
    "battery": {"capacity_kwh": 10.0, "max_discharge_w": 5000},
    "tariff": {},
    "ev_charging": {"enabled": True},
    "schedule": {"update_interval_minutes": 15},
}


@dataclass
class FakeTariff:
    is_cheap_now: bool = False


@pytest.fixture()
def manager():
    with patch("run.ForecastReader"), patch("run.SimulationWriter"), patch("run.init_telegram"):
        mgr = EnergyManager(MINIMAL_OPTIONS)
    mgr.ha_client = MagicMock()
    mgr.optimizer = MagicMock()
    return mgr


def _wire(
    manager,
    *,
    car_soc: float | None = None,
    target: float | None = None,
) -> None:
    """Drive the shaving premise off the EV SOC vs target only.

    Fullness (``car_soc >= target``) is the sole shave-vs-car-day criterion;
    connection is not a factor (FSD 4.2.3). ``car_soc=None`` models an EV whose
    SOC is unknown/unavailable (held as a stale read).
    """

    def _sensor(entity):
        if entity == manager.car_soc_last_known_entity:
            return car_soc
        if entity == manager.car_charging_max_entity:
            return target
        return None

    manager.ha_client.get_sensor_value.side_effect = _sensor


# Decision-hour times (Europe/Zurich = UTC+2 in June).
_AT_DECISION = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)      # 12:00 local ≥ 8
_BEFORE_DECISION = datetime(2026, 6, 7, 4, 0, tzinfo=UTC)   # 06:00 local < 8


class TestDayModeDecision:
    """The shave-vs-car-day choice is decided once per day at
    shaving_decision_hour and latched (FSD 4.2.3)."""

    def test_car_full_soc_unknown_is_car_day(self, manager) -> None:
        # SOC unavailable (Smart integration down) at the latch → car day (safe).
        _wire(manager, car_soc=None, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        assert manager._charge_gate_active() is False

    def test_car_below_target_at_decision_is_car_day(self, manager) -> None:
        _wire(manager, car_soc=50.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        assert manager._charge_gate_active() is False

    def test_full_car_at_decision_is_shaving_day(self, manager) -> None:
        # Full car (SOC ≥ target) → shaving day, regardless of whether it is
        # plugged in. This is the core premise; connection is not checked.
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "shaving_day"
        assert manager._charge_gate_active() is True

    def test_full_car_over_target_is_shaving_day(self, manager) -> None:
        _wire(manager, car_soc=90.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "shaving_day"

    def test_reads_last_known_soc_not_volatile(self, manager) -> None:
        # The volatile sensor.smart_battery is unavailable (car asleep — a
        # sleeping car doesn't consume, so last-known is still valid) but the
        # last-known SOC is full → shaving day. Regression: the decision must
        # read the last-known sensor, else a full sleeping car never shaves.
        def _sensor(entity):
            if entity == manager.smart_car_soc_entity:
                return None  # volatile sensor unavailable (asleep)
            if entity == manager.car_soc_last_known_entity:
                return 80.0  # last-known held
            if entity == manager.car_charging_max_entity:
                return 80.0
            return None

        manager.ha_client.get_sensor_value.side_effect = _sensor
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "shaving_day"

    def test_before_decision_hour_defaults_car_day(self, manager) -> None:
        # Even a full car: before the decision hour nothing is committed yet.
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_BEFORE_DECISION)
        assert manager._shaving_day_mode == "car_day"
        assert manager._shaving_decision_date is None

    def test_latch_holds_when_car_fills_later_same_day(self, manager) -> None:
        # At the decision hour the car still needs energy → car day …
        _wire(manager, car_soc=50.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        # … later the car reaches its target, but the latch does NOT re-flip.
        _wire(manager, car_soc=80.0, target=80.0)
        later = datetime(2026, 6, 7, 15, 0, tzinfo=UTC)  # 17:00 local
        manager._update_shaving_day_mode(later)
        assert manager._shaving_day_mode == "car_day"

    def test_decision_recomputed_next_day(self, manager) -> None:
        _wire(manager, car_soc=50.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "car_day"
        # Next day the car is full at the decision hour → shaving day.
        _wire(manager, car_soc=80.0, target=80.0)
        next_day = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
        manager._update_shaving_day_mode(next_day)
        assert manager._shaving_day_mode == "shaving_day"

    # --- Departure trigger (FSD 4.2.3): a shaving day downgrades to a car day
    # the moment the EV drops below target ("no longer full"); one-way. ---

    _LATER = datetime(2026, 6, 7, 13, 0, tzinfo=UTC)  # 15:00 local, same day
    _LATER2 = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)  # 18:00 local, same day

    def test_departure_below_target_flips_to_car_day(self, manager) -> None:
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        assert manager._shaving_day_mode == "shaving_day"
        # Car drove off and drained (last-known SOC now below target) → car day.
        _wire(manager, car_soc=55.0, target=80.0)
        manager._update_shaving_day_mode(self._LATER)
        assert manager._shaving_day_mode == "car_day"
        assert manager._charge_gate_active() is False

    def test_departure_is_one_way(self, manager) -> None:
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        _wire(manager, car_soc=55.0, target=80.0)
        manager._update_shaving_day_mode(self._LATER)
        assert manager._shaving_day_mode == "car_day"
        # Car recharges to full later → does NOT re-arm shaving.
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(self._LATER2)
        assert manager._shaving_day_mode == "car_day"

    def test_shaving_day_holds_while_car_stays_full(self, manager) -> None:
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        manager._update_shaving_day_mode(self._LATER)
        assert manager._shaving_day_mode == "shaving_day"

    def test_stale_car_soc_is_not_a_departure(self, manager) -> None:
        # Full at decision → shaving day.
        _wire(manager, car_soc=80.0, target=80.0)
        manager._update_shaving_day_mode(_AT_DECISION)
        # Smart integration goes stale: SOC unknown. Held — not a departure.
        _wire(manager, car_soc=None, target=80.0)
        manager._update_shaving_day_mode(self._LATER)
        assert manager._shaving_day_mode == "shaving_day"  # stale ≠ departed


def _forecast(now_utc: datetime, nets: list[float]) -> pd.DataFrame:
    """Build a 15-min net_energy_wh forecast starting at now_utc (UTC)."""
    idx = pd.date_range(start=now_utc, periods=len(nets), freq="15min", tz="UTC")
    return pd.DataFrame({"net_energy_wh": nets}, index=idx)


class TestMarginalDayGate:
    """B0 marginal-day gate (FSD 4.2.3): only shave when the battery is
    forecast to reach full today under the conservative p10-PV forecast.

    The gate just runs a greedy sim over whatever forecast it is handed; the
    p10-vs-p50 conservatism is applied at the fetch layer (run loop), so these
    tests pass the already-conservative forecast directly.
    """

    def _real_optimizer(self, manager) -> None:
        manager.optimizer = BatteryOptimizer(capacity_wh=10000, max_charge_w=5000)

    def test_abundant_day_fills_today(self, manager) -> None:
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [1500.0] * 64)  # strong surplus → fills today
        assert manager._will_fill_today(50.0, fc, now) is True

    def test_marginal_day_never_fills(self, manager) -> None:
        self._real_optimizer(manager)
        # 16:00 UTC = 18:00 Swiss; trickle surplus, short window → never full.
        now = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)
        fc = _forecast(now, [50.0] * 24)
        assert manager._will_fill_today(50.0, fc, now) is False

    def test_empty_gate_forecast_is_marginal(self, manager) -> None:
        """No conservative forecast → treat as marginal (never defer blindly)."""
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        assert manager._will_fill_today(50.0, pd.DataFrame(), now) is False

    def test_marginal_day_routes_to_greedy_charge(self, manager) -> None:
        """End-to-end: no car + marginal day → charge greedily at full power."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)  # full → shaving day
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)
        fc = _forecast(now, [50.0] * 24)  # marginal → never fills today

        manager.control_battery_charge(50.0, fc, fc, now)

        assert manager._charge_use_case == "B"
        assert manager._charge_action == "charging"
        assert "marginal" in manager._charge_reason
        # Released to full max_charge_w (not the gentle shaving power).
        manager.ha_client.set_number.assert_called_once_with(
            manager.charge_control_entity, manager.charge_max_w, max_retries=5
        )

    def test_abundant_day_routes_to_shaving(self, manager) -> None:
        """End-to-end: no car + abundant day → gate passes to the water-fill
        (not the greedy 'marginal' path)."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)  # full → shaving day
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        # Low surplus now, big peak later → fills today, water-fill defers now.
        fc = _forecast(now, [200.0] + [3000.0] * 40 + [200.0] * 23)

        manager.control_battery_charge(50.0, fc, fc, now)

        assert manager._charge_use_case == "B"
        assert "marginal" not in manager._charge_reason  # gate let it through

    def test_fills_but_below_margin_is_marginal(self, manager) -> None:
        """Fills today but surplus only just exceeds headroom (< ×1.2 margin)
        → treated as marginal (no real peak to shave)."""
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        # SOC 50% → headroom 5000 Wh. ~5200 Wh surplus fills it (×0.95 eff →
        # ~99.4%) but is under the 1.2 margin (6000 Wh) → marginal.
        fc = _forecast(now, [1300.0] * 4 + [0.0] * 12)
        assert manager._will_fill_today(50.0, fc, now) is False

    def test_fills_with_margin_is_abundant(self, manager) -> None:
        self._real_optimizer(manager)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [1300.0] * 8 + [0.0] * 8)  # ~10.4 kWh >> 6 kWh margin
        assert manager._will_fill_today(50.0, fc, now) is True

    def test_charge_target_hold_defers(self, manager) -> None:
        """Topic 3 (FSD 4.2.4): at/above the charge target → hold (limit 0)."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=50.0, target=80.0)  # car day
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = True
        manager._battery_target_soc = 50.0
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)

        manager.control_battery_charge(60.0, fc, fc, now)

        assert manager._charge_action == "deferred"
        assert "charge target" in manager._charge_reason
        # Ceiling mirrors the target (clamped to the register's 90% min); the
        # software power limit holds at 0 behind it.
        manager.ha_client.set_number.assert_any_call(
            manager.end_of_charge_soc_entity, 90.0, max_retries=5
        )
        manager.ha_client.set_number.assert_any_call(
            manager.charge_control_entity, 0, max_retries=5
        )

    def test_below_charge_target_does_not_hold(self, manager) -> None:
        """SOC below the charge target → normal logic runs (not a hold)."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)  # shaving day
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = True
        manager._battery_target_soc = 90.0
        now = datetime(2026, 6, 7, 16, 0, tzinfo=UTC)
        fc = _forecast(now, [50.0] * 24)  # marginal → greedy, not a hold

        manager.control_battery_charge(50.0, fc, fc, now)

        assert "charge target" not in manager._charge_reason

    def test_stale_forecast_routes_to_greedy(self, manager) -> None:
        """Fail-safe: a stale PV forecast → greedy charging, never shave."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)  # shaving day
        manager.ha_client.set_number.return_value = (True, None)
        manager._forecast_fresh = False
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)  # abundant, but forecast not trusted

        manager.control_battery_charge(50.0, fc, fc, now)

        assert manager._charge_action == "charging"
        assert "stale" in manager._charge_reason
        manager.ha_client.set_number.assert_called_once_with(
            manager.charge_control_entity, manager.charge_max_w, max_retries=5
        )


class TestReserveFloor:
    """BR reserve floor (FSD 4.2.3): on a shaving day the water-fill may not
    defer the battery below `charge_shaving_reserve_soc`. Shaving bets the
    morning surplus on a midday peak that may not arrive; the floor is what
    that bet may not risk. Runs after B0, before the water-fill.
    """

    def _real_optimizer(self, manager) -> None:
        manager.optimizer = BatteryOptimizer(capacity_wh=10000, max_charge_w=5000)

    def _abundant_morning(self, now: datetime) -> pd.DataFrame:
        """Low surplus now, big peak later → abundant day the water-fill defers."""
        return _forecast(now, [200.0] + [3000.0] * 40 + [200.0] * 23)

    def test_below_floor_charges_instead_of_deferring(self, manager) -> None:
        """The live 2026-08-24 case: 1% SOC on an abundant shaving morning.
        Without the floor the water-fill defers (surplus 200 Wh << L=3000 Wh)."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)  # full → shaving day
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)

        fc = self._abundant_morning(now)

        manager.control_battery_charge(1.0, fc, fc, now)

        assert manager._charge_use_case == "B"
        assert manager._charge_action == "charging"
        assert "reserve floor" in manager._charge_reason
        # Banked at the gentle shaving power, not the greedy max.
        manager.ha_client.set_number.assert_any_call(
            manager.charge_control_entity, manager.charge_shaving_power_w, max_retries=5
        )
        assert manager._last_charge_power_w == manager.charge_shaving_power_w

    def test_at_floor_hands_back_to_the_water_fill(self, manager) -> None:
        """At/above the floor the deferral resumes — the rest of the headroom
        is still held for the peak."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)

        fc = self._abundant_morning(now)

        manager.control_battery_charge(manager.charge_shaving_reserve_soc, fc, fc, now)

        assert manager._charge_action == "deferred"
        assert "reserve floor" not in manager._charge_reason
        assert manager._last_charge_power_w == 0

    def test_floor_of_zero_disables_it(self, manager) -> None:
        """Floor 0 → pure water-fill, even on an empty battery."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_shaving_reserve_soc = 0.0
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)

        fc = self._abundant_morning(now)

        manager.control_battery_charge(1.0, fc, fc, now)

        assert manager._charge_action == "deferred"
        assert "reserve floor" not in manager._charge_reason

    def test_charge_target_hold_still_wins(self, manager) -> None:
        """Ordering: the Topic 3 hold (4.2.4) is checked first, so a target
        below the floor stops charging rather than the floor overriding it."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=80.0, target=80.0)
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = True
        manager._battery_target_soc = 10.0  # below the 20% floor
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)

        fc = self._abundant_morning(now)

        manager.control_battery_charge(15.0, fc, fc, now)

        assert manager._charge_action == "deferred"
        assert "charge target" in manager._charge_reason

    def test_car_day_unaffected_by_the_floor(self, manager) -> None:
        """Use case A already charges greedily — the floor never downgrades it
        to the gentler shaving power."""
        self._real_optimizer(manager)
        _wire(manager, car_soc=50.0, target=80.0)  # needs energy → car day
        manager.ha_client.set_number.return_value = (True, None)
        now = datetime(2026, 6, 7, 6, 0, tzinfo=UTC)

        fc = self._abundant_morning(now)

        manager.control_battery_charge(1.0, fc, fc, now)

        assert manager._charge_use_case == "A"
        manager.ha_client.set_number.assert_called_once_with(
            manager.charge_control_entity, manager.charge_max_w, max_retries=5
        )


class TestSocCeiling:
    """Topic 3 longevity cap enforced via the inverter's native end-of-charge
    SOC register (FSD 4.2.4). Mirroring the charge target onto it makes the
    inverter hard-stop charging at the target, so the battery can't overshoot
    on the 15-min power-limit lag or trickle up from PV surplus."""

    def test_ceiling_clamped_and_written_on_change(self, manager) -> None:
        manager.ha_client.set_number.return_value = (True, None)
        manager._apply_soc_ceiling(90.0)
        manager.ha_client.set_number.assert_called_once_with(
            manager.end_of_charge_soc_entity, 90.0, max_retries=5
        )
        assert manager._last_soc_ceiling == 90.0

    def test_ceiling_write_skipped_when_unchanged(self, manager) -> None:
        manager.ha_client.set_number.return_value = (True, None)
        manager._apply_soc_ceiling(90.0)
        manager.ha_client.set_number.reset_mock()
        manager._apply_soc_ceiling(90.0)
        manager.ha_client.set_number.assert_not_called()

    def test_ceiling_clamped_to_register_min(self, manager) -> None:
        # The register accepts only 90-100%; a (hypothetical) sub-90 target is
        # clamped up to 90 so the write is always valid.
        manager.ha_client.set_number.return_value = (True, None)
        manager._apply_soc_ceiling(80.0)
        manager.ha_client.set_number.assert_called_once_with(
            manager.end_of_charge_soc_entity, 90.0, max_retries=5
        )

    def test_enabled_mirrors_target_onto_register(self, manager) -> None:
        _wire(manager, car_soc=50.0, target=80.0)  # car day
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = True
        manager._battery_target_soc = 90.0
        now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)

        manager.control_battery_charge(50.0, fc, fc, now)

        manager.ha_client.set_number.assert_any_call(
            manager.end_of_charge_soc_entity, 90.0, max_retries=5
        )
        assert manager._last_soc_ceiling == 90.0

    def test_disabled_leaves_register_untouched(self, manager) -> None:
        # Feature off and EM never lowered the register → don't clobber it.
        _wire(manager, car_soc=50.0, target=80.0)
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = False
        now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)

        manager.control_battery_charge(50.0, fc, fc, now)

        for call in manager.ha_client.set_number.call_args_list:
            assert call.args[0] != manager.end_of_charge_soc_entity

    def test_disabled_releases_ceiling_once_if_previously_set(self, manager) -> None:
        # Feature turned off after EM had capped → release to 100% once.
        _wire(manager, car_soc=50.0, target=80.0)
        manager.ha_client.set_number.return_value = (True, None)
        manager.charge_target_enabled = False
        manager._last_soc_ceiling = 90.0
        now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
        fc = _forecast(now, [3000.0] * 40)

        manager.control_battery_charge(50.0, fc, fc, now)

        manager.ha_client.set_number.assert_any_call(
            manager.end_of_charge_soc_entity, 100.0, max_retries=5
        )
        assert manager._last_soc_ceiling == 100.0
