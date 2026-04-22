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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from influxdb_client import InfluxDBClient

logger = logging.getLogger(__name__)

EV_SAFETY_HORIZON = timedelta(hours=48)


class EVBatteryOptimizer:
    """Decides whether EV charging is safe for the home battery.

    Independent of discharge-blocking logic: both rules happen to share the
    same `min_soc_percent` value but answer different questions.
    """

    def __init__(
        self,
        *,
        influx_client: "InfluxDBClient",
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
            now = datetime.now(timezone.utc)
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
            logger.info(
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
