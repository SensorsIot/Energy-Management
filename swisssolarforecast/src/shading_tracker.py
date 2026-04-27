"""Adaptive shading correction for PV forecast.

Stores observed shading ratios (actual/model) to InfluxDB after each sunny day.
Calculates aggregated shading factors from recent sunny days and writes to YAML.

Measurements stored in pv_forecast bucket:
- shading_observations: hour, string, ratio, weather_factor per day
"""

import logging
from datetime import datetime, UTC
from pathlib import Path

import yaml
import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Default shading factors (baseline from April 30, 2025 analysis)
DEFAULT_SHADING_FACTORS = {
    "East": {
        6: 0.20, 7: 0.25, 8: 0.55, 9: 0.60, 10: 0.65, 11: 0.67,
        12: 0.72, 13: 0.78, 14: 0.83, 15: 0.86, 16: 0.85, 17: 0.90, 18: 0.95
    },
    "West": {
        6: 0.65, 7: 0.75, 8: 0.82, 9: 0.89, 10: 0.95, 11: 0.93,
        12: 0.92, 13: 0.90, 14: 0.88, 15: 0.84, 16: 0.78, 17: 0.70, 18: 0.62
    },
    "South": {
        6: 0.45, 7: 0.45, 8: 0.60, 9: 0.70, 10: 0.90, 11: 0.93,
        12: 0.92, 13: 0.94, 14: 0.92, 15: 0.96, 16: 1.00, 17: 1.00, 18: 1.00
    },
}

# Minimum weather factor to consider a day "sunny"
SUNNY_THRESHOLD = 0.90

# Number of recent sunny days to average for shading factors
NUM_SUNNY_DAYS = 10


class ShadingTracker:
    """Tracks shading observations and calculates correction factors."""

    def __init__(
        self,
        influx_host: str,
        influx_port: int,
        influx_token: str,
        influx_org: str,
        bucket: str = "pv_forecast",
        local_timezone: str = "Europe/Zurich",
        shading_yaml_path: str | None = None,
    ) -> None:
        self.influx_host = influx_host
        self.influx_port = influx_port
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.bucket = bucket
        self.local_tz = ZoneInfo(local_timezone)

        # Path to shading factors YAML
        if shading_yaml_path:
            self.shading_yaml_path = Path(shading_yaml_path)
        else:
            # Default: alongside config_pv.yaml
            self.shading_yaml_path = Path(__file__).parent.parent / "shading_factors.yaml"

        self.client: InfluxDBClient | None = None
        self.write_api = None
        self.query_api = None

    def connect(self) -> None:
        """Connect to InfluxDB."""
        url = f"http://{self.influx_host}:{self.influx_port}"
        self.client = InfluxDBClient(
            url=url, token=self.influx_token, org=self.influx_org
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()
        logger.info(f"ShadingTracker connected to InfluxDB at {url}")

    def close(self) -> None:
        """Close InfluxDB connection."""
        if self.client:
            self.client.close()

    def store_shading_observation(
        self,
        snapshot_id: str,
        string_name: str,
        hour: int,
        ratio: float,
        weather_factor: float,
    ) -> None:
        """Store a single shading observation to InfluxDB.

        Args:
            snapshot_id: Date string (YYYY-MM-DD)
            string_name: East, West, or South
            hour: Local hour (0-23)
            ratio: Observed actual/model ratio
            weather_factor: Weather factor for the day

        """
        point = (
            Point("shading_observations")
            .tag("snapshot_id", snapshot_id)
            .tag("string", string_name)
            .field("hour", hour)
            .field("ratio", float(ratio))
            .field("weather_factor", float(weather_factor))
            .time(datetime.now(UTC), WritePrecision.S)
        )
        self.write_api.write(bucket=self.bucket, org=self.influx_org, record=point)

    def process_accuracy_data(self, snapshot_id: str) -> bool:
        """Process accuracy data for a snapshot and store shading observations.

        Stores ratios for ALL days (for analysis), returns True if sunny day
        (so caller knows to recalculate factors).

        Args:
            snapshot_id: Date string (YYYY-MM-DD)

        Returns:
            True if this was a sunny day (weather_factor > threshold)

        """
        # Query accuracy data with weather factor
        query = f'''
        from(bucket: "{self.bucket}")
          |> range(start: -7d)
          |> filter(fn: (r) => r._measurement == "pv_accuracy")
          |> filter(fn: (r) => r.snapshot_id == "{snapshot_id}")
          |> filter(fn: (r) => r._field == "weather_factor" or r._field == "forecast_wh_p50" or r._field == "actual_wh")
          |> pivot(rowKey: ["_time", "string"], columnKey: ["_field"], valueColumn: "_value")
        '''

        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()

            if result.empty:
                logger.warning(f"No accuracy data for snapshot {snapshot_id}")
                return False

            # Get average weather factor
            if "weather_factor" not in result.columns:
                logger.warning("No weather_factor in accuracy data")
                return False

            avg_weather = result["weather_factor"].dropna().mean()
            is_sunny = avg_weather >= SUNNY_THRESHOLD

            logger.info(
                f"Processing {snapshot_id} (weather_factor={avg_weather:.2f}, "
                f"{'sunny' if is_sunny else 'cloudy'})"
            )

            # Store ratios for ALL days (for analysis)
            observations_stored = 0

            for string_name in ["East", "West", "South"]:
                string_data = result[result["string"] == string_name]

                for _, row in string_data.iterrows():
                    ts = row.get("_time")
                    if ts is None:
                        continue

                    # Get local hour
                    if hasattr(ts, "astimezone"):
                        local_time = ts.astimezone(self.local_tz)
                    else:
                        local_time = pd.Timestamp(ts).tz_localize("UTC").tz_convert(self.local_tz)

                    hour = local_time.hour
                    forecast = row.get("forecast_wh_p50", 0)
                    actual = row.get("actual_wh", 0)
                    wf = row.get("weather_factor", avg_weather)

                    # Only store if significant production
                    if forecast > 50 and actual > 0:
                        ratio = actual / forecast
                        ratio = max(0.1, min(1.5, ratio))  # Clamp

                        self.store_shading_observation(
                            snapshot_id, string_name, hour, ratio, wf
                        )
                        observations_stored += 1

            logger.info(f"Stored {observations_stored} shading observations for {snapshot_id}")

            # Return True only if sunny (caller will recalculate factors)
            return is_sunny

        except Exception as e:
            logger.error(f"Failed to process accuracy data: {e}")
            return False

    def calculate_shading_factors(
        self, num_days: int = NUM_SUNNY_DAYS
    ) -> dict[str, dict[int, float]]:
        """Calculate shading factors from recent sunny day observations.

        Args:
            num_days: Number of recent sunny days to average

        Returns:
            Dict mapping string -> hour -> factor

        """
        # Query recent shading observations
        query = f'''
        from(bucket: "{self.bucket}")
          |> range(start: -90d)
          |> filter(fn: (r) => r._measurement == "shading_observations")
          |> filter(fn: (r) => r._field == "ratio" or r._field == "hour" or r._field == "weather_factor")
          |> pivot(rowKey: ["_time", "string", "snapshot_id"], columnKey: ["_field"], valueColumn: "_value")
          |> filter(fn: (r) => r.weather_factor >= {SUNNY_THRESHOLD})
        '''

        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()

            if result.empty:
                logger.info("No shading observations, using defaults")
                return DEFAULT_SHADING_FACTORS.copy()

            # Get unique sunny days and take most recent N
            sunny_days = result["snapshot_id"].unique()
            sunny_days = sorted(sunny_days, reverse=True)[:num_days]

            logger.info(f"Calculating factors from {len(sunny_days)} sunny days: {sunny_days}")

            # Filter to recent days
            result = result[result["snapshot_id"].isin(sunny_days)]

            # Calculate average ratio per hour/string
            factors = {}

            for string_name in ["East", "West", "South"]:
                string_data = result[result["string"] == string_name]

                if string_data.empty:
                    factors[string_name] = DEFAULT_SHADING_FACTORS.get(string_name, {})
                    continue

                hourly_factors = {}
                for hour in range(5, 21):
                    hour_data = string_data[string_data["hour"] == hour]
                    if not hour_data.empty:
                        avg_ratio = hour_data["ratio"].mean()
                        hourly_factors[hour] = round(max(0.1, min(1.2, avg_ratio)), 2)
                    else:
                        # Fall back to default
                        hourly_factors[hour] = DEFAULT_SHADING_FACTORS.get(string_name, {}).get(hour, 1.0)

                factors[string_name] = hourly_factors

            return factors

        except Exception as e:
            logger.error(f"Failed to calculate shading factors: {e}")
            return DEFAULT_SHADING_FACTORS.copy()

    def update_shading_yaml(self, num_days: int = NUM_SUNNY_DAYS) -> bool:
        """Calculate shading factors and write to YAML file.

        Args:
            num_days: Number of recent sunny days to average

        Returns:
            True if YAML was updated

        """
        factors = self.calculate_shading_factors(num_days)

        # Prepare YAML content
        yaml_content = {
            "shading_correction": {
                "description": "Shading factors by hour (local time). Updated automatically from sunny day observations.",
                "last_updated": datetime.now(self.local_tz).isoformat(),
                "num_sunny_days": num_days,
                "factors": factors,
            }
        }

        try:
            with open(self.shading_yaml_path, "w") as f:
                yaml.dump(yaml_content, f, default_flow_style=False, sort_keys=False)

            logger.info(f"Updated shading factors in {self.shading_yaml_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to write shading YAML: {e}")
            return False

    def load_shading_factors(self) -> dict[str, dict[int, float]]:
        """Load shading factors from YAML file.
        Falls back to defaults if file doesn't exist.
        """
        try:
            if self.shading_yaml_path.exists():
                with open(self.shading_yaml_path) as f:
                    data = yaml.safe_load(f)

                factors = data.get("shading_correction", {}).get("factors", {})
                if factors:
                    # Convert string keys to int for hours
                    return {
                        string: {int(h): v for h, v in hours.items()}
                        for string, hours in factors.items()
                    }
        except Exception as e:
            logger.warning(f"Failed to load shading YAML: {e}")

        return DEFAULT_SHADING_FACTORS.copy()

    def apply_shading_correction(
        self,
        power_series: pd.Series,
        string_name: str,
    ) -> pd.Series:
        """Apply shading correction to a power forecast series.

        Args:
            power_series: Power forecast with DatetimeIndex
            string_name: Name of the string (East, West, South)

        Returns:
            Corrected power series

        """
        factors = self.load_shading_factors()
        string_factors = factors.get(string_name, {})

        if not string_factors:
            return power_series

        corrected = power_series.copy()

        for idx in corrected.index:
            if hasattr(idx, "astimezone"):
                local_time = idx.astimezone(self.local_tz)
            else:
                local_time = pd.Timestamp(idx).tz_convert(self.local_tz)

            hour = local_time.hour
            factor = string_factors.get(hour, 1.0)
            corrected[idx] = corrected[idx] * factor

        return corrected


def create_shading_tracker(options: dict) -> ShadingTracker:
    """Factory function to create ShadingTracker from options dict."""
    influx_config = options.get("influxdb", {})

    return ShadingTracker(
        influx_host=influx_config.get("host", "192.168.0.203"),
        influx_port=influx_config.get("port", 8087),
        influx_token=influx_config.get("token", ""),
        influx_org=influx_config.get("org", "energymanagement"),
        bucket=influx_config.get("bucket", "pv_forecast"),
        local_timezone=options.get("timezone", "Europe/Zurich"),
    )
