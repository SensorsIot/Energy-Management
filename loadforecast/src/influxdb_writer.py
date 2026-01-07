"""
InfluxDB writer for load forecast data.

Writes P10/P50/P90 energy forecasts per 15-minute period.
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

    def delete_future_forecasts(self, start_time: datetime):
        """Delete existing forecasts from start_time onwards."""
        delete_api = self.client.delete_api()
        stop_time = datetime(2100, 1, 1, tzinfo=timezone.utc)

        logger.info(f"Deleting existing load forecasts from {start_time}")
        delete_api.delete(
            start=start_time,
            stop=stop_time,
            predicate='_measurement="load_forecast"',
            bucket=self.bucket,
            org=self.org,
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
            forecast: DataFrame with energy_wh_p10, energy_wh_p50, energy_wh_p90 columns
                     Each value = Wh consumed in that 15-min period
                     Index must be datetime (future timestamps)
            model: Model identifier
            run_time: When this forecast was calculated
        """
        if run_time is None:
            run_time = datetime.now(timezone.utc)

        run_time_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Delete existing forecasts
        if len(forecast) > 0:
            first_time = forecast.index.min()
            if hasattr(first_time, "tzinfo") and first_time.tzinfo is None:
                first_time = first_time.replace(tzinfo=timezone.utc)
            self.delete_future_forecasts(first_time)

        points = []
        for timestamp, row in forecast.iterrows():
            # Ensure timezone-aware
            if hasattr(timestamp, "tzinfo") and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            # Single point per timestamp with all percentile fields
            point = (
                Point("load_forecast")
                .tag("model", model)
                .tag("run_time", run_time_str)
                .field("energy_wh_p10", float(row["energy_wh_p10"]))
                .field("energy_wh_p50", float(row["energy_wh_p50"]))
                .field("energy_wh_p90", float(row["energy_wh_p90"]))
                .time(timestamp, WritePrecision.S)
            )
            points.append(point)

        if points:
            logger.info(f"Writing {len(points)} load forecast points to InfluxDB")
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.info("Load forecast written successfully")
        else:
            logger.warning("No forecast data to write")
