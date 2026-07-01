"""Tests for simplified battery discharge optimization algorithm (FSD v2.6).

Test cases:
1. Expensive tariff → ALLOW (always)
2. Cheap tariff + SOC stays above min → ALLOW
3. Cheap tariff + SOC would drop below min → BLOCK
4. Edge cases (no forecast, weekend, etc.)
"""

import pytest
import pandas as pd
from datetime import datetime, UTC
from zoneinfo import ZoneInfo

from src.battery_optimizer import (
    BatteryOptimizer,
    DischargeDecision,
    should_charge_now,
)

SWISS_TZ = ZoneInfo("Europe/Zurich")


def make_forecast(
    start: datetime,
    hours: int,
    pv_pattern: list[float],
    load_pattern: list[float],
) -> pd.DataFrame:
    """Create a forecast DataFrame for testing.

    Args:
        start: Start time (UTC)
        hours: Number of hours to generate
        pv_pattern: PV power pattern in W (repeated to fill hours)
        load_pattern: Load power pattern in W (repeated to fill hours)

    Returns:
        DataFrame with pv_energy_wh, load_energy_wh, net_energy_wh at 15-min intervals

    """
    periods = hours * 4  # 15-min intervals
    times = pd.date_range(start=start, periods=periods, freq="15min", tz=UTC)

    # Extend patterns to fill all periods
    pv_extended = (pv_pattern * (periods // len(pv_pattern) + 1))[:periods]
    load_extended = (load_pattern * (periods // len(load_pattern) + 1))[:periods]

    # Convert power (W) to energy per 15-min period (Wh)
    pv_wh = [p * 0.25 for p in pv_extended]
    load_wh = [val * 0.25 for val in load_extended]
    net_wh = [p - ld for p, ld in zip(pv_wh, load_wh, strict=False)]

    return pd.DataFrame(
        {
            "pv_energy_wh": pv_wh,
            "load_energy_wh": load_wh,
            "net_energy_wh": net_wh,
        },
        index=times,
    )


class TestNoForecast:
    def test_no_forecast_data_allows_discharge(self) -> None:
        opt = BatteryOptimizer()
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        decision, on, off, planned = opt.calculate_decision(
            soc_percent=50, forecast=pd.DataFrame(), now=now
        )
        assert decision.discharge_allowed is True
        assert "No forecast data" in decision.reason
        assert on.empty and off.empty and planned.empty


class TestExpensiveImportComparison:
    """FSD 4.2.2 Topic 4: lower expensive-hours import wins; tie -> battery_on."""

    def test_abundant_pv_free_discharge_wins(self) -> None:
        """Strong PV -> no expensive import either way -> tie -> free discharge."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        pv = [0] * 32 + [5000] * 60 + [0] * 4
        load = [400] * 96
        fc = make_forecast(now, 48, pv, load)
        decision, _, _, _ = opt.calculate_decision(soc_percent=80, forecast=fc, now=now)
        assert decision.discharge_allowed is True
        assert decision.expensive_import_wh == 0.0
        assert "free-discharge" in decision.reason

    def test_poor_pv_night_holds_discharge(self) -> None:
        """Cheap night + poor PV: free discharge empties the battery before the
        expensive morning, so battery_off buys less expensive energy -> hold now."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        pv = [0] * 36 + [300] * 40 + [0] * 20
        load = [1200] * 96
        fc = make_forecast(now, 48, pv, load)
        decision, _, _, _ = opt.calculate_decision(soc_percent=40, forecast=fc, now=now)
        assert decision.discharge_allowed is False
        assert "Hold" in decision.reason

    def test_expensive_slot_always_allows(self) -> None:
        """In an expensive slot, discharge is allowed regardless of the strategy."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 26, 11, 0, tzinfo=SWISS_TZ).astimezone(UTC)  # expensive
        fc = make_forecast(now, 48, [0], [1500])
        decision, _, _, _ = opt.calculate_decision(soc_percent=60, forecast=fc, now=now)
        assert decision.discharge_allowed is True

    def test_unavoidable_expensive_import_is_positive(self) -> None:
        """No PV + low SOC -> the battery cannot cover all expensive hours -> > 0."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        fc = make_forecast(now, 48, [0], [1000])
        decision, _, _, _ = opt.calculate_decision(soc_percent=10, forecast=fc, now=now)
        assert decision.expensive_import_wh > 0


class TestScenarioCurves:
    """calculate_decision returns battery_on, battery_off, and the planned path."""

    def test_three_distinct_curves_planned_matches_choice(self) -> None:
        """Hold wins -> planned == battery_off; battery_off preserves more SOC."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        pv = [0] * 36 + [300] * 40 + [0] * 20
        load = [1200] * 96
        fc = make_forecast(now, 48, pv, load)
        decision, on, off, planned = opt.calculate_decision(
            soc_percent=40, forecast=fc, now=now
        )
        assert not on.empty and not off.empty and not planned.empty
        # Hold wins this scenario, so the planned path is battery_off.
        assert decision.discharge_allowed is False
        assert planned["soc_percent"].equals(off["soc_percent"])
        # Holding discharge during cheap hours never ends below free discharge.
        assert off["soc_percent"].iloc[-1] >= on["soc_percent"].iloc[-1]

    def test_planned_is_one_of_the_two_options(self) -> None:
        """planned always equals exactly one of the two candidate sims."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        fc = make_forecast(now, 48, [0] * 32 + [5000] * 60 + [0] * 4, [400] * 96)
        _, on, off, planned = opt.calculate_decision(soc_percent=80, forecast=fc, now=now)
        matches_on = planned["soc_percent"].equals(on["soc_percent"])
        matches_off = planned["soc_percent"].equals(off["soc_percent"])
        assert matches_on or matches_off


class TestReserveMargin:
    """battery.reserve_percent is an optional safety margin (FSD 4.2.2)."""

    def test_reserve_does_not_lower_import(self) -> None:
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)
        pv = [0] * 36 + [800] * 40 + [0] * 20
        load = [1000] * 96
        fc = make_forecast(now, 48, pv, load)
        d0, _, _, _ = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0).calculate_decision(
            soc_percent=40, forecast=fc, now=now
        )
        d20, _, _, _ = BatteryOptimizer(capacity_wh=10000, min_soc_percent=20).calculate_decision(
            soc_percent=40, forecast=fc, now=now
        )
        # A higher floor stops the battery earlier -> at least as much expensive import.
        assert d20.expensive_import_wh >= d0.expensive_import_wh

    def test_floor_10_holds_overnight_when_floor_0_discharges(self) -> None:
        """The production 10% floor (FSD 4.2.2): free-discharge can use its bottom
        slice to cover a small morning expensive deficit at floor 0 (tie -> allow),
        but not at floor 10 -> hold wins, keeping a buffer for the morning."""
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)  # Mon, cheap
        pv, load = [], []
        for i in range(192):  # 48 h of 15-min slots from 22:00
            h = (22 + i * 0.25) % 24
            if 6 <= h < 9:      # early morning: weak PV during the expensive window
                pv.append(150.0)
            elif 9 <= h < 16:   # midday: strong PV recovers the battery
                pv.append(1800.0)
            elif 16 <= h < 18:
                pv.append(400.0)
            else:               # night
                pv.append(0.0)
            load.append(400.0)
        fc = make_forecast(now, 48, pv, load)
        d0, _, _, _ = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0).calculate_decision(
            soc_percent=42, forecast=fc, now=now
        )
        d10, _, _, _ = BatteryOptimizer(capacity_wh=10000, min_soc_percent=10).calculate_decision(
            soc_percent=42, forecast=fc, now=now
        )
        assert d0.discharge_allowed is True       # bottom slice covers the morning
        assert d10.discharge_allowed is False     # holds to keep the morning buffer


class TestWeekend:
    def test_weekend_is_cheap_now(self) -> None:
        opt = BatteryOptimizer(weekend_all_day_cheap=True)
        now = datetime(2026, 1, 31, 12, 0, tzinfo=SWISS_TZ).astimezone(UTC)  # Saturday
        assert opt.get_tariff_periods(now).is_cheap_now is True

    def test_weekend_no_expensive_import_allows_discharge(self) -> None:
        """Fri night -> the 48 h horizon (to Sun night) has no expensive slots."""
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        now = datetime(2026, 1, 30, 23, 0, tzinfo=SWISS_TZ).astimezone(UTC)  # Friday
        fc = make_forecast(now, 48, [0], [500])
        decision, _, _, _ = opt.calculate_decision(soc_percent=10, forecast=fc, now=now)
        assert decision.discharge_allowed is True
        assert decision.expensive_import_wh == 0.0


class TestTariffBoundaryTransitions:
    """Tariff boundaries at 21:00 and 06:00 Swiss time (weekdays)."""

    def test_2059_is_expensive(self) -> None:
        opt = BatteryOptimizer()
        now = datetime(2026, 1, 26, 20, 59, tzinfo=SWISS_TZ).astimezone(UTC)
        assert opt.get_tariff_periods(now).is_cheap_now is False

    def test_2101_is_cheap(self) -> None:
        opt = BatteryOptimizer()
        now = datetime(2026, 1, 26, 21, 1, tzinfo=SWISS_TZ).astimezone(UTC)
        assert opt.get_tariff_periods(now).is_cheap_now is True

    def test_0559_is_cheap(self) -> None:
        opt = BatteryOptimizer()
        now = datetime(2026, 1, 27, 5, 59, tzinfo=SWISS_TZ).astimezone(UTC)
        assert opt.get_tariff_periods(now).is_cheap_now is True

    def test_0601_is_expensive(self) -> None:
        opt = BatteryOptimizer()
        now = datetime(2026, 1, 27, 6, 1, tzinfo=SWISS_TZ).astimezone(UTC)
        assert opt.get_tariff_periods(now).is_cheap_now is False


class TestEblHolidays:
    """The 8 EBL low-tariff holidays are computed in-add-on (FSD 4.2.2)."""

    def test_neujahr_is_cheap(self) -> None:
        opt = BatteryOptimizer()
        d = datetime(2026, 1, 1, 12, 0, tzinfo=SWISS_TZ)
        assert opt.is_holiday(d) is True
        assert opt.is_cheap_day(d) is True

    def test_karfreitag_2026_is_cheap(self) -> None:
        # Easter 2026 = 5 Apr -> Good Friday = 3 Apr
        opt = BatteryOptimizer()
        assert opt.is_holiday(datetime(2026, 4, 3, 12, 0, tzinfo=SWISS_TZ)) is True

    def test_pfingstmontag_2026_is_cheap(self) -> None:
        # Easter + 50 = 25 May 2026
        opt = BatteryOptimizer()
        assert opt.is_holiday(datetime(2026, 5, 25, 12, 0, tzinfo=SWISS_TZ)) is True

    def test_berchtoldstag_is_not_cheap(self) -> None:
        # 2 Jan is NOT an EBL low-tariff holiday
        opt = BatteryOptimizer()
        assert opt.is_holiday(datetime(2026, 1, 2, 12, 0, tzinfo=SWISS_TZ)) is False

    def test_labour_day_is_not_cheap(self) -> None:
        opt = BatteryOptimizer()
        assert opt.is_holiday(datetime(2026, 5, 1, 12, 0, tzinfo=SWISS_TZ)) is False

    def test_config_holidays_are_ignored(self) -> None:
        # The `holidays` arg is accepted for compat but unused.
        opt = BatteryOptimizer(holidays=["2026-03-15"])
        assert opt.is_holiday(datetime(2026, 3, 15, 12, 0, tzinfo=SWISS_TZ)) is False


class TestDecisionDataclass:
    def test_decision_has_fields(self) -> None:
        d = DischargeDecision(
            discharge_allowed=True,
            reason="Test",
            min_soc_percent=50.0,
            expensive_import_wh=123.0,
        )
        assert d.discharge_allowed is True
        assert d.expensive_import_wh == 123.0

    def test_expensive_import_defaults_zero(self) -> None:
        d = DischargeDecision(discharge_allowed=True, reason="x", min_soc_percent=50.0)
        assert d.expensive_import_wh == 0.0


class TestShouldChargeNow:
    """Export-peak-shaving charge decision (FSD 4.2.3 water-fill)."""

    def test_no_surplus_now_releases(self) -> None:
        """No surplus this interval → charge/release (nothing to defer)."""
        assert (
            should_charge_now([0.0, 500.0, 1000.0], headroom_wh=2000, current_surplus_wh=0.0)
            is True
        )

    def test_negative_surplus_now_releases(self) -> None:
        """Net import this interval → release."""
        assert (
            should_charge_now([-100.0, 500.0], headroom_wh=2000, current_surplus_wh=-100.0) is True
        )

    def test_battery_full_releases(self) -> None:
        """Zero headroom → release (no benefit deferring)."""
        assert should_charge_now([800.0, 900.0], headroom_wh=0.0, current_surplus_wh=800.0) is True

    def test_cannot_fill_charges_asap(self) -> None:
        """Total remaining surplus ≤ headroom → charge now (can't overfill)."""
        # total = 1500 < headroom 5000
        assert (
            should_charge_now([500.0, 1000.0], headroom_wh=5000, current_surplus_wh=500.0) is True
        )

    def test_defers_outside_peak_band(self) -> None:
        """Low-surplus interval below water level L → defer."""
        # surplus profile: now=300 (low), peak ahead 1000+1200. headroom=1000.
        # top band to fill 1000: [1200] then [1000] → L=1000. current 300 < L.
        remaining = [300.0, 1000.0, 1200.0]
        assert should_charge_now(remaining, headroom_wh=1000, current_surplus_wh=300.0) is False

    def test_charges_inside_peak_band(self) -> None:
        """High-surplus interval at/above L → charge."""
        # headroom=1000; sorted desc 1200,1000,300; accumulate 1200 ≥1000 → L=1200.
        # current 1200 ≥ L → charge.
        remaining = [1200.0, 1000.0, 300.0]
        assert should_charge_now(remaining, headroom_wh=1000, current_surplus_wh=1200.0) is True

    def test_water_level_picks_top_intervals(self) -> None:
        """Headroom spanning two intervals: both top ticks charge, low defers."""
        remaining = [400.0, 1000.0, 900.0, 200.0]  # current = 400
        # headroom=1900 → sorted 1000,900,400,200; accumulate 1000(→1000),900(→1900)≥1900 → L=900.
        # current 400 < 900 → defer.
        assert should_charge_now(remaining, headroom_wh=1900, current_surplus_wh=400.0) is False
        # the 1000 and 900 intervals (≥L) would charge:
        assert should_charge_now(remaining, headroom_wh=1900, current_surplus_wh=900.0) is True
        assert should_charge_now(remaining, headroom_wh=1900, current_surplus_wh=1000.0) is True

    def test_cap_widens_band_so_lower_interval_charges(self) -> None:
        """A per-interval cap widens the band (gentler feed-in, FSD 4.2.3).

        Same profile/headroom: uncapped the 400 interval defers (L=900); with
        a 625 Wh cap each top interval absorbs less, so more intervals are
        needed and the 400 interval falls inside the band (L=400).
        """
        remaining = [400.0, 1000.0, 900.0, 200.0]
        # Uncapped: sorted 1000,900 fills 1500 → L=900 → 400 defers.
        assert should_charge_now(remaining, headroom_wh=1500, current_surplus_wh=400.0) is False
        # Capped at 625 Wh: 625+625+400 ≥ 1500 → L=400 → 400 charges.
        assert (
            should_charge_now(
                remaining,
                headroom_wh=1500,
                current_surplus_wh=400.0,
                max_charge_per_interval_wh=625.0,
            )
            is True
        )

    def test_cap_lowers_absorbable_total_charges_asap(self) -> None:
        """Cap can make remaining surplus unfillable → charge ASAP."""
        remaining = [400.0, 1000.0, 900.0, 200.0]
        # Capped absorbable = 625+625+400+200 = 1850 < headroom 1900 → release.
        assert (
            should_charge_now(
                remaining,
                headroom_wh=1900,
                current_surplus_wh=400.0,
                max_charge_per_interval_wh=625.0,
            )
            is True
        )

    def test_cap_still_defers_below_band(self) -> None:
        """Even with the wider capped band, intervals under L still defer."""
        remaining = [200.0, 1000.0, 900.0, 400.0]
        # Capped 625, headroom 1500 → L=400; current 200 < 400 → defer.
        assert (
            should_charge_now(
                remaining,
                headroom_wh=1500,
                current_surplus_wh=200.0,
                max_charge_per_interval_wh=625.0,
            )
            is False
        )


class TestReachesTargetToday:
    """reaches_target_today: the 10-s-fresh EV target gate (FSD 4.3.6).

    Re-anchors the SOC sim to the live SOC. Forecast: 4 h of +1000 W net
    (250 Wh/15-min) then dark → total +4000 Wh = +40 % of a 10 kWh battery.
    """

    def _fc(self, now: datetime) -> pd.DataFrame:
        return make_forecast(
            now, hours=8, pv_pattern=[1500] * 16 + [0] * 16, load_pattern=[500]
        )

    def test_reaches_from_high_soc(self) -> None:
        now = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        reaches, peak, ft = opt.reaches_target_today(60.0, self._fc(now), now, 90.0)
        assert reaches is True
        assert peak >= 90
        assert ft is not None

    def test_not_reached_from_low_soc(self) -> None:
        now = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        reaches, peak, ft = opt.reaches_target_today(20.0, self._fc(now), now, 90.0)
        assert reaches is False
        assert peak < 90
        assert ft is None

    def test_reanchor_lower_live_soc_gives_lower_peak(self) -> None:
        """The core property: same forecast, lower live SOC → lower peak.

        This is exactly why the gate must re-anchor: as the car drains the
        battery, the live SOC drops and the reachable peak drops with it.
        """
        now = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        _, peak_hi, _ = opt.reaches_target_today(60.0, self._fc(now), now, 90.0)
        _, peak_lo, _ = opt.reaches_target_today(20.0, self._fc(now), now, 90.0)
        assert peak_hi > peak_lo

    def test_empty_forecast_fails_closed(self) -> None:
        now = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
        opt = BatteryOptimizer(capacity_wh=10000, min_soc_percent=0)
        reaches, peak, ft = opt.reaches_target_today(50.0, pd.DataFrame(), now, 90.0)
        assert reaches is False
        assert peak is None
        assert ft is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
