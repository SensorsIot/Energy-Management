#!/usr/bin/env python3
"""
EnergyManager Add-on for Home Assistant.

Optimizes battery usage based on PV and load forecasts.
"""

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from src.forecast_reader import ForecastReader
from src.ha_client import HAClient
from src.battery_optimizer import BatteryOptimizer

# Swiss timezone for display
SWISS_TZ = ZoneInfo("Europe/Zurich")


def swiss_time(dt: datetime) -> str:
    """Format datetime in Swiss timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SWISS_TZ).strftime("%H:%M")


def swiss_datetime(dt: datetime) -> str:
    """Format datetime in Swiss timezone with date."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SWISS_TZ).strftime("%Y-%m-%d %H:%M")


# Configure logging with Swiss timezone
class SwissFormatter(logging.Formatter):
    """Formatter that uses Swiss timezone."""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=SWISS_TZ)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Apply Swiss formatter to root logger
for handler in logging.root.handlers:
    handler.setFormatter(SwissFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))

logger = logging.getLogger("energymanager")


class EnergyManager:
    """Main EnergyManager application."""

    def __init__(self, options: dict):
        self.options = options

        # Initialize InfluxDB components
        influx_opts = options.get("influxdb", {})
        self.forecast_reader = ForecastReader(
            host=influx_opts.get("host", "192.168.0.203"),
            port=influx_opts.get("port", 8087),
            token=influx_opts.get("token", ""),
            org=influx_opts.get("org", "spiessa"),
            pv_bucket=influx_opts.get("pv_bucket", "pv_forecast"),
            load_bucket=influx_opts.get("load_bucket", "load_forecast"),
        )

        # InfluxDB client for writing results
        self.influx_url = f"http://{influx_opts.get('host', '192.168.0.203')}:{influx_opts.get('port', 8087)}"
        self.influx_token = influx_opts.get("token", "")
        self.influx_org = influx_opts.get("org", "spiessa")
        self.output_bucket = influx_opts.get("output_bucket", "energy_manager")
        self.influx_client = None
        self.write_api = None

        # Home Assistant client
        ha_opts = options.get("home_assistant", {})
        self.ha_client = HAClient(
            url=ha_opts.get("url", "http://supervisor/core"),
            token=ha_opts.get("token"),
        )

        # Battery optimizer
        battery_opts = options.get("battery", {})
        tariff_opts = options.get("tariff", {})

        self.optimizer = BatteryOptimizer(
            capacity_wh=battery_opts.get("capacity_kwh", 10.0) * 1000,
            charge_efficiency=battery_opts.get("charge_efficiency", 0.95),
            discharge_efficiency=battery_opts.get("discharge_efficiency", 0.95),
            max_charge_w=battery_opts.get("max_charge_w", 5000),
            max_discharge_w=battery_opts.get("max_discharge_w", 5000),
            weekday_cheap_start=tariff_opts.get("weekday_cheap_start", "21:00"),
            weekday_cheap_end=tariff_opts.get("weekday_cheap_end", "06:00"),
            weekend_all_day_cheap=tariff_opts.get("weekend_all_day_cheap", True),
            holidays=tariff_opts.get("holidays", []),
        )

        self.soc_entity = battery_opts.get(
            "soc_entity", "sensor.battery_state_of_capacity"
        )
        self.discharge_control_entity = battery_opts.get(
            "discharge_control_entity", "number.battery_maximum_discharging_power"
        )

        # Scheduler
        schedule_opts = options.get("schedule", {})
        self.update_interval = schedule_opts.get("update_interval_minutes", 15)
        self.scheduler = BackgroundScheduler(timezone="UTC")

        # Track last discharge state to only send signal on change
        self.last_discharge_allowed = None

    def connect(self):
        """Connect to services."""
        logger.info("Connecting to services...")
        self.forecast_reader.connect()
        self.influx_client = InfluxDBClient(
            url=self.influx_url,
            token=self.influx_token,
            org=self.influx_org
        )
        self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
        logger.info("Connected successfully")

    def close(self):
        """Close connections."""
        self.forecast_reader.close()
        if self.influx_client:
            self.influx_client.close()

    def get_current_soc(self) -> float:
        """Get current battery SOC from HA or InfluxDB."""
        # Try Home Assistant first
        current_soc = self.ha_client.get_battery_soc(self.soc_entity)

        if current_soc is None:
            # Fallback: try to get SOC from InfluxDB
            logger.info("HA SOC not available, trying InfluxDB...")
            influx_opts = self.options.get("influxdb", {})
            current_soc = self.forecast_reader.get_current_soc(
                bucket=influx_opts.get("soc_bucket", "HuaweiNew"),
                measurement=influx_opts.get("soc_measurement", "Energy"),
                field=influx_opts.get("soc_field", "BATT_Level"),
            )

        if current_soc is None:
            logger.warning("Could not get current SOC, using 50%")
            current_soc = 50.0

        return current_soc

    def write_soc_comparison(self, sim_no_strategy, sim_with_strategy):
        """Write both SOC scenarios to InfluxDB for visualization."""
        if sim_no_strategy.empty or sim_with_strategy.empty:
            return

        # Delete old comparison data
        delete_api = self.influx_client.delete_api()
        start = sim_no_strategy.index.min()
        try:
            delete_api.delete(
                start=start,
                stop=datetime.now(timezone.utc).replace(year=2100),
                predicate='_measurement="soc_comparison"',
                bucket=self.output_bucket,
                org=self.influx_org,
            )
        except Exception as e:
            logger.warning(f"Failed to delete old comparison data: {e}")

        # Write new data
        points = []
        for t in sim_no_strategy.index:
            ts = t if t.tzinfo else t.replace(tzinfo=timezone.utc)

            points.append(
                Point("soc_comparison")
                .tag("scenario", "no_strategy")
                .field("soc_percent", float(sim_no_strategy.loc[t, "soc_percent"]))
                .time(ts, WritePrecision.S)
            )

            if t in sim_with_strategy.index:
                points.append(
                    Point("soc_comparison")
                    .tag("scenario", "with_strategy")
                    .field("soc_percent", float(sim_with_strategy.loc[t, "soc_percent"]))
                    .time(ts, WritePrecision.S)
                )

        self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=points)
        logger.info(f"Written {len(points)} SOC comparison points")

    def write_decision(self, decision, current_soc: float):
        """Write discharge decision to InfluxDB."""
        now = datetime.now(timezone.utc)

        point = (
            Point("discharge_decision")
            .field("allowed", decision.discharge_allowed)
            .field("reason", decision.reason)
            .field("deficit_wh", float(decision.deficit_wh))
            .field("saved_wh", float(decision.saved_wh))
            .field("current_soc", float(current_soc))
            .time(now, WritePrecision.S)
        )

        if decision.switch_on_time:
            point = point.field("switch_on_time", decision.switch_on_time.isoformat())

        self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=point)

    def control_battery(self, discharge_allowed: bool):
        """Control battery discharge via Home Assistant - only on state change."""
        # Only send signal if state has changed
        if discharge_allowed == self.last_discharge_allowed:
            logger.debug(f"Discharge state unchanged ({discharge_allowed}), no signal sent")
            return

        if not self.ha_client.token:
            logger.warning("No HA token, cannot control battery")
            return

        try:
            # Set max discharge power: 5000W if allowed, 0W if blocked
            value = 5000 if discharge_allowed else 0
            success = self.ha_client.set_number(self.discharge_control_entity, value)
            if success:
                self.last_discharge_allowed = discharge_allowed
                logger.info(f"Battery control: {self.discharge_control_entity} = {value}W")
        except Exception as e:
            logger.error(f"Failed to control battery: {e}")

    def run_optimization(self):
        """Run battery optimization cycle."""
        logger.info("=" * 50)
        logger.info("Running battery optimization...")

        try:
            # Get current SOC
            current_soc = self.get_current_soc()
            logger.info(f"Current battery SOC: {current_soc:.1f}%")

            # Get forecast
            now = datetime.now(timezone.utc)
            start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)

            # Get tariff periods to determine forecast end
            tariff = self.optimizer.get_tariff_periods(now)
            # Always fetch at least until tomorrow 21:00 for visualization
            tomorrow_target = (now + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
            end = max(tariff.target + timedelta(hours=1), tomorrow_target)

            logger.info(f"Fetching forecasts from {swiss_datetime(start)} to {swiss_datetime(end)}")
            logger.info(f"Tariff: cheap={'Yes' if tariff.is_cheap_now else 'No'}, "
                       f"cheap_end={swiss_datetime(tariff.cheap_end)}, "
                       f"target={swiss_datetime(tariff.target)}")

            forecast = self.forecast_reader.get_combined_forecast(
                start=start,
                end=end,
                percentile="p50",
            )

            if forecast.empty:
                logger.error("No forecast data available")
                return

            logger.info(f"Got {len(forecast)} forecast periods")

            # Calculate discharge decision
            decision, sim_no_strategy, sim_with_strategy = self.optimizer.calculate_decision(
                soc_percent=current_soc,
                forecast=forecast,
                now=now,
            )

            # Log decision
            logger.info(f"Decision: discharge_allowed={decision.discharge_allowed}")
            logger.info(f"Reason: {decision.reason}")
            if decision.switch_on_time:
                logger.info(f"Switch ON at: {swiss_datetime(decision.switch_on_time)}")

            # Write results to InfluxDB
            self.write_soc_comparison(sim_no_strategy, sim_with_strategy)
            self.write_decision(decision, current_soc)

            # Control battery
            self.control_battery(decision.discharge_allowed)

        except Exception as e:
            logger.error(f"Optimization failed: {e}", exc_info=True)

    def start(self):
        """Start the scheduler."""
        logger.info(f"Starting scheduler (every {self.update_interval} minutes)")

        # Run immediately
        self.run_optimization()

        # Schedule regular updates
        self.scheduler.add_job(
            self.run_optimization,
            "interval",
            minutes=self.update_interval,
            id="optimization",
            name="Battery Optimization",
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.start()

        # Log next run time
        job = self.scheduler.get_job("optimization")
        if job:
            logger.info(f"Next optimization at {job.next_run_time}")

    def stop(self):
        """Stop the scheduler."""
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        self.close()
        logger.info("Stopped")


def load_options() -> dict:
    """Load add-on options."""
    # HA add-on options path
    options_path = Path("/data/options.json")

    if options_path.exists():
        with open(options_path) as f:
            return json.load(f)

    # Fallback for local testing
    test_options = Path(__file__).parent / "testdata" / "options.json"
    if test_options.exists():
        with open(test_options) as f:
            return json.load(f)

    logger.warning("No options file found, using defaults")
    return {}


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("EnergyManager Add-on v1.0.0")
    logger.info("=" * 60)

    # Load options
    options = load_options()

    # Set log level
    log_level = options.get("log_level", "info").upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))
    logger.info(f"Log level: {log_level}")

    # Create and start manager
    manager = EnergyManager(options)

    # Handle shutdown signals
    def shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        manager.connect()
        manager.start()

        # Keep running
        while True:
            time.sleep(60)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        manager.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
