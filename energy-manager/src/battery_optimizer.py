"""Battery discharge optimization based on energy balance.

Simplified Algorithm (FSD v2.6):
1. If expensive tariff (06:00-21:00): always allow discharge
2. If cheap tariff: simulate SOC from NOW until end of next expensive period
3. Check if min SOC stays >= min_soc during ALL expensive hours
   - If yes: allow discharge
   - If no: block discharge
4. Re-check every 15 minutes (self-correcting based on actual conditions)
"""

import logging
from datetime import datetime, timedelta, date, UTC
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd
from dateutil.easter import easter

logger = logging.getLogger(__name__)

# Swiss timezone for display
SWISS_TZ = ZoneInfo("Europe/Zurich")


def swiss_time(dt: datetime) -> str:
    """Format datetime as HH:MM in Swiss timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SWISS_TZ).strftime("%H:%M")


@dataclass
class TariffPeriod:
    """Tariff period information."""

    cheap_start: datetime
    cheap_end: datetime
    target: datetime
    is_cheap_now: bool


@dataclass
class DischargeDecision:
    """Battery discharge decision."""

    discharge_allowed: bool
    reason: str
    min_soc_percent: float  # Minimum SOC during expensive hours (for logging)
    expensive_import_wh: float = 0.0  # Expensive-hours grid import, winning strategy (FSD 4.2.2)


class BatteryOptimizer:
    """Optimize battery discharge based on tariff and forecast."""

    def __init__(
        self,
        capacity_wh: float = 10000,
        min_soc_percent: float = 0,
        charge_efficiency: float = 0.95,
        discharge_efficiency: float = 0.95,
        max_charge_w: float = 5000,
        max_discharge_w: float = 5000,
        weekday_cheap_start: str = "21:00",
        weekday_cheap_end: str = "06:00",
        weekend_all_day_cheap: bool = True,
        holidays: list[str] = None,
    ) -> None:
        self.capacity_wh = capacity_wh
        self.min_soc_percent = min_soc_percent
        self.min_soc_wh = capacity_wh * min_soc_percent / 100
        self.charge_efficiency = charge_efficiency
        self.discharge_efficiency = discharge_efficiency
        self.max_charge_wh_per_15min = max_charge_w * 0.25
        self.max_discharge_wh_per_15min = max_discharge_w * 0.25

        # Parse tariff times
        self.cheap_start_hour = int(weekday_cheap_start.split(":")[0])
        self.cheap_start_minute = int(weekday_cheap_start.split(":")[1])
        self.cheap_end_hour = int(weekday_cheap_end.split(":")[0])
        self.cheap_end_minute = int(weekday_cheap_end.split(":")[1])
        self.weekend_all_day_cheap = weekend_all_day_cheap

        # EBL low-tariff holidays are computed in-add-on (FSD 4.2.2); the
        # `holidays` arg is accepted for backward compatibility but unused.
        self._holiday_cache: dict[int, set] = {}

    def _ebl_holidays(self, year: int) -> set:
        """Return the 8 EBL low-tariff holidays for a year (FSD 4.2.2)."""
        cached = self._holiday_cache.get(year)
        if cached is None:
            e = easter(year)
            cached = {
                date(year, 1, 1),  # Neujahr
                date(year, 8, 1),  # 1. August
                date(year, 12, 25),  # Weihnachten
                date(year, 12, 26),  # Stephanstag
                e - timedelta(days=2),  # Karfreitag
                e + timedelta(days=1),  # Ostermontag
                e + timedelta(days=39),  # Auffahrt
                e + timedelta(days=50),  # Pfingstmontag
            }
            self._holiday_cache[year] = cached
        return cached

    def is_holiday(self, dt: datetime) -> bool:
        """Check if date is an EBL low-tariff holiday (computed, FSD 4.2.2)."""
        return dt.date() in self._ebl_holidays(dt.year)

    def is_weekend(self, dt: datetime) -> bool:
        """Check if date is weekend (Saturday=5, Sunday=6)."""
        return dt.weekday() >= 5

    def is_cheap_day(self, dt: datetime) -> bool:
        """Check if entire day is cheap (weekend or holiday)."""
        return (self.weekend_all_day_cheap and self.is_weekend(dt)) or self.is_holiday(dt)

    def _soc_at_time(self, sim: pd.DataFrame, target: datetime) -> float:
        """Extract SOC percentage at a target time from a simulation DataFrame."""
        if sim.empty:
            return 0.0
        valid = sim.index[sim.index <= target]
        if len(valid) == 0:
            return float(sim["soc_percent"].iloc[0])
        return float(sim.loc[valid[-1], "soc_percent"])

    def expensive_mask(self, index: pd.DatetimeIndex) -> pd.Series:
        """Boolean Series (indexed by `index`): True for expensive Hochtarif slots.

        Expensive = weekday 06:00-21:00 and not a cheap day. Cheap days
        (weekend/holiday) and the weekday 21:00-06:00 window are all False.
        See FSD 4.2.2 / 4.1.3.
        """
        sim_swiss = index.tz_convert(SWISS_TZ)
        hours = pd.Series(sim_swiss.hour, index=index)
        minutes = pd.Series(sim_swiss.minute, index=index)
        after_cheap_end = (hours > self.cheap_end_hour) | (
            (hours == self.cheap_end_hour) & (minutes > 0)
        )
        at_or_before_cheap_start = (hours < self.cheap_start_hour) | (
            (hours == self.cheap_start_hour) & (minutes == 0)
        )
        is_expensive_day = pd.Series(
            [not self.is_cheap_day(ts) for ts in sim_swiss], index=index
        )
        return after_cheap_end & at_or_before_cheap_start & is_expensive_day

    def filter_expensive_periods(self, simulation: pd.DataFrame) -> pd.DataFrame:
        """Filter simulation to only expensive weekday periods (06:15-21:00)."""
        if simulation.empty:
            return simulation
        return simulation[self.expensive_mask(simulation.index)]

    def get_tariff_periods(self, now: datetime) -> TariffPeriod:
        """Calculate tariff periods based on current time.

        Returns:
            TariffPeriod with cheap_start, cheap_end, target, is_cheap_now

        """
        # Convert to Swiss time for tariff comparison
        # Tariff hours (21:00-06:00) are defined in Swiss time
        now_swiss = now.astimezone(SWISS_TZ)

        # Normalize to start of current 15-min period
        now_swiss = now_swiss.replace(second=0, microsecond=0)
        now_swiss = now_swiss.replace(minute=(now_swiss.minute // 15) * 15)

        today = now_swiss.replace(hour=0, minute=0, second=0, microsecond=0)
        now = now_swiss  # Use Swiss time for all comparisons

        # Check if today is a cheap day (weekend/holiday)
        if self.is_cheap_day(now):
            # Case 4: Weekend/holiday — all day cheap
            check_day = today + timedelta(days=1)
            while self.is_cheap_day(check_day):
                check_day += timedelta(days=1)

            cheap_end = check_day.replace(hour=self.cheap_end_hour, minute=self.cheap_end_minute)
            target = check_day.replace(hour=21, minute=0)

            # Cheap started at previous evening or start of weekend
            cheap_start = now  # Already in cheap period
            is_cheap_now = True

        else:
            # Weekday
            today_cheap_start = today.replace(
                hour=self.cheap_start_hour, minute=self.cheap_start_minute
            )
            today_cheap_end = today.replace(hour=self.cheap_end_hour, minute=self.cheap_end_minute)

            if now.hour < self.cheap_end_hour or (
                now.hour == self.cheap_end_hour and now.minute < self.cheap_end_minute
            ):
                # Case 3: Weekday night (00:00-06:00)
                cheap_start = (today - timedelta(days=1)).replace(
                    hour=self.cheap_start_hour, minute=self.cheap_start_minute
                )
                cheap_end = today_cheap_end
                target = today.replace(hour=21, minute=0)
                is_cheap_now = True

            elif now.hour >= self.cheap_start_hour:
                # Case 2: Weekday evening (21:00-23:59)
                cheap_start = today_cheap_start

                # Check if tomorrow is weekend/holiday
                tomorrow = today + timedelta(days=1)
                if self.is_cheap_day(tomorrow):
                    # Find next weekday
                    check_day = tomorrow + timedelta(days=1)
                    while self.is_cheap_day(check_day):
                        check_day += timedelta(days=1)
                    cheap_end = check_day.replace(
                        hour=self.cheap_end_hour, minute=self.cheap_end_minute
                    )
                    target = check_day.replace(hour=21, minute=0)
                else:
                    cheap_end = tomorrow.replace(
                        hour=self.cheap_end_hour, minute=self.cheap_end_minute
                    )
                    target = tomorrow.replace(hour=21, minute=0)

                is_cheap_now = True

            else:
                # Case 1: Daytime expensive period (06:00 - 21:00)
                cheap_start = today_cheap_start
                cheap_end = (today + timedelta(days=1)).replace(
                    hour=self.cheap_end_hour, minute=self.cheap_end_minute
                )
                # Target is tomorrow 21:00 (end of next expensive period)
                target = (today + timedelta(days=1)).replace(hour=21, minute=0)
                is_cheap_now = False

        return TariffPeriod(
            cheap_start=cheap_start,
            cheap_end=cheap_end,
            target=target,
            is_cheap_now=is_cheap_now,
        )

    def simulate_soc(
        self,
        soc_percent: float,
        forecast: pd.DataFrame,
        block_from: datetime | None = None,
        block_until: datetime | None = None,
        max_soc_percent: float = 100.0,
        block_cheap: bool = False,
        cheap_mask: pd.Series | None = None,
        floor_wh: float = 0.0,
    ) -> pd.DataFrame:
        """Simulate the SOC trajectory with optional discharge blocking.

        Discharge is blocked inside the [block_from, block_until) window and/or,
        when block_cheap is set, on every slot flagged True in cheap_mask (the
        with-strategy of FSD 4.2.2). The battery never discharges below floor_wh.
        Charging stops at max_soc_percent (surplus above is exported; discharge
        unaffected). Per slot it records grid_import_wh = the house load not
        covered by PV or the battery (the energy bought from the grid).

        Returns a DataFrame with soc_percent, soc_wh, soc_wh_unclamped, net_wh,
        discharge_wh, grid_import_wh.
        """
        e_bat = soc_percent / 100 * self.capacity_wh
        e_bat_unclamped = e_bat
        ceil_wh = max_soc_percent / 100 * self.capacity_wh
        results = []

        for t, row in forecast.iterrows():
            net_wh = row["net_energy_wh"]

            # Record SOC at START of this period (before energy changes)
            results.append(
                {
                    "time": t,
                    "soc_percent": e_bat / self.capacity_wh * 100,
                    "soc_wh": e_bat,
                    "soc_wh_unclamped": e_bat_unclamped,
                    "net_wh": net_wh,
                    "discharge_wh": 0,
                    "grid_import_wh": 0,
                }
            )

            in_block_window = (
                block_until is not None
                and (block_from is None or t >= block_from)
                and t < block_until
            )
            cheap_here = (
                block_cheap
                and cheap_mask is not None
                and t in cheap_mask.index
                and bool(cheap_mask.loc[t])
            )
            discharge_blocked = in_block_window or cheap_here

            if net_wh > 0:
                # Surplus: charge up to the ceiling; surplus above is exported.
                charge = min(
                    net_wh * self.charge_efficiency,
                    self.max_charge_wh_per_15min,
                    max(0.0, ceil_wh - e_bat),
                )
                e_bat += charge
                e_bat_unclamped = min(
                    e_bat_unclamped + net_wh * self.charge_efficiency, self.capacity_wh
                )
                discharge_wh = 0
                grid_import_wh = 0.0
            elif discharge_blocked:
                # Deficit but blocked: hold; buy the whole deficit from the grid.
                discharge_wh = 0
                discharge_needed = -net_wh / self.discharge_efficiency
                e_bat_unclamped -= discharge_needed
                grid_import_wh = -net_wh
            else:
                # Deficit: discharge down to floor_wh; buy any shortfall.
                discharge_needed = -net_wh / self.discharge_efficiency
                discharge = min(
                    discharge_needed,
                    self.max_discharge_wh_per_15min,
                    max(0.0, e_bat - floor_wh),
                )
                e_bat -= discharge
                e_bat_unclamped -= discharge_needed
                discharge_wh = discharge_needed
                grid_import_wh = max(0.0, -net_wh - discharge * self.discharge_efficiency)

            results[-1]["discharge_wh"] = discharge_wh
            results[-1]["grid_import_wh"] = grid_import_wh

        return pd.DataFrame(results).set_index("time")

    def reaches_target_today(
        self,
        current_soc: float,
        forecast: pd.DataFrame,
        now: datetime,
        target: float,
    ) -> tuple[bool, float | None, str | None]:
        """Report whether the battery reaches `target`% today, re-anchored to live SOC.

        The 10-s-fresh version of the EV target gate (Section 4.3.6). Re-runs the
        SOC simulation from the *current* SOC over the net-energy forecast and
        takes the peak between now and local midnight. Unlike the `soc_forecast`
        curve in InfluxDB — which is anchored to the 15-min cycle and so reads
        optimistically while the car drains the real battery — this reflects the
        live (car-suppressed) SOC, so the gate stops the car at the right moment
        instead of ~one forecast period late.

        Returns (reaches, peak_soc_today, full_time_local "HH:MM" or None). An
        empty/missing forecast returns (False, None, None) — fail-closed, matching
        the InfluxDB gate's precautionary default.
        """
        if forecast is None or forecast.empty:
            return False, None, None
        end_today = (
            now.astimezone(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        sim = self.simulate_soc(current_soc, forecast, max_soc_percent=100.0)
        peak: float | None = None
        full_time: str | None = None
        for t, v in sim["soc_percent"].items():
            tt = t if t.tzinfo else t.replace(tzinfo=UTC)
            if tt < now or tt > end_today:
                continue
            fv = float(v)
            if peak is None or fv > peak:
                peak = fv
            if full_time is None and fv >= target:
                full_time = tt.astimezone(SWISS_TZ).strftime("%H:%M")
        if peak is None:
            return False, None, None
        return peak >= target, peak, full_time

    def calculate_decision(
        self,
        soc_percent: float,
        forecast: pd.DataFrame,
        now: datetime,
        previously_blocked: bool = False,
        max_soc_percent: float = 100.0,
    ) -> tuple[DischargeDecision, pd.DataFrame, pd.DataFrame]:
        """Decide whether the home battery may discharge (FSD 4.2.2, Topic 4).

        Simulates the next 48 h two ways: **battery_on** (free discharge) and
        **battery_off** (hold discharge during cheap slots), sums the
        expensive-hours grid import (Wh) for each, and the lower wins -- ties go
        to battery_on (free discharge). Emits `expensive_import_wh` (== 0 means
        the battery covers every expensive hour without buying).
        `previously_blocked` is accepted but unused -- the metric is a stable
        cost, so no hysteresis.

        Returns (decision, sim_battery_on, sim_battery_off, sim_planned), where
        sim_planned is whichever of the two the decision selects (the path that
        actually executes).
        """
        if forecast.empty:
            logger.warning("No forecast data available")
            return (
                DischargeDecision(
                    discharge_allowed=True,
                    reason="No forecast data",
                    min_soc_percent=100.0,
                    expensive_import_wh=0.0,
                ),
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
            )

        horizon = now + timedelta(hours=48)
        fc = forecast[forecast.index <= horizon]
        if fc.empty:
            fc = forecast

        expensive = self.expensive_mask(fc.index)
        cheap = ~expensive
        floor_wh = self.min_soc_wh

        sim_without = self.simulate_soc(
            soc_percent, fc, max_soc_percent=max_soc_percent, floor_wh=floor_wh
        )
        sim_with = self.simulate_soc(
            soc_percent,
            fc,
            max_soc_percent=max_soc_percent,
            floor_wh=floor_wh,
            block_cheap=True,
            cheap_mask=cheap,
        )

        imp_without = float(sim_without.loc[expensive, "grid_import_wh"].sum())
        imp_with = float(sim_with.loc[expensive, "grid_import_wh"].sum())

        # Lower expensive import wins; tie -> battery_on (free discharge).
        use_with = imp_with < imp_without
        expensive_import_wh = imp_with if use_with else imp_without
        sim_planned = sim_with if use_with else sim_without

        exp_soc = sim_planned.loc[expensive, "soc_percent"]
        min_soc = float(exp_soc.min()) if not exp_soc.empty else 100.0

        now_cheap = bool(cheap.iloc[0]) if len(cheap) else False
        if use_with and now_cheap:
            discharge_allowed = False
            reason = (
                f"Hold (cheap slot) - with-strategy saves "
                f"{imp_without - imp_with:.0f} Wh expensive import; "
                f"exp_import={expensive_import_wh:.0f} Wh"
            )
        elif use_with:
            discharge_allowed = True
            reason = (
                f"Discharge (expensive slot; with-strategy active); "
                f"exp_import={expensive_import_wh:.0f} Wh"
            )
        else:
            discharge_allowed = True
            reason = (
                f"Discharge (free-discharge optimal); "
                f"exp_import={expensive_import_wh:.0f} Wh"
            )

        logger.info(
            f"Discharge: expensive import battery_on={imp_without:.0f} Wh, "
            f"battery_off={imp_with:.0f} Wh -> {'battery_off' if use_with else 'battery_on'}; "
            f"allowed={discharge_allowed}, exp_min_soc={min_soc:.0f}%"
        )

        return (
            DischargeDecision(
                discharge_allowed=discharge_allowed,
                reason=reason,
                min_soc_percent=min_soc,
                expensive_import_wh=expensive_import_wh,
            ),
            sim_without,
            sim_with,
            sim_planned,
        )

def should_charge_now(
    remaining_surplus_wh: list[float],
    headroom_wh: float,
    current_surplus_wh: float,
    max_charge_per_interval_wh: float | None = None,
) -> bool:
    """Decide whether to charge the home battery now (export-peak shaving).

    Defers battery charging so its remaining headroom absorbs the highest
    part of the day's grid-export curve. We pick the highest-surplus
    intervals of the rest of the day until their absorbed energy fills the
    headroom — the "water level" L is the surplus of the lowest selected
    interval — and charge now iff the current interval is in that top band.

    Each interval absorbs at most ``max_charge_per_interval_wh`` (the charge
    power × 0.25 h); surplus above that is exported even while charging. A
    lower charge power therefore raises the per-interval cap's bite, so more
    intervals are needed to fill the headroom (a wider, gentler band → a
    flatter feed-in profile). When the cap is None the interval absorbs its
    whole surplus (the original zero-export behaviour).

    This is evaluated every 15 min and is fully self-correcting: headroom
    is re-read from the actual SOC each tick, so as the battery fills, L
    rises, the band narrows, and charging stops once full.

    Args:
        remaining_surplus_wh: per-15-min net surplus (Wh) for the rest of
            today, including the current interval (negatives allowed).
        headroom_wh: energy needed to reach the target SOC (Wh).
        current_surplus_wh: this interval's net surplus (Wh).
        max_charge_per_interval_wh: cap on energy the battery can absorb in
            one 15-min interval (None = uncapped / whole surplus).

    Returns:
        True to charge (or release control), False to defer charging.

    """
    # No surplus right now → nothing to defer; let normal behaviour run.
    if current_surplus_wh <= 0:
        return True
    # Battery already full → no benefit in deferring.
    if headroom_wh <= 0:
        return True

    def _absorbed(surplus: float) -> float:
        if max_charge_per_interval_wh is None:
            return surplus
        return min(surplus, max_charge_per_interval_wh)

    positives = [e for e in remaining_surplus_wh if e > 0]
    total_absorbable = sum(_absorbed(e) for e in positives)
    # Can't fill the battery from the remaining (capped) surplus → charge ASAP.
    if total_absorbable <= headroom_wh:
        return True

    # Water-fill: accumulate the highest-surplus intervals (each contributing
    # at most the per-interval cap) until the headroom is met; L is the
    # surplus of the last (lowest) one included.
    accumulated = 0.0
    water_level = 0.0
    for surplus in sorted(positives, reverse=True):
        accumulated += _absorbed(surplus)
        water_level = surplus
        if accumulated >= headroom_wh:
            break

    # Charge now iff this interval is in the selected top band.
    return current_surplus_wh >= water_level
