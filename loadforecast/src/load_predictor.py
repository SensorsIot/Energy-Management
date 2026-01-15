"""
Statistical load prediction using historical consumption data.

Builds time-of-day profiles from historical data and generates
P10/P50/P90 power forecasts (W) at 15-minute intervals.

Time-of-day profiles are built using LOCAL time (configurable timezone),
not UTC, so that "morning peak" patterns align with actual user behavior.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import pytz
from influxdb_client import InfluxDBClient

logger = logging.getLogger(__name__)


class LoadPredictor:
    """Predict household load using statistical time-of-day profiles."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        org: str,
        source_bucket: str = "HomeAssistant",
        load_entity: str = "load_power",
        history_days: int = 90,
        local_timezone: str = "Europe/Zurich",
    ):
        self.host = host
        self.port = port
        self.token = token
        self.org = org
        self.source_bucket = source_bucket
        self.load_entity = load_entity
        self.history_days = history_days
        self.local_timezone = local_timezone
        self.tz = pytz.timezone(local_timezone)
        self.client: Optional[InfluxDBClient] = None
        self.profile: Optional[pd.DataFrame] = None
        logger.info(f"Using timezone: {local_timezone} for time-of-day profiles")

    def connect(self):
        """Connect to InfluxDB."""
        url = f"http://{self.host}:{self.port}"
        logger.info(f"Connecting to InfluxDB at {url}")
        self.client = InfluxDBClient(url=url, token=self.token, org=self.org)

    def close(self):
        """Close connection."""
        if self.client:
            self.client.close()

    def load_historical_data(self) -> pd.DataFrame:
        """Load historical load data from InfluxDB."""
        logger.info(f"Loading {self.history_days} days of historical data...")

        query_api = self.client.query_api()

        query = f'''
        from(bucket: "{self.source_bucket}")
          |> range(start: -{self.history_days}d)
          |> filter(fn: (r) => r.entity_id == "{self.load_entity}")
          |> filter(fn: (r) => r._field == "value")
          |> aggregateWindow(every: 15m, fn: mean)
          |> keep(columns: ["_time", "_value"])
        '''

        result = query_api.query_data_frame(query)
        if result.empty:
            raise ValueError(f"No data for entity {self.load_entity}")

        df = result[["_time", "_value"]].copy()
        df.columns = ["time", "load_power"]
        df["time"] = pd.to_datetime(df["time"])

        # Convert to local timezone for time-of-day profile building
        # This ensures "7am" means local 7am, not UTC 7am
        if df["time"].dt.tz is None:
            df["time"] = df["time"].dt.tz_localize("UTC")
        df["time"] = df["time"].dt.tz_convert(self.local_timezone)

        df = df.set_index("time")
        df = df.dropna()

        logger.info(f"Loaded {len(df)} data points (converted to {self.local_timezone})")
        return df

    def build_profile(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Build time-of-day load profile with P10/P50/P90.

        Groups all historical data by 15-minute slot (0-95) and
        calculates percentiles for each slot.
        """
        df = df.copy()
        df["slot"] = df.index.hour * 4 + df.index.minute // 15  # 96 slots per day

        # Aggregate by time-of-day slot
        self.profile = df.groupby("slot")["load_power"].agg(
            p10=lambda x: x.quantile(0.1),
            p50=lambda x: x.quantile(0.5),
            p90=lambda x: x.quantile(0.9),
            mean="mean",
            count="count",
        )

        # Log statistics
        total_days = len(df) / 96  # Approximate days of data
        avg_load = df["load_power"].mean()
        logger.info(f"Built profile from ~{total_days:.0f} days of data, avg load {avg_load:.0f}W")
        logger.info(f"  P10 range: {self.profile['p10'].min():.0f} - {self.profile['p10'].max():.0f} W")
        logger.info(f"  P50 range: {self.profile['p50'].min():.0f} - {self.profile['p50'].max():.0f} W")
        logger.info(f"  P90 range: {self.profile['p90'].min():.0f} - {self.profile['p90'].max():.0f} W")

        return self.profile

    def generate_forecast(
        self,
        start_time: Optional[datetime] = None,
        hours: int = 48,
    ) -> pd.DataFrame:
        """
        Generate load forecast with P10/P50/P90.

        Args:
            start_time: Forecast start (default: now, aligned to 15-min)
            hours: Forecast horizon in hours

        Returns:
            DataFrame with columns: power_w_p10, power_w_p50, power_w_p90
            Each value represents instantaneous power (W) at that timestamp.
            Index is in UTC for consistency with InfluxDB storage.
        """
        if self.profile is None:
            raise ValueError("Profile not built. Call build_profile() first.")

        # Align start time to 15-min boundary in UTC
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        start_time = start_time.replace(
            minute=(start_time.minute // 15) * 15,
            second=0,
            microsecond=0,
        )

        # Generate forecast timestamps in UTC (15-min intervals)
        n_slots = hours * 4
        timestamps_utc = pd.date_range(start=start_time, periods=n_slots, freq="15min", tz="UTC")

        # Convert to local time for slot lookup
        timestamps_local = timestamps_utc.tz_convert(self.local_timezone)

        # Build forecast
        forecast_data = []
        for ts_utc, ts_local in zip(timestamps_utc, timestamps_local):
            # Use LOCAL time for slot calculation (matches how profile was built)
            slot = ts_local.hour * 4 + ts_local.minute // 15

            if slot in self.profile.index:
                row = self.profile.loc[slot]
                # Store power directly (W) - energy calculated when needed
                forecast_data.append({
                    "time": ts_utc,  # Store in UTC for InfluxDB
                    "power_w_p10": row["p10"],
                    "power_w_p50": row["p50"],
                    "power_w_p90": row["p90"],
                })
            else:
                # Fallback (shouldn't happen with complete data)
                forecast_data.append({
                    "time": ts_utc,
                    "power_w_p10": self.profile["p10"].median(),
                    "power_w_p50": self.profile["p50"].median(),
                    "power_w_p90": self.profile["p90"].median(),
                })

        forecast = pd.DataFrame(forecast_data)
        forecast = forecast.set_index("time")

        logger.info(f"Generated {len(forecast)} forecast points (15-min intervals, "
                    f"slots based on {self.local_timezone})")
        return forecast

    def get_profile_summary(self) -> dict:
        """Get summary statistics of the profile."""
        if self.profile is None:
            return {}

        return {
            "slots": len(self.profile),
            "avg_p50_power": self.profile["p50"].mean(),
            "daily_energy_p50": self.profile["p50"].sum() * 0.25,  # Wh per day
            "min_samples_per_slot": self.profile["count"].min(),
        }
