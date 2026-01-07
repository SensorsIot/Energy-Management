"""
Read PV and Load forecasts from InfluxDB.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from influxdb_client import InfluxDBClient

logger = logging.getLogger(__name__)


class ForecastReader:
    """Read forecasts from InfluxDB."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        org: str,
        pv_bucket: str = "pv_forecast",
        load_bucket: str = "load_forecast",
    ):
        self.host = host
        self.port = port
        self.token = token
        self.org = org
        self.pv_bucket = pv_bucket
        self.load_bucket = load_bucket
        self.client: Optional[InfluxDBClient] = None

    def connect(self):
        """Connect to InfluxDB."""
        url = f"http://{self.host}:{self.port}"
        logger.info(f"Connecting to InfluxDB at {url}")
        self.client = InfluxDBClient(url=url, token=self.token, org=self.org)

    def close(self):
        """Close connection."""
        if self.client:
            self.client.close()

    def get_pv_forecast(
        self,
        start: datetime,
        end: datetime,
        percentile: str = "p50",
    ) -> pd.Series:
        """
        Get PV energy forecast.

        Args:
            start: Start time
            end: End time
            percentile: Which percentile (p10, p50, p90)

        Returns:
            Series with energy_wh indexed by time
        """
        query_api = self.client.query_api()

        query = f'''
        from(bucket: "{self.pv_bucket}")
          |> range(start: {start.isoformat()}, stop: {end.isoformat()})
          |> filter(fn: (r) => r._measurement == "pv_forecast")
          |> filter(fn: (r) => r.inverter == "total")
          |> filter(fn: (r) => r._field == "energy_wh_{percentile}")
          |> keep(columns: ["_time", "_value"])
        '''

        result = query_api.query_data_frame(query)

        if result.empty:
            logger.warning(f"No PV forecast data found for {start} to {end}")
            return pd.Series(dtype=float)

        result = result.set_index("_time")
        result.index = pd.to_datetime(result.index, utc=True)

        return result["_value"].rename("pv_energy_wh")

    def get_load_forecast(
        self,
        start: datetime,
        end: datetime,
        percentile: str = "p50",
    ) -> pd.Series:
        """
        Get Load energy forecast.

        Args:
            start: Start time
            end: End time
            percentile: Which percentile (p10, p50, p90)

        Returns:
            Series with energy_wh indexed by time
        """
        query_api = self.client.query_api()

        query = f'''
        from(bucket: "{self.load_bucket}")
          |> range(start: {start.isoformat()}, stop: {end.isoformat()})
          |> filter(fn: (r) => r._measurement == "load_forecast")
          |> filter(fn: (r) => r._field == "energy_wh_{percentile}")
          |> keep(columns: ["_time", "_value"])
        '''

        result = query_api.query_data_frame(query)

        if result.empty:
            logger.warning(f"No Load forecast data found for {start} to {end}")
            return pd.Series(dtype=float)

        result = result.set_index("_time")
        result.index = pd.to_datetime(result.index, utc=True)

        return result["_value"].rename("load_energy_wh")

    def get_combined_forecast(
        self,
        start: datetime,
        end: datetime,
        percentile: str = "p50",
    ) -> pd.DataFrame:
        """
        Get combined PV and Load forecast.

        Returns:
            DataFrame with columns: pv_energy_wh, load_energy_wh, net_energy_wh
        """
        pv = self.get_pv_forecast(start, end, percentile)
        load = self.get_load_forecast(start, end, percentile)

        if pv.empty or load.empty:
            logger.warning("Missing forecast data")
            return pd.DataFrame()

        # Align on common timestamps
        df = pd.DataFrame({"pv_energy_wh": pv, "load_energy_wh": load})
        df = df.dropna()

        # Filter to requested time range (InfluxDB may return extra data)
        logger.info(f"DEBUG: Before filter: {len(df)} rows, range {df.index.min()} to {df.index.max()}")
        logger.info(f"DEBUG: Filtering to start={start}, end={end}")
        df = df[(df.index >= start) & (df.index < end)]
        logger.info(f"DEBUG: After filter: {len(df)} rows")

        # Calculate net energy (positive = surplus, negative = deficit)
        df["net_energy_wh"] = df["pv_energy_wh"] - df["load_energy_wh"]

        logger.info(f"Loaded {len(df)} forecast periods from {df.index.min()} to {df.index.max()}")

        return df

    def get_current_soc(
        self,
        bucket: str = "HuaweiNew",
        measurement: str = "Energy",
        field: str = "BATT_Level",
    ) -> Optional[float]:
        """
        Get current battery SOC from InfluxDB.

        Args:
            bucket: Bucket containing battery data
            measurement: Measurement name
            field: Field name for SOC

        Returns:
            Current SOC percentage or None if not available
        """
        query_api = self.client.query_api()

        query = f'''
        from(bucket: "{bucket}")
          |> range(start: -1h)
          |> filter(fn: (r) => r._measurement == "{measurement}")
          |> filter(fn: (r) => r._field == "{field}")
          |> last()
        '''

        try:
            result = query_api.query(query)
            for table in result:
                for record in table.records:
                    soc = float(record.get_value())
                    logger.info(f"Current SOC from InfluxDB: {soc:.1f}%")
                    return soc
        except Exception as e:
            logger.warning(f"Failed to get SOC from InfluxDB: {e}")

        return None
