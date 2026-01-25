"""
InfluxDB writer for EnergyManager simulation results.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)


class SimulationWriter:
    """Write simulation results to InfluxDB."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        org: str,
        bucket: str = "energy_manager",
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

    def close(self):
        """Close connection."""
        if self.client:
            self.client.close()

    def write_soc_forecast(
        self,
        simulation: pd.DataFrame,
        scenario: str = "with_strategy",
    ):
        """
        Write SOC forecast to InfluxDB.

        FSD 4.2.3: Store only soc_percent to measurement 'soc_forecast'.
        PV/Load data is already in input buckets.

        Args:
            simulation: DataFrame with 'soc_percent' column, indexed by time
            scenario: Tag to identify the scenario ("with_strategy" or "without_strategy")
        """
        if simulation.empty:
            logger.warning("Empty simulation, nothing to write")
            return

        # No need to delete - points overwrite on same measurement+tags+timestamp.
        # This avoids InfluxDB delete API performance issues (see FSD C.4).

        points = []

        for timestamp, row in simulation.iterrows():
            # Ensure timestamp is timezone-aware
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            point = (
                Point("soc_forecast")
                .tag("scenario", scenario)
                .field("soc_percent", float(row["soc_percent"]))
                .time(timestamp, WritePrecision.S)
            )
            points.append(point)

        # Write all points
        logger.info(f"Writing {len(points)} SOC forecast points ({scenario}) to InfluxDB")
        self.write_api.write(bucket=self.bucket, org=self.org, record=points)
        logger.info(f"SOC forecast ({scenario}) written successfully")

    def write_decision(
        self,
        discharge_allowed: bool,
        reason: str,
        min_soc_percent: float,
        min_soc_time: datetime,
        current_soc: float,
    ):
        """
        Write discharge decision to InfluxDB.

        Args:
            discharge_allowed: Whether discharge is allowed
            reason: Explanation of decision
            min_soc_percent: Minimum SOC in simulation
            min_soc_time: Time of minimum SOC
            current_soc: Current battery SOC
        """
        now = datetime.now(timezone.utc)

        point = (
            Point("discharge_decision")
            .field("allowed", discharge_allowed)
            .field("reason", reason)
            .field("min_soc_percent", float(min_soc_percent))
            .field("min_soc_time", min_soc_time.isoformat())
            .field("current_soc", float(current_soc))
            .time(now, WritePrecision.S)
        )

        self.write_api.write(bucket=self.bucket, org=self.org, record=point)
        logger.info(f"Decision logged: discharge_allowed={discharge_allowed}")
