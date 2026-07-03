"""Daily long-term PV summary (FSD §8.1).

Writes one `pv_daily` point per day to the infinite-retention `energy_longterm`
bucket at 23:55 local: production, specific yield, clear-sky performance ratio,
calibration gains, clipping hours, and forecast bias. PV physics and health
only — household energy flows belong to Home Assistant's energy statistics.
"""

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from influxdb_client import Point, WritePrecision

from src.calibration import STRING_SENSORS, CalibrationTracker  # noqa: F401  (sensors doc'd there)

logger = logging.getLogger(__name__)

LONGTERM_BUCKET = "energy_longterm"
CLIP_THRESHOLD_FRACTION = 0.96
PR_SUNNY_MIN_OBS = 5

# Daily-resetting production counters in the HomeAssistant bucket
PRODUCTION_SENSORS = {
    "huawei": "inverter_daily_yield",   # kWh, resets at midnight
    "enphase": "enphase_energy_today",  # kWh, resets at midnight
}
TOTAL_POWER_SENSOR = "solar_pv_total_ac_power"  # W
ENPHASE_POWER_SENSOR = "enphase_power"          # W


def compute_summary(
    production_kwh: dict[str, float],
    power_15m: pd.Series,
    enphase_15m: pd.Series,
    clearsky_total_kwh: float,
    sunny_ratios: list[float],
    gains: dict[str, float],
    forecast_p50_kwh: float | None,
    total_dc_wp: float,
    enphase_cap_w: float = 1500.0,
    huawei_cap_w: float = 10000.0,
) -> dict:
    """Pure computation of the pv_daily fields (unit-testable)."""
    huawei = production_kwh.get("huawei", 0.0)
    enphase = production_kwh.get("enphase", 0.0)
    total = huawei + enphase

    fields: dict[str, float] = {
        "production_huawei_kwh": round(huawei, 3),
        "production_enphase_kwh": round(enphase, 3),
        "production_total_kwh": round(total, 3),
        "specific_yield_kwh_kwp": round(total / (total_dc_wp / 1000.0), 3),
        "peak_power_w": round(float(power_15m.max()), 0) if len(power_15m) else 0.0,
        "clearsky_potential_kwh": round(clearsky_total_kwh, 3),
    }
    if clearsky_total_kwh > 0:
        fields["performance_ratio"] = round(total / clearsky_total_kwh, 3)
    if len(sunny_ratios) >= PR_SUNNY_MIN_OBS:
        fields["pr_sunny"] = round(float(np.median(sunny_ratios)), 3)
    for name, gain in gains.items():
        fields[f"gain_{name.lower()}"] = round(float(gain), 3)

    # Clipping hours: 15-min means at or above 96 % of the AC cap
    if len(enphase_15m):
        fields["clipping_hours_south"] = round(
            float((enphase_15m >= CLIP_THRESHOLD_FRACTION * enphase_cap_w).sum()) * 0.25, 2
        )
    if len(power_15m):
        huawei_15m = power_15m - enphase_15m.reindex(power_15m.index).fillna(0.0)
    else:
        huawei_15m = power_15m
    if len(huawei_15m):
        fields["clipping_hours_huawei"] = round(
            float((huawei_15m >= CLIP_THRESHOLD_FRACTION * huawei_cap_w).sum()) * 0.25, 2
        )

    if forecast_p50_kwh and forecast_p50_kwh > 0:
        fields["forecast_p50_kwh"] = round(forecast_p50_kwh, 3)
        fields["forecast_bias"] = round(total / forecast_p50_kwh, 3)

    return fields


class DailySummary:
    """Collects the day's data and writes the pv_daily point."""

    def __init__(self, tracker: CalibrationTracker, total_dc_wp: float,
                 bucket: str = LONGTERM_BUCKET) -> None:
        self.tracker = tracker
        self.total_dc_wp = total_dc_wp
        self.bucket = bucket
        self.local_tz = tracker.local_tz

    def _fetch_15m(self, entity: str, start: datetime, stop: datetime) -> pd.Series:
        start_s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        stop_s = stop.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = f'''
        from(bucket: "{self.tracker.ha_bucket}")
          |> range(start: {start_s}, stop: {stop_s})
          |> filter(fn: (r) => r.entity_id == "{entity}" and r._field == "value")
          |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
          |> keep(columns: ["_time", "_value"])
        '''
        try:
            result = self.tracker.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                return pd.Series(dtype=float)
            s = result.set_index("_time")["_value"].astype(float)
            s.index = pd.DatetimeIndex(s.index).tz_convert("UTC")
            return s
        except Exception as e:
            logger.error(f"Failed to fetch {entity}: {e}")
            return pd.Series(dtype=float)

    def _daily_counter_total(self, entity: str, start: datetime, stop: datetime) -> float:
        """Day total of a midnight-resetting counter = its max over the day."""
        s = self._fetch_15m(entity, start, stop)
        return float(s.max()) if len(s) else 0.0

    def _forecast_p50_for_day(self, day_local: datetime) -> float | None:
        """Sum of the previous evening's 21:00 snapshot P50 over this day."""
        snapshot_id = (day_local - timedelta(days=1)).strftime("%Y-%m-%d")
        query = f'''
        from(bucket: "{self.tracker.pv_bucket}")
          |> range(start: -3d)
          |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot"
              and r.snapshot_id == "{snapshot_id}" and r.model == "hybrid"
              and r.string == "total" and r._field == "forecast_wh_p50")
          |> sum()
        '''
        try:
            result = self.tracker.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                return None
            return float(result["_value"].iloc[0]) / 1000.0
        except Exception as e:
            logger.debug(f"No snapshot forecast for {snapshot_id}: {e}")
            return None

    def _sunny_ratios_today(self, snapshot_id: str) -> list[float]:
        query = f'''
        from(bucket: "{self.tracker.pv_bucket}")
          |> range(start: -2d)
          |> filter(fn: (r) => r._measurement == "calibration_observations"
              and r.snapshot_id == "{snapshot_id}" and r._field == "ratio")
        '''
        try:
            result = self.tracker.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                return []
            return [float(x) for x in result["_value"]]
        except Exception:
            return []

    def write_summary(self, day_local: datetime | None = None) -> bool:
        """Compute and write the pv_daily point for one local day (default today)."""
        if day_local is None:
            day_local = datetime.now(self.local_tz)
        day_start = day_local.astimezone(self.local_tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        snapshot_id = day_start.strftime("%Y-%m-%d")

        production = {
            name: self._daily_counter_total(entity, day_start, day_end)
            for name, entity in PRODUCTION_SENSORS.items()
        }
        power = self._fetch_15m(TOTAL_POWER_SENSOR, day_start, day_end)
        enphase_pw = self._fetch_15m(ENPHASE_POWER_SENSOR, day_start, day_end)

        # Clear-sky ceiling for the day (total AC), 15-min grid
        idx = pd.date_range(day_start, day_end, freq="15min", tz="UTC", inclusive="left")
        ref = self.tracker.clearsky_reference(idx)
        specs = self.tracker._string_specs()
        ew = ref[["East", "West"]].sum(axis=1) * specs["East"]["inverter"]["efficiency"]
        ew = np.clip(ew, 0, specs["East"]["inverter"]["max_power"])
        clearsky_kwh = float((ew + ref["South"]).sum() * 0.25 / 1000.0)

        gains = {}
        cal = self.tracker.load_calibration()
        for unit in ("East", "West", "SouthFront"):
            if unit in cal:
                key = "South" if unit == "SouthFront" else unit
                gains[key] = cal[unit].get("gain", 1.0)

        fields = compute_summary(
            production_kwh=production,
            power_15m=power,
            enphase_15m=enphase_pw,
            clearsky_total_kwh=clearsky_kwh,
            sunny_ratios=self._sunny_ratios_today(snapshot_id),
            gains=gains,
            forecast_p50_kwh=self._forecast_p50_for_day(day_start),
            total_dc_wp=self.total_dc_wp,
        )

        point = Point("pv_daily").time(day_start, WritePrecision.S)
        for k, v in fields.items():
            point = point.field(k, float(v))
        self.tracker.write_api.write(bucket=self.bucket, record=point)
        logger.info(
            f"pv_daily written for {snapshot_id}: "
            f"{fields['production_total_kwh']} kWh, PR {fields.get('performance_ratio')}"
        )
        return True
