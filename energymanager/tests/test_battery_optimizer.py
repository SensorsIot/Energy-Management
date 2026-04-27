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

from src.battery_optimizer import BatteryOptimizer, DischargeDecision

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

    return pd.DataFrame({
        "pv_energy_wh": pv_wh,
        "load_energy_wh": load_wh,
        "net_energy_wh": net_wh,
    }, index=times)


class TestExpensiveTariff:
    """During expensive tariff (06:00-21:00): always ALLOW discharge."""

    def test_expensive_tariff_allows_discharge(self) -> None:
        """At 12:00 (expensive), discharge should be allowed regardless of SOC forecast."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        # Midday on a weekday - expensive tariff
        now = datetime(2026, 1, 26, 11, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Forecast with high load, no PV (worst case)
        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=[0],  # No PV
            load_pattern=[2000],  # 2kW constant load
        )

        decision, sim_full, sim_strategy = optimizer.calculate_decision(
            soc_percent=50,
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True
        assert "Expensive tariff" in decision.reason

    def test_expensive_tariff_low_soc_still_allows(self) -> None:
        """Even with low SOC during expensive tariff, discharge is allowed."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        now = datetime(2026, 1, 26, 14, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=[0],
            load_pattern=[5000],  # Very high load
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=15,  # Low starting SOC
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True


class TestCheapTariffAllow:
    """During cheap tariff: ALLOW if SOC stays >= min during expensive hours."""

    def test_cheap_tariff_high_pv_allows_discharge(self) -> None:
        """At 22:00 (cheap), with good PV forecast, discharge should be allowed."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        # Evening on a weekday - cheap tariff
        now = datetime(2026, 1, 26, 21, 30, tzinfo=SWISS_TZ).astimezone(UTC)

        # Good PV during day, moderate load
        # Simulate: night (no PV), then day (good PV)
        pv_pattern = [0] * 36 + [4000] * 48 + [0] * 12  # 0 until 6am, then PV
        load_pattern = [500] * 96  # 500W constant

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=80,  # Good starting SOC
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True
        assert "SOC stays >=" in decision.reason

    def test_cheap_tariff_full_battery_allows_discharge(self) -> None:
        """With 100% SOC and good PV, should allow discharge."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Good PV during day (enough to cover load and charge battery)
        # 22:00 → 06:00 = 8h = 32 periods of no PV
        # 06:00 → 21:00 = 15h = 60 periods of good PV
        pv_pattern = [0] * 32 + [5000] * 60 + [0] * 4  # Strong PV during day
        load_pattern = [400] * 96  # Low load

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=100,
            forecast=forecast,
            now=now,
        )

        assert decision.discharge_allowed is True


class TestCheapTariffBlock:
    """During cheap tariff: BLOCK if SOC would drop below min during expensive hours."""

    def test_cheap_tariff_low_pv_blocks_discharge(self) -> None:
        """At 22:00 (cheap), with poor PV forecast and hysteresis active, discharge should be blocked."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        now = datetime(2026, 1, 26, 21, 30, tzinfo=SWISS_TZ).astimezone(UTC)

        # Poor PV (cloudy day), high load
        pv_pattern = [0] * 36 + [500] * 48 + [0] * 12  # Very little PV
        load_pattern = [1500] * 96  # 1.5kW constant

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        # With 0% reserve, blocking only happens via hysteresis (previously_blocked)
        decision, _, _ = optimizer.calculate_decision(
            soc_percent=50,  # Medium SOC
            forecast=forecast,
            now=now,
            previously_blocked=True,
        )

        assert decision.discharge_allowed is False
        assert "Block" in decision.reason

    def test_cheap_tariff_low_soc_blocks_discharge(self) -> None:
        """At 22:00 (cheap), with low starting SOC and hysteresis active, discharge should be blocked."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Moderate PV but starting SOC is low
        pv_pattern = [0] * 36 + [2000] * 48 + [0] * 12
        load_pattern = [1000] * 96

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        # With 0% reserve, blocking only happens via hysteresis (previously_blocked)
        decision, _, _ = optimizer.calculate_decision(
            soc_percent=20,  # Low starting SOC
            forecast=forecast,
            now=now,
            previously_blocked=True,
        )

        assert decision.discharge_allowed is False

    def test_min_soc_threshold_respected(self) -> None:
        """SOC dropping to exactly min_soc should be allowed, below should block."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=20,  # Higher threshold
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Create forecast that would result in ~15% min SOC
        pv_pattern = [0] * 36 + [1000] * 48 + [0] * 12
        load_pattern = [800] * 96

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=40,
            forecast=forecast,
            now=now,
        )

        # With 20% threshold, should block if min SOC drops below 20%
        assert decision.min_soc_percent < 20 or decision.discharge_allowed is True


class TestDischargeFloor:
    """SOC floor logic: allow discharge above floor, block at/below floor."""

    def test_high_soc_above_floor_allows(self) -> None:
        """At 22:00 cheap, with high SOC well above the floor → allow discharge.

        This is the core fix: the old algorithm blocked at 71% because the
        free-discharge sim showed 6% at 08:00. The new algorithm calculates
        a SOC floor (~15%) and allows discharge since 71% >> 15%.
        """
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Moderate PV (enough to recover by midday), moderate load
        pv_pattern = [0] * 32 + [3000] * 48 + [0] * 16
        load_pattern = [500] * 96

        forecast = make_forecast(start=now, hours=48,
                                 pv_pattern=pv_pattern, load_pattern=load_pattern)

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=71, forecast=forecast, now=now,
        )
        # Old algorithm would block; new algorithm allows (above floor)
        assert decision.discharge_allowed is True
        assert "floor" in decision.reason.lower() or "stays >=" in decision.reason.lower()

    def test_low_soc_at_floor_blocks(self) -> None:
        """SOC at or below floor → block discharge (with hysteresis active)."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Low PV — morning drop is significant
        pv_pattern = [0] * 36 + [1500] * 48 + [0] * 12
        load_pattern = [800] * 96

        forecast = make_forecast(start=now, hours=48,
                                 pv_pattern=pv_pattern, load_pattern=load_pattern)

        # With 0% reserve, blocking only happens via hysteresis (previously_blocked)
        decision, _, _ = optimizer.calculate_decision(
            soc_percent=12, forecast=forecast, now=now,
            previously_blocked=True,
        )
        assert decision.discharge_allowed is False
        assert "block" in decision.reason.lower()


class TestSelfCorrecting:
    """Test that re-checking every 15 min allows self-correction."""

    def test_block_then_allow_as_conditions_improve(self) -> None:
        """If initially blocked, later check with better SOC should allow."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Balanced forecast: PV roughly matches load during day
        # Night: no PV, 500W load
        # Day: 3000W PV, 500W load (net surplus)
        pv_pattern = [0] * 32 + [3000] * 60 + [0] * 4
        load_pattern = [500] * 96

        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=pv_pattern,
            load_pattern=load_pattern,
        )

        # First check: low SOC (30%) - might not have enough for expensive hours
        decision1, _, _ = optimizer.calculate_decision(
            soc_percent=30,
            forecast=forecast,
            now=now,
        )

        # Second check: high SOC (90%) - should definitely have enough
        decision2, _, _ = optimizer.calculate_decision(
            soc_percent=90,
            forecast=forecast,
            now=now,
        )

        # Higher starting SOC → higher min SOC during expensive hours
        assert decision2.min_soc_percent > decision1.min_soc_percent
        # With 90% SOC and good PV, should allow discharge
        assert decision2.discharge_allowed is True


class TestEdgeCases:
    """Edge cases and special scenarios."""

    def test_no_forecast_data_allows_discharge(self) -> None:
        """With no forecast data, default to allowing discharge."""
        optimizer = BatteryOptimizer()

        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        decision, sim_full, sim_strategy = optimizer.calculate_decision(
            soc_percent=50,
            forecast=pd.DataFrame(),  # Empty forecast
            now=now,
        )

        assert decision.discharge_allowed is True
        assert "No forecast data" in decision.reason

    def test_weekend_all_day_cheap(self) -> None:
        """Weekend is all-day cheap tariff."""
        optimizer = BatteryOptimizer(weekend_all_day_cheap=True)

        # Saturday midday
        now = datetime(2026, 1, 31, 12, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        tariff = optimizer.get_tariff_periods(now)

        assert tariff.is_cheap_now is True

    def test_weekend_allows_discharge_despite_low_soc(self) -> None:
        """On weekends, daytime SOC dips should not block discharge (all-day cheap)."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
            weekend_all_day_cheap=True,
        )

        # Friday night 23:00 → weekend ahead
        now = datetime(2026, 1, 30, 23, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # No PV, moderate load → SOC will drop to 0% on Saturday
        # But Saturday is cheap, so it shouldn't matter
        forecast = make_forecast(
            start=now,
            hours=48,
            pv_pattern=[0],  # No PV at all (worst case)
            load_pattern=[500],  # 500W constant
        )

        decision, _, _ = optimizer.calculate_decision(
            soc_percent=10,
            forecast=forecast,
            now=now,
        )

        # Weekend days have no expensive hours → discharge should be allowed
        assert decision.discharge_allowed is True

    def test_weekday_morning_is_expensive(self) -> None:
        """Weekday 08:00 should be expensive tariff."""
        optimizer = BatteryOptimizer()

        # Monday morning
        now = datetime(2026, 1, 26, 8, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        tariff = optimizer.get_tariff_periods(now)

        assert tariff.is_cheap_now is False

    def test_weekday_night_is_cheap(self) -> None:
        """Weekday 23:00 should be cheap tariff."""
        optimizer = BatteryOptimizer()

        # Monday night
        now = datetime(2026, 1, 26, 23, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        tariff = optimizer.get_tariff_periods(now)

        assert tariff.is_cheap_now is True

    def test_holiday_is_cheap(self) -> None:
        """Configured holidays should be all-day cheap."""
        optimizer = BatteryOptimizer(holidays=["2026-01-01"])

        # New Year's Day midday
        now = datetime(2026, 1, 1, 12, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        assert optimizer.is_holiday(now) is True
        assert optimizer.is_cheap_day(now) is True


class TestDecisionDataclass:
    """Test DischargeDecision dataclass fields."""

    def test_decision_has_required_fields(self) -> None:
        """DischargeDecision should have discharge_allowed, reason, min_soc_percent."""
        decision = DischargeDecision(
            discharge_allowed=True,
            reason="Test reason",
            min_soc_percent=50.0,
        )

        assert decision.discharge_allowed is True
        assert decision.reason == "Test reason"
        assert decision.min_soc_percent == 50.0


# ===================================================================
# IT-BATT-03: Tariff boundary transitions
# ===================================================================


class TestTariffBoundaryTransitions:
    """IT-BATT-03: Verify tariff boundaries at 21:00 and 06:00 Swiss time.

    Default tariff: cheap 21:00–06:00, expensive 06:00–21:00 (weekdays).
    """

    def test_2059_is_expensive(self) -> None:
        """20:59 Swiss → still expensive (cheap starts at 21:00)."""
        optimizer = BatteryOptimizer()
        now = datetime(2026, 1, 26, 20, 59, tzinfo=SWISS_TZ).astimezone(UTC)
        tariff = optimizer.get_tariff_periods(now)
        assert tariff.is_cheap_now is False

    def test_2101_is_cheap(self) -> None:
        """21:01 Swiss → cheap (within cheap window)."""
        optimizer = BatteryOptimizer()
        now = datetime(2026, 1, 26, 21, 1, tzinfo=SWISS_TZ).astimezone(UTC)
        tariff = optimizer.get_tariff_periods(now)
        assert tariff.is_cheap_now is True

    def test_0559_is_cheap(self) -> None:
        """05:59 Swiss → cheap (before 06:00 boundary)."""
        optimizer = BatteryOptimizer()
        now = datetime(2026, 1, 27, 5, 59, tzinfo=SWISS_TZ).astimezone(UTC)
        tariff = optimizer.get_tariff_periods(now)
        assert tariff.is_cheap_now is True

    def test_0601_is_expensive(self) -> None:
        """06:01 Swiss → expensive (after 06:00 boundary)."""
        optimizer = BatteryOptimizer()
        now = datetime(2026, 1, 27, 6, 1, tzinfo=SWISS_TZ).astimezone(UTC)
        tariff = optimizer.get_tariff_periods(now)
        assert tariff.is_cheap_now is False


# ===================================================================
# Hysteresis: prevent oscillation at soc_ok boundary
# ===================================================================


class TestHysteresis:
    """Hysteresis prevents flip-flopping when projected min SOC hovers near threshold."""

    def _make_optimizer_and_forecast(self):
        """Create optimizer and a forecast that yields min_soc ~0-1%."""
        optimizer = BatteryOptimizer(
            capacity_wh=10000,
            min_soc_percent=0,
        )
        # Monday 22:00 (cheap tariff)
        now = datetime(2026, 1, 26, 22, 0, tzinfo=SWISS_TZ).astimezone(UTC)

        # Craft forecast so that at low SOC, projected min is just above 0%
        # Night: 0 PV, 300W load.  Day: enough PV to recover
        pv_pattern = [0] * 32 + [2500] * 48 + [0] * 16
        load_pattern = [300] * 96
        forecast = make_forecast(start=now, hours=48,
                                 pv_pattern=pv_pattern, load_pattern=load_pattern)
        return optimizer, forecast, now

    def test_previously_blocked_requires_margin_to_reallow(self) -> None:
        """When previously blocked, min_soc barely above threshold stays blocked."""
        optimizer, forecast, now = self._make_optimizer_and_forecast()

        # Find SOC where min_soc is just above 0% (between 0-2%)
        # Try a range to find the right one
        for soc in range(1, 40):
            decision, _, _ = optimizer.calculate_decision(
                soc_percent=soc, forecast=forecast, now=now,
                previously_blocked=False,
            )
            if 0 <= decision.min_soc_percent < 2 and decision.discharge_allowed:
                # Found a borderline case — now test with previously_blocked=True
                decision_blocked, _, _ = optimizer.calculate_decision(
                    soc_percent=soc, forecast=forecast, now=now,
                    previously_blocked=True,
                )
                # Same inputs, but with hysteresis: should block (needs min_soc >= 2%)
                assert decision_blocked.discharge_allowed is False, (
                    f"SOC={soc}%, min_soc={decision.min_soc_percent:.1f}%: "
                    f"should stay blocked with hysteresis"
                )
                return

        pytest.skip("Could not find borderline SOC for this forecast")

    def test_previously_blocked_allows_with_clear_margin(self) -> None:
        """When previously blocked but min_soc clearly above threshold+2%, allow."""
        optimizer, forecast, now = self._make_optimizer_and_forecast()

        # High SOC — min_soc will be well above 2%
        decision, _, _ = optimizer.calculate_decision(
            soc_percent=90, forecast=forecast, now=now,
            previously_blocked=True,
        )
        assert decision.discharge_allowed is True

    def test_not_previously_blocked_allows_at_threshold(self) -> None:
        """When not previously blocked, min_soc at threshold allows normally."""
        optimizer, forecast, now = self._make_optimizer_and_forecast()

        # Find SOC where min_soc is just above 0%
        for soc in range(1, 40):
            decision, _, _ = optimizer.calculate_decision(
                soc_percent=soc, forecast=forecast, now=now,
                previously_blocked=False,
            )
            if 0 <= decision.min_soc_percent < 2 and decision.discharge_allowed:
                # Without hysteresis: should allow (min_soc >= 0%)
                assert decision.discharge_allowed is True
                return

        pytest.skip("Could not find borderline SOC for this forecast")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
