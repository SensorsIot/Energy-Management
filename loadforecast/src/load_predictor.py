"""
Load prediction using occupancy-based profiles from historical data.

Uses lab power (Shelly 2PM) to identify occupancy patterns:
- nobody_home: lab inactive, low house load
- woman_home: lab inactive, moderate house load
- man_home: lab active, moderate house load
- both_home: lab active, high house load
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

import numpy as np
import pandas as pd
from influxdb_client import InfluxDBClient

logger = logging.getLogger(__name__)

# Occupancy detection thresholds
LAB_POWER_THRESHOLD = 100  # W - above this = man in lab
LAB_HOURS_THRESHOLD = 2    # hours - more than this = man home for the day


class LoadPredictor:
    """Predict household load using occupancy-based patterns."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        org: str,
        source_bucket: str = "HomeAssistant",
        house_load_entity: str = "load_power",
        lab_power_entity: str = "shelly_2pm_white_switch_1_power",
        history_days: int = 90,
    ):
        self.host = host
        self.port = port
        self.token = token
        self.org = org
        self.source_bucket = source_bucket
        self.house_load_entity = house_load_entity
        self.lab_power_entity = lab_power_entity
        self.history_days = history_days
        self.client: Optional[InfluxDBClient] = None
        self.profiles: Dict[str, pd.DataFrame] = {}
        self.occupancy_stats: Dict[str, dict] = {}

    def connect(self):
        """Connect to InfluxDB."""
        url = f"http://{self.host}:{self.port}"
        logger.info(f"Connecting to InfluxDB at {url}")
        self.client = InfluxDBClient(url=url, token=self.token, org=self.org)

    def close(self):
        """Close connection."""
        if self.client:
            self.client.close()

    def _query_entity(self, entity_id: str, column_name: str) -> pd.DataFrame:
        """Query a single entity from InfluxDB."""
        query_api = self.client.query_api()

        query = f'''
        from(bucket: "{self.source_bucket}")
          |> range(start: -{self.history_days}d)
          |> filter(fn: (r) => r.entity_id == "{entity_id}")
          |> filter(fn: (r) => r._field == "value")
          |> aggregateWindow(every: 15m, fn: mean)
          |> keep(columns: ["_time", "_value"])
        '''

        result = query_api.query_data_frame(query)
        if result.empty:
            return pd.DataFrame()

        df = result[["_time", "_value"]].copy()
        df.columns = ["time", column_name]
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
        return df

    def load_historical_data(self) -> pd.DataFrame:
        """Load historical house load and lab power data."""
        logger.info(f"Loading {self.history_days} days of historical data...")

        # Load house load
        house = self._query_entity(self.house_load_entity, "house_load")
        if house.empty:
            raise ValueError(f"No data for {self.house_load_entity}")
        logger.info(f"Loaded {len(house)} house load points")

        # Load lab power
        lab = self._query_entity(self.lab_power_entity, "lab_power")
        if lab.empty:
            logger.warning(f"No lab power data - using house load only")
            house["lab_power"] = 0
            return house.dropna()

        logger.info(f"Loaded {len(lab)} lab power points")

        # Merge on timestamp
        df = house.join(lab, how="inner").dropna()
        logger.info(f"Merged data: {len(df)} points")
        return df

    def _classify_daily_occupancy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Classify each day's occupancy based on lab activity and house load."""
        df = df.copy()
        df["hour"] = df.index.hour
        df["date"] = df.index.date
        df["man_in_lab"] = df["lab_power"] > LAB_POWER_THRESHOLD

        daily = []
        for date, group in df.groupby("date"):
            daytime = group[(group["hour"] >= 8) & (group["hour"] < 20)]
            if len(daytime) < 20:  # Need at least 20 x 15-min slots
                continue

            lab_hours = daytime["man_in_lab"].sum() / 4  # 4 slots per hour
            house_load = daytime["house_load"].mean()

            daily.append({
                "date": date,
                "weekday": group.index[0].weekday(),
                "house_load": house_load,
                "lab_hours": lab_hours,
                "man_home": lab_hours > LAB_HOURS_THRESHOLD,
            })

        daily_df = pd.DataFrame(daily)

        # Determine woman presence based on house load when man is away
        man_away = daily_df[~daily_df["man_home"]]
        if len(man_away) > 2:
            q_low = man_away["house_load"].quantile(0.33)
        else:
            q_low = 350  # Default threshold

        # High load threshold for "both home"
        q_high = daily_df["house_load"].quantile(0.75)

        def classify(row):
            if row["man_home"]:
                if row["house_load"] > q_high:
                    return "both_home"
                else:
                    return "man_home"
            else:
                if row["house_load"] < q_low:
                    return "nobody_home"
                else:
                    return "woman_home"

        daily_df["occupancy"] = daily_df.apply(classify, axis=1)
        return daily_df

    def build_profiles(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """Build load profiles for each occupancy type."""
        # Classify daily occupancy
        daily_df = self._classify_daily_occupancy(df)
        date_to_occ = dict(zip(daily_df["date"], daily_df["occupancy"]))

        # Add occupancy to raw data
        df = df.copy()
        df["date"] = df.index.date
        df["occupancy"] = df["date"].map(date_to_occ)
        df["hour"] = df.index.hour
        df["slot"] = df["hour"] * 4 + (df.index.minute // 15)  # 96 slots per day

        # Build profiles for each occupancy type (15-min resolution)
        self.profiles = {}
        self.occupancy_stats = {}

        for occ in ["nobody_home", "woman_home", "man_home", "both_home"]:
            occ_data = df[df["occupancy"] == occ]
            if len(occ_data) < 10:
                logger.warning(f"Insufficient data for {occ} ({len(occ_data)} points)")
                continue

            profile = occ_data.groupby("slot")["house_load"].agg(
                p10=lambda x: x.quantile(0.1),
                p50=lambda x: x.quantile(0.5),
                p90=lambda x: x.quantile(0.9),
                mean="mean",
                count="count",
            )
            self.profiles[occ] = profile

            # Stats
            days = daily_df[daily_df["occupancy"] == occ]
            self.occupancy_stats[occ] = {
                "days": len(days),
                "pct": len(days) / len(daily_df) * 100,
                "daytime_mean": days["house_load"].mean(),
            }
            logger.info(f"Built profile for {occ}: {len(days)} days, {days['house_load'].mean():.0f}W avg")

        # Default profile (weighted average or man_home as default)
        if "man_home" in self.profiles:
            self.profiles["default"] = self.profiles["man_home"].copy()
        elif self.profiles:
            # Use most common profile as default
            most_common = max(self.occupancy_stats.keys(),
                            key=lambda k: self.occupancy_stats[k]["days"])
            self.profiles["default"] = self.profiles[most_common].copy()

        return self.profiles

    def generate_forecast(
        self,
        start_time: Optional[datetime] = None,
        hours: int = 24,
        occupancy: str = "default",
    ) -> pd.DataFrame:
        """
        Generate load forecast for the specified period.

        Args:
            start_time: Forecast start (default: now, aligned to 15-min)
            hours: Forecast horizon in hours
            occupancy: Occupancy profile to use (default, man_home, woman_home, both_home, nobody_home)

        Returns:
            DataFrame with column: energy_wh_p50
            Each value represents expected Wh consumed in that 15-min period (P50 only).
        """
        if not self.profiles:
            raise ValueError("Profiles not built. Call build_profiles() first.")

        # Select profile
        if occupancy not in self.profiles:
            logger.warning(f"Profile {occupancy} not found, using default")
            occupancy = "default"
        profile = self.profiles[occupancy]

        # Align start time to 15-min boundary
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        start_time = start_time.replace(
            minute=(start_time.minute // 15) * 15,
            second=0,
            microsecond=0,
        )

        # Generate forecast timestamps (15-min intervals)
        n_slots = hours * 4
        timestamps = pd.date_range(start=start_time, periods=n_slots, freq="15min")

        # Build forecast
        forecast_data = []
        for ts in timestamps:
            slot = ts.hour * 4 + ts.minute // 15

            if slot in profile.index:
                row = profile.loc[slot]
                # Average W over 15 min = W * 0.25h = Wh for that period
                # Only store P50 for load forecast
                forecast_data.append({
                    "time": ts,
                    "energy_wh_p50": row["p50"] * 0.25,
                })
            else:
                # Fallback
                median = profile["p50"].median()
                forecast_data.append({
                    "time": ts,
                    "energy_wh_p50": median * 0.25,
                })

        forecast = pd.DataFrame(forecast_data)
        forecast = forecast.set_index("time")

        logger.info(f"Generated {len(forecast)} forecast points (15-min) using '{occupancy}' profile")
        return forecast

    def get_current_occupancy(self) -> str:
        """Detect current occupancy from recent lab power."""
        try:
            query_api = self.client.query_api()
            query = f'''
            from(bucket: "{self.source_bucket}")
              |> range(start: -1h)
              |> filter(fn: (r) => r.entity_id == "{self.lab_power_entity}")
              |> filter(fn: (r) => r._field == "value")
              |> mean()
            '''
            result = query_api.query_data_frame(query)
            if not result.empty:
                lab_power = result["_value"].iloc[0]
                if lab_power > LAB_POWER_THRESHOLD:
                    return "man_home"  # Could refine to both_home based on house load
            return "default"
        except Exception as e:
            logger.warning(f"Could not detect occupancy: {e}")
            return "default"

    def get_profile_summary(self) -> dict:
        """Get summary of all profiles."""
        return {
            "profiles": list(self.profiles.keys()),
            "occupancy_stats": self.occupancy_stats,
        }
