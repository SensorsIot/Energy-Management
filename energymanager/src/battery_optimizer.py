"""
Battery discharge optimization based on energy balance.

Simplified Algorithm (FSD v2.6):
1. If expensive tariff (06:00-21:00): always allow discharge
2. If cheap tariff: simulate SOC from NOW until end of next expensive period
3. Check if min SOC stays >= min_soc during ALL expensive hours
   - If yes: allow discharge
   - If no: block discharge
4. Re-check every 15 minutes (self-correcting based on actual conditions)
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

# Swiss timezone for display
SWISS_TZ = ZoneInfo("Europe/Zurich")


def swiss_time(dt: datetime) -> str:
    """Format datetime as HH:MM in Swiss timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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


class BatteryOptimizer:
    """Optimize battery discharge based on tariff and forecast."""

    def __init__(
        self,
        capacity_wh: float = 10000,
        min_soc_percent: float = 10,
        charge_efficiency: float = 0.95,
        discharge_efficiency: float = 0.95,
        max_charge_w: float = 5000,
        max_discharge_w: float = 5000,
        weekday_cheap_start: str = "21:00",
        weekday_cheap_end: str = "06:00",
        weekend_all_day_cheap: bool = True,
        holidays: List[str] = None,
    ):
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

        # Parse holidays
        self.holidays = set()
        if holidays:
            for h in holidays:
                try:
                    self.holidays.add(datetime.strptime(h, "%Y-%m-%d").date())
                except ValueError:
                    logger.warning(f"Invalid holiday format: {h}")

    def is_holiday(self, dt: datetime) -> bool:
        """Check if date is a holiday."""
        return dt.date() in self.holidays

    def is_weekend(self, dt: datetime) -> bool:
        """Check if date is weekend (Saturday=5, Sunday=6)."""
        return dt.weekday() >= 5

    def is_cheap_day(self, dt: datetime) -> bool:
        """Check if entire day is cheap (weekend or holiday)."""
        return (self.weekend_all_day_cheap and self.is_weekend(dt)) or self.is_holiday(dt)

    def filter_expensive_periods(self, simulation: pd.DataFrame) -> pd.DataFrame:
        """Filter simulation to only include expensive weekday periods (06:15-21:00).

        Excludes weekend/holiday days entirely since they are all-day cheap.
        """
        if simulation.empty:
            return simulation

        sim_swiss = simulation.index.tz_convert(SWISS_TZ)
        hours = sim_swiss.hour
        minutes = sim_swiss.minute

        after_cheap_end = (hours > self.cheap_end_hour) | (
            (hours == self.cheap_end_hour) & (minutes > 0)
        )
        at_or_before_cheap_start = (hours < self.cheap_start_hour) | (
            (hours == self.cheap_start_hour) & (minutes == 0)
        )
        is_expensive_day = pd.Series(
            [not self.is_cheap_day(t) for t in sim_swiss],
            index=simulation.index,
        )
        return simulation[after_cheap_end & at_or_before_cheap_start & is_expensive_day]

    def get_tariff_periods(self, now: datetime) -> TariffPeriod:
        """
        Calculate tariff periods based on current time.

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
            # Find next Monday (or next non-holiday weekday)
            check_day = today + timedelta(days=1)
            while self.is_cheap_day(check_day):
                check_day += timedelta(days=1)

            cheap_end = check_day.replace(
                hour=self.cheap_end_hour,
                minute=self.cheap_end_minute
            )
            target = check_day.replace(hour=21, minute=0)

            # Cheap started at previous evening or start of weekend
            cheap_start = now  # Already in cheap period
            is_cheap_now = True

        else:
            # Weekday
            today_cheap_start = today.replace(
                hour=self.cheap_start_hour,
                minute=self.cheap_start_minute
            )
            today_cheap_end = today.replace(
                hour=self.cheap_end_hour,
                minute=self.cheap_end_minute
            )

            if now.hour < self.cheap_end_hour or (
                now.hour == self.cheap_end_hour and now.minute < self.cheap_end_minute
            ):
                # Before 06:00 - in cheap period from last night
                cheap_start = (today - timedelta(days=1)).replace(
                    hour=self.cheap_start_hour,
                    minute=self.cheap_start_minute
                )
                cheap_end = today_cheap_end
                target = today.replace(hour=21, minute=0)
                is_cheap_now = True

            elif now.hour >= self.cheap_start_hour:
                # After 21:00 - in cheap period
                cheap_start = today_cheap_start

                # Check if tomorrow is weekend/holiday
                tomorrow = today + timedelta(days=1)
                if self.is_cheap_day(tomorrow):
                    # Find next weekday
                    check_day = tomorrow + timedelta(days=1)
                    while self.is_cheap_day(check_day):
                        check_day += timedelta(days=1)
                    cheap_end = check_day.replace(
                        hour=self.cheap_end_hour,
                        minute=self.cheap_end_minute
                    )
                    target = check_day.replace(hour=21, minute=0)
                else:
                    cheap_end = tomorrow.replace(
                        hour=self.cheap_end_hour,
                        minute=self.cheap_end_minute
                    )
                    target = tomorrow.replace(hour=21, minute=0)

                is_cheap_now = True

            else:
                # Daytime expensive period (06:00 - 21:00)
                cheap_start = today_cheap_start
                cheap_end = (today + timedelta(days=1)).replace(
                    hour=self.cheap_end_hour,
                    minute=self.cheap_end_minute
                )
                # Target is tomorrow 21:00 (end of next expensive period)
                target = (today + timedelta(days=1)).replace(hour=21, minute=0)
                is_cheap_now = False

        return TariffPeriod(
            cheap_start=cheap_start,
            cheap_end=cheap_end,
            target=target,
            is_cheap_now=is_cheap_now
        )

    def simulate_soc(
        self,
        soc_percent: float,
        forecast: pd.DataFrame,
        block_from: Optional[datetime] = None,
        block_until: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Simulate SOC trajectory with optional discharge blocking.

        Args:
            soc_percent: Starting SOC (0-100)
            forecast: DataFrame with net_energy_wh column
            block_from: Start blocking from this time (None = from start)
            block_until: Block discharge until this time (None = no blocking)

        Returns:
            DataFrame with soc_percent, soc_wh, soc_wh_unclamped columns
        """
        e_bat = soc_percent / 100 * self.capacity_wh
        e_bat_unclamped = e_bat
        results = []

        for t, row in forecast.iterrows():
            # Record SOC at START of this period (before energy changes)
            results.append({
                "time": t,
                "soc_percent": e_bat / self.capacity_wh * 100,
                "soc_wh": e_bat,
                "soc_wh_unclamped": e_bat_unclamped,
                "discharge_wh": 0,  # Will be updated below
            })

            net_wh = row["net_energy_wh"]
            # Block only in the specified time window
            in_block_window = (
                block_until and
                (block_from is None or t >= block_from) and
                t < block_until
            )
            discharge_blocked = in_block_window

            if net_wh > 0:
                # Surplus: charge battery
                charge = min(
                    net_wh * self.charge_efficiency,
                    self.max_charge_wh_per_15min,
                    self.capacity_wh - e_bat,
                )
                e_bat += charge
                e_bat_unclamped = min(
                    e_bat_unclamped + net_wh * self.charge_efficiency,
                    self.capacity_wh
                )
                discharge_wh = 0
            elif discharge_blocked:
                # Deficit but blocked: don't discharge
                discharge_wh = 0
                # Track what would have been discharged (unclamped)
                discharge_needed = -net_wh / self.discharge_efficiency
                e_bat_unclamped -= discharge_needed
            else:
                # Deficit: discharge battery
                discharge_needed = -net_wh / self.discharge_efficiency
                discharge = min(
                    discharge_needed,
                    self.max_discharge_wh_per_15min,
                    max(0, e_bat),
                )
                e_bat = max(0, e_bat - discharge)
                e_bat_unclamped -= discharge_needed
                discharge_wh = discharge_needed

            # Update the last result with discharge_wh for this period
            results[-1]["discharge_wh"] = discharge_wh

        return pd.DataFrame(results).set_index("time")

    def calculate_decision(
        self,
        soc_percent: float,
        forecast: pd.DataFrame,
        now: datetime,
    ) -> Tuple[DischargeDecision, pd.DataFrame, pd.DataFrame]:
        """
        Calculate battery discharge decision.

        Simplified Algorithm (FSD v2.6):
        1. Always simulate SOC trajectory for visualization
        2. If expensive tariff: always allow discharge (we're in the protected period)
        3. If cheap tariff: check if min SOC stays >= min_soc during expensive hours
           - If yes: allow discharge
           - If no: block discharge
        4. Re-check every 15 minutes (self-correcting)

        Args:
            soc_percent: Current SOC (0-100)
            forecast: DataFrame with pv_energy_wh, load_energy_wh, net_energy_wh
            now: Current time

        Returns:
            (decision, sim_no_strategy, sim_with_strategy)
        """
        tariff = self.get_tariff_periods(now)

        logger.info(f"Tariff: cheap={tariff.is_cheap_now}, "
                   f"cheap_end={tariff.cheap_end}, target={tariff.target}")

        if forecast.empty:
            logger.warning("No forecast data available")
            return (
                DischargeDecision(
                    discharge_allowed=True,
                    reason="No forecast data",
                    min_soc_percent=100.0,
                ),
                pd.DataFrame(),
                pd.DataFrame(),
            )

        # Step 1: Always simulate full trajectory from NOW to target
        # This gives us the complete picture for visualization
        sim_full = self.simulate_soc(soc_percent, forecast)

        # Step 2: Find minimum SOC during EXPENSIVE hours only
        # During cheap hours (21:00-06:00), low SOC is fine - electricity is cheap
        # During expensive hours (06:15-21:00 on weekdays), SOC must stay >= min_soc
        # Weekend/holiday days are excluded entirely (all-day cheap)
        expensive_periods = self.filter_expensive_periods(sim_full)

        if expensive_periods.empty:
            # No expensive periods in simulation
            min_soc_percent = 100.0
            min_soc_time = sim_full.index[0]
        else:
            min_soc_wh = expensive_periods["soc_wh"].min()
            min_soc_percent = min_soc_wh / self.capacity_wh * 100
            min_soc_time = expensive_periods["soc_wh"].idxmin()

        # Simple yes/no: does SOC stay above threshold?
        soc_ok = min_soc_percent >= self.min_soc_percent

        logger.info(f"Min SOC during expensive hours: {min_soc_percent:.0f}% at {swiss_time(min_soc_time)}, "
                   f"threshold: {self.min_soc_percent:.0f}%, OK: {soc_ok}")

        # Step 3: Decision logic - simple yes/no
        if not tariff.is_cheap_now:
            # EXPENSIVE TARIFF (06:00-21:00): Always allow discharge
            # We're in the period we were protecting - use the battery now
            discharge_allowed = True
            reason = f"Expensive tariff - allow discharge (min SOC {min_soc_percent:.0f}%)"

        elif soc_ok:
            # CHEAP TARIFF + SOC OK: Allow discharge
            discharge_allowed = True
            reason = f"SOC stays >= {self.min_soc_percent:.0f}% (min: {min_soc_percent:.0f}% at {swiss_time(min_soc_time)})"

        else:
            # CHEAP TARIFF + SOC NOT OK: Block discharge
            discharge_allowed = False
            reason = f"Block - SOC would drop to {min_soc_percent:.0f}% at {swiss_time(min_soc_time)} (need {self.min_soc_percent:.0f}%)"

        logger.info(f"Decision: discharge_allowed={discharge_allowed}")

        # For visualization: if blocking, show what would happen with full block until cheap_end
        if not discharge_allowed:
            sim_with_strategy = self.simulate_soc(
                soc_percent,
                forecast,
                block_from=now,
                block_until=tariff.cheap_end
            )
        else:
            sim_with_strategy = sim_full

        return (
            DischargeDecision(
                discharge_allowed=discharge_allowed,
                reason=reason,
                min_soc_percent=min_soc_percent,
            ),
            sim_full,
            sim_with_strategy,
        )
