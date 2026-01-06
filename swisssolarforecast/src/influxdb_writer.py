"""
InfluxDB writer for PV forecast data.

Writes forecast results with future timestamps to InfluxDB.
Supports P10/P50/P90 percentiles from ensemble forecasts.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)


class ForecastWriter:
    """Writes PV forecast data to InfluxDB."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        org: str,
        bucket: str,
    ):
        self.host = host
        self.port = port
        self.token = token
        self.org = org
        self.bucket = bucket
        self.client: Optional[InfluxDBClient] = None
        self.write_api = None

    def connect(self):
        """Connect to InfluxDB."""
        url = f"http://{self.host}:{self.port}"
        logger.info(f"Connecting to InfluxDB at {url}")

        self.client = InfluxDBClient(
            url=url,
            token=self.token,
            org=self.org,
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

        # Verify connection
        try:
            health = self.client.health()
            logger.info(f"InfluxDB connection: {health.status}")
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            raise

    def close(self):
        """Close InfluxDB connection."""
        if self.client:
            self.client.close()
            logger.info("InfluxDB connection closed")

    def ensure_bucket(self, retention_days: int = 30):
        """Create bucket if it doesn't exist."""
        buckets_api = self.client.buckets_api()

        # Check if bucket exists
        existing = buckets_api.find_bucket_by_name(self.bucket)
        if existing:
            logger.debug(f"Bucket '{self.bucket}' already exists")
            return

        # Create bucket with retention
        retention_seconds = retention_days * 24 * 60 * 60
        logger.info(f"Creating bucket '{self.bucket}' with {retention_days} day retention")

        buckets_api.create_bucket(
            bucket_name=self.bucket,
            org=self.org,
            retention_rules=[{
                "type": "expire",
                "everySeconds": retention_seconds,
            }]
        )

    def delete_future_forecasts(self, start_time: datetime):
        """Delete existing forecasts from start_time onwards (before writing new)."""
        delete_api = self.client.delete_api()

        # Delete from start_time to far future
        stop_time = datetime(2100, 1, 1, tzinfo=timezone.utc)

        logger.info(f"Deleting existing forecasts from {start_time}")
        delete_api.delete(
            start=start_time,
            stop=stop_time,
            predicate='_measurement="pv_forecast"',
            bucket=self.bucket,
            org=self.org,
        )

    def write_forecast(
        self,
        forecast: pd.DataFrame,
        model: str = "hybrid",
        run_time: Optional[datetime] = None,
    ):
        """
        Write forecast DataFrame to InfluxDB.

        Args:
            forecast: DataFrame with columns like total_ac_power_p10/p50/p90, ghi, etc.
                     Index must be datetime (future timestamps)
            model: Model identifier (ch1, ch2, hybrid)
            run_time: When this forecast was calculated (defaults to now)
        """
        if run_time is None:
            run_time = datetime.now(timezone.utc)

        run_time_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Delete existing future forecasts first
        if len(forecast) > 0:
            first_time = forecast.index.min()
            if hasattr(first_time, 'tzinfo') and first_time.tzinfo is None:
                first_time = first_time.replace(tzinfo=timezone.utc)
            self.delete_future_forecasts(first_time)

        points = []

        for timestamp, row in forecast.iterrows():
            # Ensure timestamp is timezone-aware
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            # Write P10/P50/P90 as separate points with percentile tag
            for percentile in ["p10", "p50", "p90"]:
                power_col = f"total_ac_power_{percentile}"
                if power_col not in row:
                    continue

                point = (
                    Point("pv_forecast")
                    .tag("percentile", percentile.upper())
                    .tag("inverter", "total")
                    .tag("model", model)
                    .tag("run_time", run_time_str)
                    .field("power_w", float(row[power_col]))
                    .time(timestamp, WritePrecision.S)
                )

                # Add GHI if available
                if "ghi" in row and pd.notna(row["ghi"]):
                    point = point.field("ghi", float(row["ghi"]))

                # Add temperature if available
                if "temp_air" in row and pd.notna(row["temp_air"]):
                    point = point.field("temp_air", float(row["temp_air"]))

                points.append(point)

            # Write per-inverter data if available
            for col in row.index:
                if "_ac_power_p50" in col and col != "total_ac_power_p50":
                    inverter_name = col.replace("_ac_power_p50", "")

                    for percentile in ["p10", "p50", "p90"]:
                        inv_col = f"{inverter_name}_ac_power_{percentile}"
                        if inv_col not in row:
                            continue

                        point = (
                            Point("pv_forecast")
                            .tag("percentile", percentile.upper())
                            .tag("inverter", inverter_name)
                            .tag("model", model)
                            .tag("run_time", run_time_str)
                            .field("power_w", float(row[inv_col]))
                            .time(timestamp, WritePrecision.S)
                        )
                        points.append(point)

        # Write all points
        if points:
            logger.info(f"Writing {len(points)} forecast points to InfluxDB")
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.info("Forecast written successfully")
        else:
            logger.warning("No forecast data to write")

    def write_metadata(self, key: str, value: str):
        """Write metadata point (e.g., last update time, status)."""
        point = (
            Point("pv_forecast_metadata")
            .tag("key", key)
            .field("value", value)
            .time(datetime.now(timezone.utc), WritePrecision.S)
        )
        self.write_api.write(bucket=self.bucket, org=self.org, record=point)
