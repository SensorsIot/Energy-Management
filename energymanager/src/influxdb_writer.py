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

    def delete_future_data(self, start: datetime):
        """Delete existing future simulation data."""
        delete_api = self.client.delete_api()
        stop = datetime.now(timezone.utc).replace(year=2100)

        try:
            delete_api.delete(
                start=start,
                stop=stop,
                predicate='_measurement="soc_simulation"',
                bucket=self.bucket,
                org=self.org,
            )
            logger.debug(f"Deleted existing simulation data from {start}")
        except Exception as e:
            logger.warning(f"Failed to delete old data: {e}")

    def write_simulation(
        self,
        simulation: pd.DataFrame,
        run_time: Optional[datetime] = None,
    ):
        """
        Write SOC simulation results to InfluxDB.

        Args:
            simulation: DataFrame with columns:
                - soc_percent
                - soc_wh
                - pv_energy_wh
                - load_energy_wh
                - net_energy_wh
                - battery_flow_wh
                - grid_flow_wh
            run_time: When this simulation was calculated
        """
        if simulation.empty:
            logger.warning("Empty simulation, nothing to write")
            return

        if run_time is None:
            run_time = datetime.now(timezone.utc)

        run_time_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Delete existing future data
        self.delete_future_data(simulation.index.min())

        points = []

        for timestamp, row in simulation.iterrows():
            # Ensure timestamp is timezone-aware
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            point = (
                Point("soc_simulation")
                .tag("run_time", run_time_str)
                .field("soc_percent", float(row["soc_percent"]))
                .field("soc_wh", float(row["soc_wh"]))
                .field("pv_energy_wh", float(row["pv_energy_wh"]))
                .field("load_energy_wh", float(row["load_energy_wh"]))
                .field("net_energy_wh", float(row["net_energy_wh"]))
                .field("battery_flow_wh", float(row["battery_flow_wh"]))
                .field("grid_flow_wh", float(row["grid_flow_wh"]))
                .time(timestamp, WritePrecision.S)
            )
            points.append(point)

        # Write all points
        logger.info(f"Writing {len(points)} simulation points to InfluxDB")
        self.write_api.write(bucket=self.bucket, org=self.org, record=points)
        logger.info("Simulation written successfully")

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
