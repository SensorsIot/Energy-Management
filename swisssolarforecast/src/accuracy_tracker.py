"""
Forecast accuracy tracking for SwissSolarForecast add-on.

Implements FSD Chapter 5.3: Forecast Accuracy #1 - Battery Discharge Optimization.

Phase 1: Snapshot only (21:00 daily local time)
- Captures forecast for next 24h
- Records decision context (SOC, discharge status)

Phase 2 (future): Evaluation at 21:15 comparing snapshots with actuals
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

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
        """
        self.influx_host = influx_host
        self.influx_port = influx_port
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.pv_bucket = pv_bucket
        self.ha_url = ha_url.rstrip("/")
        self.soc_entity = soc_entity
        self.discharge_control_entity = discharge_control_entity

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
    )
