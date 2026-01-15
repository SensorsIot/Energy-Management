"""
InfluxDB writer for load forecast data.

Writes P10/P50/P90 power forecasts (W) at 15-minute intervals.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)


class LoadForecastWriter:
    """Write load forecasts to InfluxDB."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        org: str,
        bucket: str = "load_forecast",
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
        self.client = InfluxDBClient(url=url, token=self.token, org=self.org)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

        # Verify connection
        health = self.client.health()
        logger.info(f"InfluxDB connection: {health.status}")

    def close(self):
        """Close connection."""
        if self.client:
            self.client.close()
            logger.info("InfluxDB connection closed")

    def ensure_bucket(self, retention_days: int = 30):
        """Create bucket if it doesn't exist."""
        buckets_api = self.client.buckets_api()

        existing = buckets_api.find_bucket_by_name(self.bucket)
        if existing:
            logger.debug(f"Bucket '{self.bucket}' already exists")
            return

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

    def write_forecast(
        self,
        forecast: pd.DataFrame,
        model: str = "statistical",
        run_time: Optional[datetime] = None,
    ):
        """
        Write load forecast to InfluxDB.

        Args:
            forecast: DataFrame with power_w_p10, power_w_p50, power_w_p90 columns
                     Each value = instantaneous power (W) at that timestamp
                     Index must be datetime (future timestamps)
            model: Model identifier
            run_time: When this forecast was calculated
        """
        if run_time is None:
            run_time = datetime.now(timezone.utc)

        run_time_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # No need to delete - points overwrite on same measurement+tags+timestamp
        # when run_time is a field (not tag). This avoids InfluxDB delete API
        # performance issues (see FSD C.4).

        points = []
        for timestamp, row in forecast.iterrows():
            # Ensure timezone-aware
            if hasattr(timestamp, "tzinfo") and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            # Single point per timestamp with all percentile fields
            # Note: run_time is a field (not tag) so points overwrite on same timestamp+model
            point = (
                Point("load_forecast")
                .tag("model", model)
                .field("power_w_p10", float(row["power_w_p10"]))
                .field("power_w_p50", float(row["power_w_p50"]))
                .field("power_w_p90", float(row["power_w_p90"]))
                .field("run_time", run_time_str)
                .time(timestamp, WritePrecision.S)
            )
            points.append(point)

        if points:
            logger.info(f"Writing {len(points)} load forecast points to InfluxDB")
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.info("Load forecast written successfully")
        else:
            logger.warning("No forecast data to write")
