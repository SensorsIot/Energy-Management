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
from src.appliance_signal import calculate_appliance_signal
from src.influxdb_writer import SimulationWriter
from src.notifications import init_telegram, notify_error

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
        influx_token = influx_opts.get("token", "")

        self.forecast_reader = ForecastReader(
            host=influx_opts.get("host", "192.168.0.203"),
            port=influx_opts.get("port", 8087),
            token=influx_token,
            org=influx_opts.get("org", "energymanagement"),
            pv_bucket=influx_opts.get("pv_bucket", "pv_forecast"),
            load_bucket=influx_opts.get("load_bucket", "load_forecast"),
        )

        # InfluxDB client for writing results
        self.influx_url = f"http://{influx_opts.get('host', '192.168.0.203')}:{influx_opts.get('port', 8087)}"
        self.influx_token = influx_token
        self.influx_org = influx_opts.get("org", "energymanagement")
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

        # Appliance signal config
        appliance_opts = options.get("appliances", {})
        self.appliance_power_w = appliance_opts.get("power_w", 2500)
        self.appliance_energy_wh = appliance_opts.get("energy_wh", 1500)

        # Scheduler
        schedule_opts = options.get("schedule", {})
        self.update_interval = schedule_opts.get("update_interval_minutes", 15)
        self.scheduler = BackgroundScheduler(timezone="UTC")

        # Track last discharge state to only send signal on change
        self.last_discharge_allowed = None

        # SimulationWriter for FSD 4.2.3 output
        self.simulation_writer = SimulationWriter(
            host=influx_opts.get("host", "192.168.0.203"),
            port=influx_opts.get("port", 8087),
            token=influx_token,
            org=influx_opts.get("org", "energymanagement"),
            bucket=self.output_bucket,
        )

        # Initialize Telegram notifications
        telegram_opts = options.get("telegram", {})
        init_telegram(
            bot_token=telegram_opts.get("bot_token", ""),
            chat_id=telegram_opts.get("chat_id", ""),
        )

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
        self.simulation_writer.connect()
        logger.info("Connected successfully")

    def close(self):
        """Close connections."""
        self.forecast_reader.close()
        if self.influx_client:
            self.influx_client.close()
        self.simulation_writer.close()

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

        # Delete ALL comparison data from simulation start onwards
        # This ensures old stale data doesn't interfere
        delete_api = self.influx_client.delete_api()
        sim_start = sim_no_strategy.index.min()
        # Also delete from 1 hour before to catch any stale data
        delete_start = sim_start - timedelta(hours=1)
        try:
            delete_api.delete(
                start=delete_start,
                stop=datetime.now(timezone.utc).replace(year=2100),
                predicate='_measurement="soc_comparison"',
                bucket=self.output_bucket,
                org=self.influx_org,
            )
            logger.info(f"DEBUG: Deleted soc_comparison data from {delete_start} onwards")
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

        # Set max discharge power: 5000W if allowed, 0W if blocked
        value = 5000 if discharge_allowed else 0
        action = "enable" if discharge_allowed else "block"

        success, error_msg = self.ha_client.set_battery_discharge_power(
            self.discharge_control_entity,
            value,
            max_retries=5,
        )

        if success:
            self.last_discharge_allowed = discharge_allowed
            logger.info(f"Battery control: {self.discharge_control_entity} = {value}W")
        else:
            # All retries failed - send Telegram notification
            logger.error(f"Failed to {action} battery discharge after 5 attempts")
            notify_error(
                title="Battery Control Failed",
                message=(
                    f"Failed to {action} battery discharge after 5 attempts.\n\n"
                    f"Entity: {self.discharge_control_entity}\n"
                    f"Target value: {value}W\n"
                    f"Error: {error_msg}\n\n"
                    f"The battery may not be in the expected state!"
                ),
            )

    def run_optimization(self):
        """Run battery optimization cycle."""
        logger.info("=" * 50)
        logger.info("Running battery optimization...")

        try:
            # Get current SOC
            current_soc = self.get_current_soc()
            logger.info(f"DEBUG: Current battery SOC from get_current_soc(): {current_soc:.1f}%")

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
            logger.info(f"DEBUG: Forecast first timestamp: {forecast.index[0]}")
            logger.info(f"DEBUG: Forecast last timestamp: {forecast.index[-1]}")

            # Calculate discharge decision
            decision, sim_no_strategy, sim_with_strategy = self.optimizer.calculate_decision(
                soc_percent=current_soc,
                forecast=forecast,
                now=now,
            )

            # Debug: log first few simulation points
            if not sim_no_strategy.empty:
                logger.info(f"DEBUG: Simulation first timestamp: {sim_no_strategy.index[0]}")
                logger.info(f"DEBUG: Simulation first SOC: {sim_no_strategy['soc_percent'].iloc[0]:.1f}%")

            # Log decision
            logger.info(f"Decision: discharge_allowed={decision.discharge_allowed}")
            logger.info(f"Reason: {decision.reason}")
            if decision.switch_on_time:
                logger.info(f"Switch ON at: {swiss_datetime(decision.switch_on_time)}")

            # Write results to InfluxDB
            # FSD 4.2.3: Write SOC forecast (baseline simulation)
            self.simulation_writer.write_soc_forecast(sim_no_strategy)
            # Additional: comparison for dashboard visualization
            self.write_soc_comparison(sim_no_strategy, sim_with_strategy)
            self.write_decision(decision, current_soc)

            # Control battery
            self.control_battery(decision.discharge_allowed)

            # Calculate appliance signal
            self.calculate_appliance_signal(current_soc, forecast)

        except Exception as e:
            logger.error(f"Optimization failed: {e}", exc_info=True)

    def calculate_appliance_signal(self, current_soc: float, forecast):
        """Calculate and output appliance signal to Home Assistant."""
        try:
            # Get current PV and load from HA
            current_pv = self.ha_client.get_sensor_value("sensor.solar_pv_total_ac_power") or 0
            current_load = self.ha_client.get_sensor_value("sensor.load_power") or 0

            # Calculate signal
            signal = calculate_appliance_signal(
                current_pv_w=current_pv,
                current_load_w=current_load,
                current_soc_percent=current_soc,
                forecast=forecast,
                capacity_wh=self.optimizer.capacity_wh,
                appliance_power_w=self.appliance_power_w,
                appliance_energy_wh=self.appliance_energy_wh,
            )

            logger.info(f"Appliance signal: {signal.signal} - {signal.reason}")

            # Output to Home Assistant
            self.ha_client.set_sensor_state(
                "sensor.appliance_signal",
                signal.signal,
                attributes={
                    "friendly_name": "Appliance Signal",
                    "reason": signal.reason,
                    "excess_power_w": signal.excess_power_w,
                    "forecast_surplus_wh": signal.forecast_surplus_wh,
                    "icon": "mdi:washing-machine",
                },
            )

            # Write to InfluxDB
            point = (
                Point("appliance_signal")
                .field("signal", signal.signal)
                .field("reason", signal.reason)
                .field("excess_power_w", float(signal.excess_power_w))
                .field("forecast_surplus_wh", float(signal.forecast_surplus_wh))
                .time(datetime.now(timezone.utc), WritePrecision.S)
            )
            self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=point)

        except Exception as e:
            logger.error(f"Failed to calculate appliance signal: {e}")

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


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_options() -> dict:
    """Load add-on options from user config file or HA options."""
    import yaml

    # Priority 1: User config file (not managed by Supervisor)
    user_config = Path("/config/energymanager.yaml")
    if user_config.exists():
        logger.info(f"Loading config from {user_config}")
        with open(user_config) as f:
            user_opts = yaml.safe_load(f) or {}

        # Start with HA options as base (for defaults), merge user config on top
        options_path = Path("/data/options.json")
        if options_path.exists():
            with open(options_path) as f:
                base_opts = json.load(f)
            return deep_merge(base_opts, user_opts)
        return user_opts

    # Priority 2: HA add-on options path
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
