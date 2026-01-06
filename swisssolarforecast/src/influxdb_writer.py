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

    def _resample_forecast(self, forecast: pd.DataFrame, minutes: int = 15) -> pd.DataFrame:
        """
        Resample forecast to specified minute intervals using interpolation.

        Aligns timestamps to exact 15-min boundaries (00, 15, 30, 45)
        for synchronization with actual consumption data.
        """
        if len(forecast) < 2:
            return forecast

        # Get start/end aligned to minute boundaries
        start = forecast.index.min().floor(f'{minutes}min')
        end = forecast.index.max().ceil(f'{minutes}min')

        # Create new index at exact intervals
        new_index = pd.date_range(start=start, end=end, freq=f'{minutes}min')

        # Reindex and interpolate
        forecast_resampled = forecast.reindex(
            forecast.index.union(new_index)
        ).interpolate(method='linear').reindex(new_index)

        # Ensure timezone
        if forecast_resampled.index.tzinfo is None:
            forecast_resampled.index = forecast_resampled.index.tz_localize('UTC')

        logger.debug(f"Resampled forecast from {len(forecast)} to {len(forecast_resampled)} points ({minutes} min)")
        return forecast_resampled

    def write_forecast(
        self,
        forecast: pd.DataFrame,
        model: str = "hybrid",
        run_time: Optional[datetime] = None,
        resample_minutes: int = 15,
    ):
        """
        Write forecast DataFrame to InfluxDB.

        Args:
            forecast: DataFrame with columns like total_ac_power_p10/p50/p90, ghi, etc.
                     Index must be datetime (future timestamps)
            model: Model identifier (ch1, ch2, hybrid)
            run_time: When this forecast was calculated (defaults to now)
            resample_minutes: Resample to this interval (default 15 min for MPC)
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

        # Resample to finer resolution (15 min) for MPC optimizer
        forecast = self._resample_forecast(forecast, resample_minutes)

        # Time step in hours for energy integration (15 min = 0.25 h)
        time_diff = resample_minutes / 60.0

        # Calculate cumulative energy for each percentile
        # Energy is ever-increasing: cumsum of power × time_step
        cumulative_energy = {}
        for percentile in ["p10", "p50", "p90"]:
            power_col = f"total_ac_power_{percentile}"
            if power_col in forecast.columns:
                # Energy (Wh) = cumulative sum of power (W) × time_step (h)
                # This gives monotonically increasing energy at each 15-min interval
                cumulative_energy[percentile] = (forecast[power_col].cumsum() * time_diff).values

        points = []

        for idx, (timestamp, row) in enumerate(forecast.iterrows()):
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
                )

                # Add cumulative energy
                if percentile in cumulative_energy:
                    point = point.field("energy_wh", float(cumulative_energy[percentile][idx]))

                # Add GHI if available
                if "ghi" in row and pd.notna(row["ghi"]):
                    point = point.field("ghi", float(row["ghi"]))

                # Add temperature if available
                if "temp_air" in row and pd.notna(row["temp_air"]):
                    point = point.field("temp_air", float(row["temp_air"]))

                point = point.time(timestamp, WritePrecision.S)
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

    def query_load_forecast(
        self,
        start_time: datetime,
        end_time: datetime,
        source_bucket: str = "load_forecast",
    ) -> pd.DataFrame:
        """
        Query load forecast for net calculation.

        Args:
            start_time: Start of query range
            end_time: End of query range
            source_bucket: Bucket containing load forecast

        Returns:
            DataFrame with load_power_w column indexed by timestamp
        """
        query_api = self.client.query_api()

        query = f'''
        from(bucket: "{source_bucket}")
          |> range(start: {start_time.isoformat()}, stop: {end_time.isoformat()})
          |> filter(fn: (r) => r._measurement == "load_forecast")
          |> filter(fn: (r) => r._field == "power_w")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''

        try:
            result = query_api.query_data_frame(query)
            if result.empty:
                logger.warning("No load forecast data found")
                return pd.DataFrame()

            result = result.set_index("_time")
            result = result.rename(columns={"power_w": "load_power_w"})
            return result[["load_power_w"]]
        except Exception as e:
            logger.warning(f"Could not query load forecast: {e}")
            return pd.DataFrame()

    def write_energy_balance(
        self,
        pv_forecast: pd.DataFrame,
        load_forecast: Optional[pd.DataFrame] = None,
        model: str = "hybrid",
        run_time: Optional[datetime] = None,
        resample_minutes: int = 15,
    ):
        """
        Write energy balance to InfluxDB with aligned timestamps.

        Stores PV forecast, load forecast, and net difference (available - used).
        All values are at exact 15-min boundaries for MPC synchronization.

        Args:
            pv_forecast: PV power forecast (P10/P50/P90)
            load_forecast: Load/consumption forecast (optional)
            model: Model identifier
            run_time: Forecast calculation time
            resample_minutes: Time resolution (default 15 min)
        """
        if run_time is None:
            run_time = datetime.now(timezone.utc)

        run_time_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Resample PV forecast to 15-min intervals
        pv_forecast = self._resample_forecast(pv_forecast, resample_minutes)

        if len(pv_forecast) == 0:
            logger.warning("No PV forecast data to write")
            return

        # Delete existing forecasts
        first_time = pv_forecast.index.min()
        if hasattr(first_time, 'tzinfo') and first_time.tzinfo is None:
            first_time = first_time.replace(tzinfo=timezone.utc)
        self.delete_future_forecasts(first_time)

        # Time step for energy integration
        time_diff = resample_minutes / 60.0

        # Resample load forecast to match PV forecast timestamps if available
        if load_forecast is not None and len(load_forecast) > 0:
            load_forecast = load_forecast.reindex(pv_forecast.index, method='nearest')
        else:
            # No load forecast - create zeros
            load_forecast = pd.DataFrame(
                {"load_power_w": 0.0},
                index=pv_forecast.index
            )

        # Calculate cumulative energy
        pv_cumulative = {}
        load_cumulative = (load_forecast["load_power_w"].cumsum() * time_diff).values

        for percentile in ["p10", "p50", "p90"]:
            power_col = f"total_ac_power_{percentile}"
            if power_col in pv_forecast.columns:
                pv_cumulative[percentile] = (pv_forecast[power_col].cumsum() * time_diff).values

        points = []

        for idx, timestamp in enumerate(pv_forecast.index):
            # Ensure timestamp is timezone-aware
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            row = pv_forecast.loc[timestamp]
            load_power = float(load_forecast.loc[timestamp, "load_power_w"]) if "load_power_w" in load_forecast.columns else 0.0
            load_energy = float(load_cumulative[idx])

            # Single point per timestamp with ALL percentile values
            # This guarantees exact same timestamp for P10/P50/P90
            point = (
                Point("pv_forecast")
                .tag("inverter", "total")
                .tag("model", model)
                .tag("run_time", run_time_str)
                .field("load_power_w", load_power)
                .field("load_energy_wh", load_energy)
            )

            # Add P10/P50/P90 values as separate fields
            for percentile in ["p10", "p50", "p90"]:
                power_col = f"total_ac_power_{percentile}"
                if power_col not in row:
                    continue

                pv_power = float(row[power_col])
                pv_energy = float(pv_cumulative[percentile][idx]) if percentile in pv_cumulative else 0.0
                net_power = pv_power - load_power
                net_energy = pv_energy - load_energy

                # Field names: power_w_p10, power_w_p50, power_w_p90, etc.
                point = point.field(f"power_w_{percentile}", pv_power)
                point = point.field(f"energy_wh_{percentile}", pv_energy)
                point = point.field(f"net_power_w_{percentile}", net_power)
                point = point.field(f"net_energy_wh_{percentile}", net_energy)

            # Add weather data if available
            if "ghi" in row and pd.notna(row["ghi"]):
                point = point.field("ghi", float(row["ghi"]))
            if "temp_air" in row and pd.notna(row["temp_air"]):
                point = point.field("temp_air", float(row["temp_air"]))

            point = point.time(timestamp, WritePrecision.S)
            points.append(point)

            # Write per-inverter data if available (e.g., EastWest_ac_power_p50, South_ac_power_p50)
            inverter_names = set()
            for col in row.index:
                if "_ac_power_p50" in col and col != "total_ac_power_p50":
                    inv_name = col.replace("_ac_power_p50", "")
                    inverter_names.add(inv_name)

            for inv_name in inverter_names:
                inv_point = (
                    Point("pv_forecast")
                    .tag("inverter", inv_name)
                    .tag("model", model)
                    .tag("run_time", run_time_str)
                )

                # Add P10/P50/P90 values for this inverter
                for percentile in ["p10", "p50", "p90"]:
                    inv_col = f"{inv_name}_ac_power_{percentile}"
                    if inv_col in row and pd.notna(row[inv_col]):
                        inv_point = inv_point.field(f"power_w_{percentile}", float(row[inv_col]))

                inv_point = inv_point.time(timestamp, WritePrecision.S)
                points.append(inv_point)

        # Write all points
        if points:
            logger.info(f"Writing {len(points)} energy balance points to InfluxDB")
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.info("Energy balance written successfully")
        else:
            logger.warning("No energy balance data to write")
