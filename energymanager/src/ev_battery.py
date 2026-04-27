"""EV charging decisions based on the home-battery SOC forecast.

Layer-2 decision module: consumes the home-battery SOC forecast produced by
BatteryOptimizer (Layer 1) and decides whether EV charging is safe.

Rule: EV is allowed only if the home-battery SOC forecast stays
>= min_soc_percent across the next `horizon` (default 48 h), with one
15-min slot of the candidate EV load subtracted as worst case. Re-evaluated
every 15 min — if the forecast drops below the floor, EV stops and the
battery (now EV-free) rides the remaining forecast back up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from influxdb_client import InfluxDBClient

logger = logging.getLogger(__name__)

SWISS_TZ = ZoneInfo("Europe/Zurich")
EV_SAFETY_HORIZON = timedelta(hours=48)


class EVBatteryOptimizer:
    """Decides whether EV charging is safe for the home battery.

    Independent of discharge-blocking logic: both rules happen to share the
    same `min_soc_percent` value but answer different questions.
    """

    def __init__(
        self,
        *,
        influx_client: InfluxDBClient,
        bucket: str,
        capacity_wh: float,
        min_soc_percent: float,
        horizon: timedelta = EV_SAFETY_HORIZON,
    ) -> None:
        self.influx_client = influx_client
        self.bucket = bucket
        self.capacity_wh = capacity_wh
        self.min_soc_percent = min_soc_percent
        self.horizon = horizon

    def check_ev_safe(self, ev_load_wh: float = 0.0) -> tuple[bool, float]:
        """Return (safe, min_soc_in_horizon_percent).

        Queries the `with_strategy` SOC forecast from `now` to `now + horizon`,
        takes the minimum, subtracts the worst-case EV load, and compares to
        `min_soc_percent`.

        On error or missing data, returns (False, 0.0) — blocking EV is the
        safe default.
        """
        try:
            now = datetime.now(UTC)
            stop = (now + self.horizon).isoformat()
            query = f'''
            from(bucket: "{self.bucket}")
              |> range(start: now(), stop: {stop})
              |> filter(fn: (r) => r._measurement == "soc_forecast")
              |> filter(fn: (r) => r.scenario == "with_strategy")
              |> filter(fn: (r) => r._field == "soc_percent")
              |> min()
            '''
            result = self.influx_client.query_api().query(query)
            if not result or not result[0].records:
                logger.warning("No SOC forecast available — blocking EV as precaution")
                return False, 0.0

            raw_min = result[0].records[0].get_value()
            min_soc = max(0.0, raw_min - self._extra_load_percent(ev_load_wh))
            safe = min_soc >= self.min_soc_percent

            hours = self.horizon.total_seconds() / 3600
            load_note = f" (with EV {ev_load_wh:.0f}Wh)" if ev_load_wh > 0 else ""
            logger.debug(
                f"EV safety: min SOC over next {hours:.0f}h={min_soc:.0f}%"
                f"{load_note} (floor={self.min_soc_percent:.0f}%) → "
                f"{'EV allowed' if safe else 'EV blocked'}"
            )
            return safe, min_soc

        except Exception as e:
            logger.error(f"EV safety check failed: {e}")
            return False, 0.0

    def _extra_load_percent(self, extra_load_wh: float) -> float:
        if extra_load_wh <= 0 or self.capacity_wh <= 0:
            return 0.0
        return extra_load_wh / self.capacity_wh * 100

    def will_battery_hit_full(
        self,
    ) -> tuple[bool, float | None, str | None, datetime]:
        """Check if the home battery is forecast to reach 100% today.

        Only looks at today's solar window (until midnight local time).
        Tomorrow's forecast is irrelevant — the battery being full tomorrow
        doesn't justify diverting today's solar to the EV.

        Returns:
            (hits_full, peak_soc or None, full_time_local "HH:MM" or None, end_of_today)

        """
        now = datetime.now(UTC)
        now_local = now.astimezone(SWISS_TZ)
        end_of_today = now_local.replace(
            hour=23, minute=59, second=59, microsecond=0
        ).astimezone(UTC)
        end_stop = end_of_today.isoformat()

        query = f"""
        from(bucket: "{self.bucket}")
          |> range(start: now(), stop: {end_stop})
          |> filter(fn: (r) => r._measurement == "soc_forecast")
          |> filter(fn: (r) => r.scenario == "with_strategy")
          |> filter(fn: (r) => r._field == "soc_percent")
          |> max()
        """
        result = self.influx_client.query_api().query(query)
        if not result or not result[0].records:
            return False, None, None, end_of_today

        peak_soc = result[0].records[0].get_value()
        hits_full = peak_soc >= 99

        full_time_local = None
        if hits_full:
            time_query = f"""
            from(bucket: "{self.bucket}")
              |> range(start: now(), stop: {end_stop})
              |> filter(fn: (r) => r._measurement == "soc_forecast")
              |> filter(fn: (r) => r.scenario == "with_strategy")
              |> filter(fn: (r) => r._field == "soc_percent")
              |> filter(fn: (r) => r._value >= 99.0)
              |> first()
            """
            time_result = self.influx_client.query_api().query(time_query)
            if time_result and time_result[0].records:
                full_utc = time_result[0].records[0].get_time()
                full_time_local = full_utc.astimezone(SWISS_TZ).strftime("%H:%M")

        logger.debug(
            f"Peak SOC today: {peak_soc:.0f}% → "
            f"{'battery full' if hits_full else 'not full'}"
            f"{f' at {full_time_local}' if full_time_local else ''}"
        )
        return hits_full, peak_soc, full_time_local, end_of_today
