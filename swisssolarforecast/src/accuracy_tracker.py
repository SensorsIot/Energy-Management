"""
Forecast accuracy tracking for SwissSolarForecast add-on.

Implements FSD Chapter 5.3: Forecast Accuracy #1 - Battery Discharge Optimization.

Phase 1: Snapshot (21:00 daily local time)
- Captures forecast for next 24h
- Records decision context (SOC, discharge status)

Phase 2: Evaluation (21:15 daily local time)
- Compares snapshot forecast with actual PV production from HomeAssistant bucket
- Writes per-period accuracy to pv_accuracy measurement
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)


class AccuracyTracker:
    """
    Tracks PV forecast accuracy for battery discharge optimization decisions.

    Phase 1: At 21:00 local time, snapshots the current forecast for the next 24h period
    and records decision context (SOC, discharge blocked status).
    """

    # String configuration matching FSD 5.3.3
    STRINGS = [
        {"string": "East", "inverter": "EastWest"},
        {"string": "West", "inverter": "EastWest"},
        {"string": "SouthFront", "inverter": "South"},
        {"string": "SouthBack", "inverter": "South"},
        {"string": "total", "inverter": "total"},
    ]

    # Map inverter names to HA entity IDs for actual PV power
    # Note: EastWest removed - use per-string East/West instead (DC power, more accurate)
    ACTUAL_ENTITIES = {
        "total": "solar_pv_total_ac_power",
        "South": "enphase_energy_power",
    }

    # Map individual strings to HA entity IDs for per-string actuals
    STRING_ACTUAL_ENTITIES = {
        "East": "inverter_pv_1_power",
        "West": "inverter_pv_2_power",
    }

    def __init__(
        self,
        influx_host: str,
        influx_port: int,
        influx_token: str,
        influx_org: str,
        pv_bucket: str = "pv_forecast",
        ha_url: str = "http://supervisor/core",
        ha_token: Optional[str] = None,
        soc_entity: str = "sensor.battery_state_of_capacity",
        discharge_control_entity: str = "number.battery_maximum_discharging_power",
        local_timezone: str = "Europe/Zurich",
    ):
        """
        Initialize accuracy tracker.

        Args:
            influx_host: InfluxDB hostname
            influx_port: InfluxDB port
            influx_token: InfluxDB token
            influx_org: InfluxDB organization
            pv_bucket: Bucket for PV forecasts (source and output)
            ha_url: Home Assistant API URL
            ha_token: Home Assistant long-lived access token
            soc_entity: HA entity for battery SOC
            discharge_control_entity: HA entity for discharge control
            local_timezone: Local timezone for snapshot timing (default: Europe/Zurich)
        """
        self.influx_host = influx_host
        self.influx_port = influx_port
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.pv_bucket = pv_bucket
        self.ha_url = ha_url.rstrip("/")
        self.soc_entity = soc_entity
        self.discharge_control_entity = discharge_control_entity
        self.local_tz = ZoneInfo(local_timezone)

        # Get HA token from environment if not provided
        self._ha_token = ha_token
        if not self._ha_token:
            self._ha_token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
            if not self._ha_token:
                try:
                    with open("/run/secrets/supervisor_token", "r") as f:
                        self._ha_token = f.read().strip()
                except FileNotFoundError:
                    pass

        self.client: Optional[InfluxDBClient] = None
        self.write_api = None
        self.query_api = None

    def connect(self):
        """Connect to InfluxDB."""
        url = f"http://{self.influx_host}:{self.influx_port}"
        logger.info(f"Accuracy tracker connecting to InfluxDB at {url}")

        self.client = InfluxDBClient(
            url=url,
            token=self.influx_token,
            org=self.influx_org,
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

        # Verify connection
        try:
            health = self.client.health()
            logger.info(f"Accuracy tracker InfluxDB connection: {health.status}")
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            raise

    def close(self):
        """Close InfluxDB connection."""
        if self.client:
            self.client.close()
            logger.info("Accuracy tracker InfluxDB connection closed")

    def _get_ha_state(self, entity_id: str) -> Optional[dict]:
        """Get entity state from Home Assistant API."""
        if not self._ha_token:
            logger.warning("No HA token available, cannot query HA API")
            return None

        try:
            response = requests.get(
                f"{self.ha_url}/api/states/{entity_id}",
                headers={"Authorization": f"Bearer {self._ha_token}"},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get HA state for {entity_id}: {e}")
            return None

    def _get_ha_numeric_value(self, entity_id: str) -> Optional[float]:
        """Get numeric value from HA entity."""
        state = self._get_ha_state(entity_id)
        if not state:
            return None
        try:
            value = float(state.get("state", 0))
            return value
        except (ValueError, TypeError):
            logger.warning(f"Could not parse numeric value from {entity_id}: {state.get('state')}")
            return None

    def snapshot_forecast(self, decision_time: Optional[datetime] = None) -> bool:
        """
        Snapshot current forecast for the next 24h period at decision time.

        This is called at 21:00 daily to freeze the forecast that will be
        compared with actuals the next day.

        Args:
            decision_time: Override decision time (default: now)

        Returns:
            True if snapshot was successful
        """
        if decision_time is None:
            decision_time = datetime.now(timezone.utc)

        # Generate snapshot_id as date string (YYYY-MM-DD)
        snapshot_id = decision_time.strftime("%Y-%m-%d")
        snapshot_type = "battery_21h"

        logger.info(f"Creating forecast snapshot for {snapshot_id}")

        # Define the 24h period to snapshot (21:00 to next day 21:00)
        snapshot_start = decision_time.replace(minute=0, second=0, microsecond=0)
        snapshot_end = snapshot_start + timedelta(hours=24)

        # Query current forecast from pv_forecast bucket
        forecast_data = self._query_forecast(snapshot_start, snapshot_end)
        if forecast_data is None or forecast_data.empty:
            logger.error("No forecast data available for snapshot")
            return False

        # Get the run_time from the forecast
        forecast_run_time = forecast_data.get("run_time", [None])[0] if "run_time" in forecast_data.columns else None
        if not forecast_run_time:
            forecast_run_time = decision_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Write snapshot data per string
        points = []
        for string_config in self.STRINGS:
            string_name = string_config["string"]
            inverter_name = string_config["inverter"]

            # Filter data for this string/inverter
            string_data = self._filter_forecast_by_string(forecast_data, string_name, inverter_name)

            for idx, row in string_data.iterrows():
                timestamp = idx if isinstance(idx, datetime) else pd.Timestamp(idx)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize(timezone.utc)

                point = (
                    Point("pv_forecast_snapshot")
                    .tag("snapshot_type", snapshot_type)
                    .tag("snapshot_id", snapshot_id)
                    .tag("inverter", inverter_name)
                    .tag("string", string_name)
                    .tag("forecast_run_time", forecast_run_time)
                    .field("forecast_wh_p10", float(row.get("energy_wh_p10", 0)))
                    .field("forecast_wh_p50", float(row.get("energy_wh_p50", 0)))
                    .field("forecast_wh_p90", float(row.get("energy_wh_p90", 0)))
                    .time(timestamp, WritePrecision.S)
                )
                points.append(point)

        # Write snapshot metadata (decision context)
        soc = self._get_ha_numeric_value(self.soc_entity)
        discharge_power = self._get_ha_numeric_value(self.discharge_control_entity)
        discharge_blocked = discharge_power is not None and discharge_power == 0

        meta_point = (
            Point("pv_forecast_snapshot_meta")
            .tag("snapshot_type", snapshot_type)
            .tag("snapshot_id", snapshot_id)
            .field("soc_at_decision", soc if soc is not None else 0.0)
            .field("decision_discharge_blocked", discharge_blocked)
            .field("forecast_run_time", forecast_run_time)
            .time(decision_time, WritePrecision.S)
        )
        points.append(meta_point)

        # Write all points
        if points:
            logger.info(f"Writing {len(points)} snapshot points to InfluxDB")
            self.write_api.write(bucket=self.pv_bucket, org=self.influx_org, record=points)
            logger.info(f"Forecast snapshot {snapshot_id} written successfully")
            return True

        return False

    def _query_actuals(
        self, entity_id: str, start: datetime, end: datetime
    ) -> Optional[pd.DataFrame]:
        """
        Query actual PV production from HomeAssistant InfluxDB bucket.

        Uses aggregateWindow to align to 15-min periods, then converts
        mean power (W) to energy (Wh) per period.

        Returns DataFrame with 'actual_wh' column indexed by time.
        """
        start_str = start.isoformat()
        end_str = end.isoformat()

        query = f'''
        from(bucket: "HomeAssistant")
          |> range(start: {start_str}, stop: {end_str})
          |> filter(fn: (r) => r._measurement == "W")
          |> filter(fn: (r) => r.entity_id == "{entity_id}")
          |> filter(fn: (r) => r._field == "value")
          |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
          |> map(fn: (r) => ({{r with _value: r._value * 0.25}}))
          |> yield(name: "actual_wh")
        '''

        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                logger.warning(f"No actual data for entity {entity_id}")
                return None

            if "_time" in result.columns:
                result.set_index("_time", inplace=True)

            df = pd.DataFrame(index=result.index)
            df["actual_wh"] = result["_value"]
            return df
        except Exception as e:
            logger.error(
                f"Failed to query actuals for {entity_id}: {e}. "
                "Check that the InfluxDB token has read access to the HomeAssistant bucket."
            )
            return None

    def _query_snapshot_for_inverter(
        self, inverter: str, snapshot_id: str, start: datetime, end: datetime
    ) -> Optional[pd.DataFrame]:
        """
        Query snapshot forecast data for a specific inverter.

        For 'total': query string=="total" directly and pivot.
        For 'EastWest'/'South': sum across strings using group() + sum(), then pivot.

        Returns DataFrame with forecast_wh_p10/p50/p90 columns indexed by time.
        """
        start_str = start.isoformat()
        end_str = end.isoformat()

        if inverter == "total":
            query = f'''
            from(bucket: "{self.pv_bucket}")
              |> range(start: {start_str}, stop: {end_str})
              |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot")
              |> filter(fn: (r) => r.snapshot_id == "{snapshot_id}")
              |> filter(fn: (r) => r.inverter == "total")
              |> filter(fn: (r) => r.string == "total")
              |> filter(fn: (r) => r._field =~ /^forecast_wh_p(10|50|90)$/)
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''
        else:
            # Sum across strings for this inverter (e.g. East+West, SouthFront+SouthBack)
            query = f'''
            from(bucket: "{self.pv_bucket}")
              |> range(start: {start_str}, stop: {end_str})
              |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot")
              |> filter(fn: (r) => r.snapshot_id == "{snapshot_id}")
              |> filter(fn: (r) => r.inverter == "{inverter}")
              |> filter(fn: (r) => r._field =~ /^forecast_wh_p(10|50|90)$/)
              |> group(columns: ["_time", "_field"])
              |> sum()
              |> group()
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''

        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                logger.warning(f"No snapshot data for inverter={inverter}, snapshot_id={snapshot_id}")
                return None

            if "_time" in result.columns:
                result.set_index("_time", inplace=True)

            df = pd.DataFrame(index=result.index)
            for col in ["forecast_wh_p10", "forecast_wh_p50", "forecast_wh_p90"]:
                df[col] = result[col] if col in result.columns else 0.0
            return df
        except Exception as e:
            logger.error(f"Failed to query snapshot for inverter={inverter}: {e}")
            return None

    def _query_snapshot_for_string(
        self, string_name: str, snapshot_id: str, start: datetime, end: datetime
    ) -> Optional[pd.DataFrame]:
        """
        Query snapshot forecast data for a specific string (e.g. East, West).

        Returns DataFrame with forecast_wh_p10/p50/p90 columns indexed by time.
        """
        start_str = start.isoformat()
        end_str = end.isoformat()

        query = f'''
        from(bucket: "{self.pv_bucket}")
          |> range(start: {start_str}, stop: {end_str})
          |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot")
          |> filter(fn: (r) => r.snapshot_id == "{snapshot_id}")
          |> filter(fn: (r) => r.string == "{string_name}")
          |> filter(fn: (r) => r._field =~ /^forecast_wh_p(10|50|90)$/)
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''

        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                logger.warning(f"No snapshot data for string={string_name}, snapshot_id={snapshot_id}")
                return None

            if "_time" in result.columns:
                result.set_index("_time", inplace=True)

            df = pd.DataFrame(index=result.index)
            for col in ["forecast_wh_p10", "forecast_wh_p50", "forecast_wh_p90"]:
                df[col] = result[col] if col in result.columns else 0.0
            return df
        except Exception as e:
            logger.error(f"Failed to query snapshot for string={string_name}: {e}")
            return None

    def evaluate_forecast(self, evaluation_time: Optional[datetime] = None) -> bool:
        """
        Evaluate yesterday's snapshot forecast against actual PV production.

        Called at 21:15 daily. Compares the snapshot taken at yesterday's 21:00
        with actual production data from the HomeAssistant InfluxDB bucket.

        Two-layer calibration: decomposes forecast error into weather error
        (MeteoSwiss GHI forecast was wrong) and model error (PV model parameters
        are wrong per-string).

        Args:
            evaluation_time: Override evaluation time (default: now). Used for testing.

        Returns:
            True if evaluation was successful
        """
        if evaluation_time is None:
            evaluation_time = datetime.now(timezone.utc)

        # Convert to local timezone to determine snapshot timing
        # Snapshot is created at 21:00 LOCAL time, so we need to match that
        eval_local = evaluation_time.astimezone(self.local_tz)
        yesterday_local = eval_local - timedelta(days=1)

        # Snapshot ID = yesterday's date in local timezone
        snapshot_id = yesterday_local.strftime("%Y-%m-%d")

        # Snapshot period: yesterday 21:00 LOCAL → today 21:00 LOCAL (converted to UTC)
        snapshot_start_local = yesterday_local.replace(hour=21, minute=0, second=0, microsecond=0)
        snapshot_end_local = eval_local.replace(hour=21, minute=0, second=0, microsecond=0)

        # Convert to UTC for InfluxDB queries
        snapshot_start = snapshot_start_local.astimezone(timezone.utc)
        snapshot_end = snapshot_end_local.astimezone(timezone.utc)

        logger.info(
            f"Evaluating forecast accuracy for snapshot {snapshot_id} "
            f"({snapshot_start.isoformat()} to {snapshot_end.isoformat()})"
        )

        snapshot_type = "battery_21h"
        total_points = 0

        # Collect joined DataFrames for weather factor computation
        # Keys: "East", "West", "South" (inverter-level for South = SouthFront+SouthBack)
        string_joined: Dict[str, pd.DataFrame] = {}
        inverter_joined: Dict[str, pd.DataFrame] = {}

        # --- Phase A+B: Query data and write basic accuracy points ---

        for inverter, entity_id in self.ACTUAL_ENTITIES.items():
            # Query snapshot forecast
            snapshot_df = self._query_snapshot_for_inverter(
                inverter, snapshot_id, snapshot_start, snapshot_end
            )
            if snapshot_df is None or snapshot_df.empty:
                logger.warning(f"No snapshot data for inverter={inverter}, skipping")
                continue

            # Query actuals
            actuals_df = self._query_actuals(entity_id, snapshot_start, snapshot_end)
            if actuals_df is None or actuals_df.empty:
                logger.warning(f"No actual data for inverter={inverter}, skipping")
                continue

            # Inner join on timestamp
            joined = snapshot_df.join(actuals_df, how="inner")
            if joined.empty:
                logger.warning(f"No overlapping timestamps for inverter={inverter}")
                continue

            # Drop rows where actual_wh is NaN
            joined = joined.dropna(subset=["actual_wh"])

            # Calculate error
            joined["error_wh"] = joined["forecast_wh_p50"] - joined["actual_wh"]

            # Store for weather factor computation
            inverter_joined[inverter] = joined

            # Write pv_accuracy points
            points = []
            for idx, row in joined.iterrows():
                timestamp = idx if isinstance(idx, datetime) else pd.Timestamp(idx)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize(timezone.utc)

                point = (
                    Point("pv_accuracy")
                    .tag("snapshot_type", snapshot_type)
                    .tag("snapshot_id", snapshot_id)
                    .tag("inverter", inverter)
                    .tag("string", inverter)
                    .field("forecast_wh_p10", float(row.get("forecast_wh_p10", 0)))
                    .field("forecast_wh_p50", float(row.get("forecast_wh_p50", 0)))
                    .field("forecast_wh_p90", float(row.get("forecast_wh_p90", 0)))
                    .field("actual_wh", float(row["actual_wh"]))
                    .field("error_wh", float(row["error_wh"]))
                    .time(timestamp, WritePrecision.S)
                )
                points.append(point)

            if points:
                self.write_api.write(
                    bucket=self.pv_bucket, org=self.influx_org, record=points
                )
                total_points += len(points)
                logger.info(
                    f"Wrote {len(points)} accuracy points for inverter={inverter}"
                )

        # Per-string evaluation (East, West) where individual actuals are available
        for string_name, entity_id in self.STRING_ACTUAL_ENTITIES.items():
            inverter = [s["inverter"] for s in self.STRINGS if s["string"] == string_name][0]

            snapshot_df = self._query_snapshot_for_string(
                string_name, snapshot_id, snapshot_start, snapshot_end
            )
            if snapshot_df is None or snapshot_df.empty:
                logger.warning(f"No snapshot data for string={string_name}, skipping")
                continue

            actuals_df = self._query_actuals(entity_id, snapshot_start, snapshot_end)
            if actuals_df is None or actuals_df.empty:
                logger.warning(f"No actual data for string={string_name}, skipping")
                continue

            joined = snapshot_df.join(actuals_df, how="inner")
            if joined.empty:
                logger.warning(f"No overlapping timestamps for string={string_name}")
                continue

            joined = joined.dropna(subset=["actual_wh"])
            joined["error_wh"] = joined["forecast_wh_p50"] - joined["actual_wh"]

            # Store for weather factor computation
            string_joined[string_name] = joined

            points = []
            for idx, row in joined.iterrows():
                timestamp = idx if isinstance(idx, datetime) else pd.Timestamp(idx)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize(timezone.utc)

                point = (
                    Point("pv_accuracy")
                    .tag("snapshot_type", snapshot_type)
                    .tag("snapshot_id", snapshot_id)
                    .tag("inverter", inverter)
                    .tag("string", string_name)
                    .field("forecast_wh_p10", float(row.get("forecast_wh_p10", 0)))
                    .field("forecast_wh_p50", float(row.get("forecast_wh_p50", 0)))
                    .field("forecast_wh_p90", float(row.get("forecast_wh_p90", 0)))
                    .field("actual_wh", float(row["actual_wh"]))
                    .field("error_wh", float(row["error_wh"]))
                    .time(timestamp, WritePrecision.S)
                )
                points.append(point)

            if points:
                self.write_api.write(
                    bucket=self.pv_bucket, org=self.influx_org, record=points
                )
                total_points += len(points)
                logger.info(
                    f"Wrote {len(points)} accuracy points for string={string_name}"
                )

        # --- Phase C: Compute weather factor ---
        # weather_factor = sum(actual_wh across East+West+South) / sum(forecast_wh_p50 across East+West+South)
        # Use East + West individual strings + South inverter to cover all strings without double-counting

        calibration_sources = {}
        if "East" in string_joined:
            calibration_sources["East"] = string_joined["East"]
        if "West" in string_joined:
            calibration_sources["West"] = string_joined["West"]
        if "South" in inverter_joined:
            calibration_sources["South"] = inverter_joined["South"]

        weather_factor_series = None
        if len(calibration_sources) >= 2:
            # Sum forecast and actual across all available strings per timestamp
            # Use intersection of timestamps where all sources have data
            common_index = None
            for df in calibration_sources.values():
                if common_index is None:
                    common_index = df.index
                else:
                    common_index = common_index.intersection(df.index)

            if common_index is not None and len(common_index) > 0:
                sum_forecast = pd.Series(0.0, index=common_index)
                sum_actual = pd.Series(0.0, index=common_index)
                for df in calibration_sources.values():
                    sum_forecast += df.loc[common_index, "forecast_wh_p50"]
                    sum_actual += df.loc[common_index, "actual_wh"]

                # Compute weather factor, skip periods where forecast is zero
                weather_factor_series = pd.Series(index=common_index, dtype=float)
                nonzero_mask = sum_forecast > 0
                weather_factor_series[nonzero_mask] = (
                    sum_actual[nonzero_mask] / sum_forecast[nonzero_mask]
                )
                weather_factor_series[~nonzero_mask] = float("nan")

                logger.info(
                    f"Computed weather_factor for {nonzero_mask.sum()} periods "
                    f"(mean={weather_factor_series[nonzero_mask].mean():.3f})"
                )

        # --- Phase D: Write calibration fields ---
        if weather_factor_series is not None:
            calibration_points = 0

            # Write for individual strings (East, West) and South inverter
            calibration_targets = {
                "East": ("EastWest", string_joined.get("East")),
                "West": ("EastWest", string_joined.get("West")),
                "South": ("South", inverter_joined.get("South")),
                "total": ("total", inverter_joined.get("total")),
            }

            for tag_string, (tag_inverter, joined_df) in calibration_targets.items():
                if joined_df is None or joined_df.empty:
                    continue

                # Only use timestamps where we have both joined data and weather factor
                common_ts = joined_df.index.intersection(weather_factor_series.dropna().index)
                if len(common_ts) == 0:
                    continue

                points = []
                for ts in common_ts:
                    wf = float(weather_factor_series[ts])
                    forecast_p50 = float(joined_df.loc[ts, "forecast_wh_p50"])
                    actual = float(joined_df.loc[ts, "actual_wh"])

                    weather_adjusted_wh = forecast_p50 * wf
                    weather_error_wh = forecast_p50 - weather_adjusted_wh
                    model_error_wh = weather_adjusted_wh - actual

                    timestamp = ts if isinstance(ts, datetime) else pd.Timestamp(ts)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.tz_localize(timezone.utc)

                    point = (
                        Point("pv_accuracy")
                        .tag("snapshot_type", snapshot_type)
                        .tag("snapshot_id", snapshot_id)
                        .tag("inverter", tag_inverter)
                        .tag("string", tag_string)
                        .field("weather_factor", wf)
                        .field("weather_adjusted_wh", weather_adjusted_wh)
                        .field("weather_error_wh", weather_error_wh)
                        .field("model_error_wh", model_error_wh)
                        .time(timestamp, WritePrecision.S)
                    )
                    points.append(point)

                if points:
                    self.write_api.write(
                        bucket=self.pv_bucket, org=self.influx_org, record=points
                    )
                    calibration_points += len(points)
                    logger.info(
                        f"Wrote {len(points)} calibration points for string={tag_string}"
                    )

            if calibration_points > 0:
                total_points += calibration_points
                logger.info(f"Calibration: {calibration_points} total calibration points written")
        else:
            logger.warning("Insufficient data for weather factor computation (need at least 2 of East/West/South)")

        if total_points > 0:
            logger.info(
                f"Evaluation complete for snapshot {snapshot_id}: "
                f"{total_points} total points written"
            )
            return True
        else:
            logger.warning(f"No accuracy data written for snapshot {snapshot_id}")
            return False

    def _query_forecast(self, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        """Query current PV forecast from InfluxDB."""
        start_str = start.isoformat()
        end_str = end.isoformat()

        query = f'''
        from(bucket: "{self.pv_bucket}")
          |> range(start: {start_str}, stop: {end_str})
          |> filter(fn: (r) => r._measurement == "pv_forecast")
          |> filter(fn: (r) => r._field =~ /^(energy_wh_p10|energy_wh_p50|energy_wh_p90|power_w_p10|power_w_p50|power_w_p90)$/)
          |> pivot(rowKey:["_time"], columnKey: ["_field", "inverter"], valueColumn: "_value")
        '''

        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            if result.empty:
                return None

            if "_time" in result.columns:
                result.set_index("_time", inplace=True)

            return result
        except Exception as e:
            logger.error(f"Failed to query forecast: {e}")
            return None

    def _filter_forecast_by_string(
        self, forecast: pd.DataFrame, string_name: str, inverter_name: str
    ) -> pd.DataFrame:
        """
        Filter forecast data for a specific string.

        The forecast data has columns like:
        - energy_wh_p50_total, energy_wh_p50_EastWest, energy_wh_p50_South
        - For individual strings, we use inverter-level data (per-string not stored separately)
        """
        result = pd.DataFrame(index=forecast.index)

        if string_name == "total":
            col_suffix = "_total"
        elif string_name in ["East", "West"]:
            col_suffix = "_EastWest"
        elif string_name in ["SouthFront", "SouthBack"]:
            col_suffix = "_South"
        else:
            col_suffix = f"_{inverter_name}"

        for percentile in ["p10", "p50", "p90"]:
            energy_col = f"energy_wh_{percentile}{col_suffix}"
            if energy_col in forecast.columns:
                result[f"energy_wh_{percentile}"] = forecast[energy_col]
            else:
                alt_col = f"energy_wh_{percentile}_total"
                if alt_col in forecast.columns:
                    result[f"energy_wh_{percentile}"] = forecast[alt_col]
                else:
                    bare_col = f"energy_wh_{percentile}"
                    if bare_col in forecast.columns:
                        result[f"energy_wh_{percentile}"] = forecast[bare_col]
                    else:
                        result[f"energy_wh_{percentile}"] = 0.0

        return result


def create_accuracy_tracker(options: Dict) -> AccuracyTracker:
    """Factory function to create AccuracyTracker from options dict."""
    influx_config = options.get("influxdb", {})
    accuracy_config = options.get("accuracy_tracker", {})

    return AccuracyTracker(
        influx_host=influx_config.get("host", "192.168.0.203"),
        influx_port=influx_config.get("port", 8087),
        influx_token=influx_config.get("token", ""),
        influx_org=influx_config.get("org", "energymanagement"),
        pv_bucket=influx_config.get("bucket", "pv_forecast"),
        ha_url=accuracy_config.get("ha_url", "http://supervisor/core"),
        soc_entity=accuracy_config.get("soc_entity", "sensor.battery_state_of_capacity"),
        discharge_control_entity=accuracy_config.get(
            "discharge_control_entity", "number.battery_maximum_discharging_power"
        ),
        local_timezone=options.get("timezone", "Europe/Zurich"),
    )
