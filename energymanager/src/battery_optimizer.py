"""
Battery discharge optimization based on energy balance.

Algorithm:
1. Simulate with battery always ON until target (next 21:00)
2. Calculate deficit at target
3. If deficit > 0: block discharge during cheap tariff to save energy
4. Switch ON when saved >= deficit or cheap tariff ends
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
    switch_on_time: Optional[datetime]
    reason: str
    deficit_wh: float
    saved_wh: float


class BatteryOptimizer:
    """Optimize battery discharge based on tariff and forecast."""

    def __init__(
        self,
        capacity_wh: float = 10000,
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

    def get_tariff_periods(self, now: datetime) -> TariffPeriod:
        """
        Calculate tariff periods based on current time.

        Returns:
            TariffPeriod with cheap_start, cheap_end, target, is_cheap_now
        """
        # Normalize to start of current 15-min period
        now = now.replace(second=0, microsecond=0)
        now = now.replace(minute=(now.minute // 15) * 15)

        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

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
                    switch_on_time=None,
                    reason="No forecast data",
                    deficit_wh=0,
                    saved_wh=0,
                ),
                pd.DataFrame(),
                pd.DataFrame(),
            )

        # Step 1: Simulate full trajectory from NOW to target (tomorrow 21:00)
        # This gives us the complete picture for visualization
        sim_full_no_strategy = self.simulate_soc(soc_percent, forecast)

        # Get SOC at cheap_start (21:00 tonight) for decision calculations
        forecast_until_cheap = forecast[forecast.index < tariff.cheap_start]
        if not forecast_until_cheap.empty:
            soc_at_cheap_start = sim_full_no_strategy.loc[
                sim_full_no_strategy.index < tariff.cheap_start, "soc_percent"
            ].iloc[-1]
        else:
            soc_at_cheap_start = soc_percent

        logger.info(f"SOC at cheap start ({swiss_time(tariff.cheap_start)}): {soc_at_cheap_start:.1f}%")

        # Step 2: Get unclamped SOC at target time (tomorrow 21:00)
        soc_at_target = sim_full_no_strategy["soc_wh_unclamped"].iloc[-1]
        deficit_wh = max(0, -soc_at_target)

        logger.info(f"SOC at target (unclamped): {soc_at_target:.0f} Wh, "
                   f"deficit: {deficit_wh:.0f} Wh")

        # Step 3: Always simulate with_strategy for visualization comparison
        # Even if no deficit, simulate blocking during cheap hours to show the difference
        sim_full_with_strategy = self.simulate_soc(
            soc_percent,
            forecast,
            block_from=tariff.cheap_start,
            block_until=tariff.cheap_end  # Block entire cheap period for comparison
        )

        # If no deficit, still block during cheap hours to allow grid charging
        if deficit_wh <= 0:
            # During cheap tariff: block discharge to allow grid charging
            # During expensive tariff: allow discharge
            if tariff.is_cheap_now:
                return (
                    DischargeDecision(
                        discharge_allowed=False,
                        switch_on_time=tariff.cheap_end,
                        reason=f"Cheap tariff - blocking to charge (SOC: {soc_percent:.0f}%)",
                        deficit_wh=0,
                        saved_wh=0,
                    ),
                    sim_full_no_strategy,
                    sim_full_with_strategy,
                )
            else:
                return (
                    DischargeDecision(
                        discharge_allowed=True,
                        switch_on_time=None,
                        reason=f"No deficit - SOC at target: {soc_at_target/self.capacity_wh*100:.0f}%",
                        deficit_wh=0,
                        saved_wh=0,
                    ),
                    sim_full_no_strategy,
                    sim_full_with_strategy,
                )

        # Step 4: Calculate when to switch ON by accumulating savings during cheap period
        forecast_cheap = sim_full_no_strategy[
            (sim_full_no_strategy.index >= tariff.cheap_start) &
            (sim_full_no_strategy.index < tariff.cheap_end)
        ]

        saved_wh = 0
        switch_on_time = tariff.cheap_end  # Default: end of cheap period

        for t, row in forecast_cheap.iterrows():
            if row["discharge_wh"] > 0:
                saved_wh += row["discharge_wh"]

            if saved_wh >= deficit_wh:
                switch_on_time = t
                break

        logger.info(f"Saved {saved_wh:.0f} Wh, switch ON at {swiss_time(switch_on_time)}")

        # Step 5: Simulate with strategy - full trajectory from NOW
        # Blocking only applies during cheap tariff (21:00 to switch_on_time)
        sim_full_with_strategy = self.simulate_soc(
            soc_percent,
            forecast,
            block_from=tariff.cheap_start,
            block_until=switch_on_time
        )

        # Determine if discharge is currently allowed
        # During expensive tariff: always allow
        # During cheap tariff: only allow after switch_on_time
        if not tariff.is_cheap_now:
            discharge_allowed = True
            reason = (f"Expensive tariff - tonight block until {swiss_time(switch_on_time)} "
                     f"(deficit {deficit_wh:.0f} Wh)")
        elif now >= switch_on_time:
            discharge_allowed = True
            reason = f"Cheap tariff - discharge enabled (saved {saved_wh:.0f} Wh)"
        else:
            discharge_allowed = False
            if saved_wh < deficit_wh:
                reason = (f"Block discharge until {swiss_time(switch_on_time)} - "
                         f"saved {saved_wh:.0f}/{deficit_wh:.0f} Wh (shortfall)")
            else:
                reason = (f"Block discharge until {swiss_time(switch_on_time)} - "
                         f"saved {saved_wh:.0f} Wh")

        return (
            DischargeDecision(
                discharge_allowed=discharge_allowed,
                switch_on_time=switch_on_time,
                reason=reason,
                deficit_wh=deficit_wh,
                saved_wh=saved_wh,
            ),
            sim_full_no_strategy,
            sim_full_with_strategy,
        )
