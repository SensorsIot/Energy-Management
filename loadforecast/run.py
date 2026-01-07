#!/usr/bin/env python3
"""
LoadForecast Home Assistant Add-on

Statistical load prediction using historical consumption data.
Generates P10/P50/P90 forecasts per 15-minute period.
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from croniter import croniter

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("loadforecast")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.load_predictor import LoadPredictor
from src.influxdb_writer import LoadForecastWriter


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
    """Load add-on options from user config file and/or HA options."""
    # Default options
    defaults = {
        "influxdb": {
            "host": "192.168.0.203",
            "port": 8087,
            "token": "",
            "org": "energymanagement",
            "source_bucket": "HomeAssistant",
            "target_bucket": "load_forecast",
        },
        "load_sensor": {
            "entity_id": "load_power",
        },
        "forecast": {
            "history_days": 90,
            "horizon_hours": 48,
        },
        "schedule": {
            "cron": "15 * * * *",
        },
        "log_level": "info",
    }

    # Load base options from HA Supervisor
    options_path = Path("/data/options.json")
    if options_path.exists():
        logger.info("Loading base options from /data/options.json")
        with open(options_path) as f:
            options = deep_merge(defaults, json.load(f))
    else:
        logger.warning("No options.json found, using defaults")
        options = defaults

    # Load user config file (deep merge on top of base options)
    yaml_paths = [
        Path("/config/loadforecast.yaml"),
        Path("/share/loadforecast/config.yaml"),
    ]

    for yaml_path in yaml_paths:
        if yaml_path.exists():
            logger.info(f"Loading user config from {yaml_path}")
            with open(yaml_path) as f:
                yaml_config = yaml.safe_load(f) or {}
            options = deep_merge(options, yaml_config)
            break

    return options


def run_forecast(options: dict) -> bool:
    """Run a single forecast cycle."""
    influx_config = options.get("influxdb", {})
    sensor_config = options.get("load_sensor", {})
    forecast_config = options.get("forecast", {})

    logger.info("=" * 60)
    logger.info("Running Load Forecast")
    logger.info("=" * 60)

    # Initialize predictor
    predictor = LoadPredictor(
        host=influx_config.get("host", "192.168.0.203"),
        port=influx_config.get("port", 8087),
        token=influx_config.get("token", ""),
        org=influx_config.get("org", "energymanagement"),
        source_bucket=influx_config.get("source_bucket", "HomeAssistant"),
        load_entity=sensor_config.get("entity_id", "load_power"),
        history_days=forecast_config.get("history_days", 90),
    )

    try:
        # Connect and load data
        predictor.connect()
        historical_data = predictor.load_historical_data()

        # Build profile
        predictor.build_profile(historical_data)
        summary = predictor.get_profile_summary()

        logger.info(f"Profile summary:")
        logger.info(f"  Avg P50 power: {summary['avg_p50_power']:.0f} W")
        logger.info(f"  Daily energy (P50): {summary['daily_energy_p50']:.0f} Wh")
        logger.info(f"  Min samples/slot: {summary['min_samples_per_slot']:.0f}")

        # Generate forecast
        forecast = predictor.generate_forecast(
            hours=forecast_config.get("horizon_hours", 48)
        )

        logger.info(f"Forecast range: {forecast.index.min()} to {forecast.index.max()}")
        logger.info(f"  P10: {forecast['energy_wh_p10'].min():.0f} - {forecast['energy_wh_p10'].max():.0f} Wh/15min")
        logger.info(f"  P50: {forecast['energy_wh_p50'].min():.0f} - {forecast['energy_wh_p50'].max():.0f} Wh/15min")
        logger.info(f"  P90: {forecast['energy_wh_p90'].min():.0f} - {forecast['energy_wh_p90'].max():.0f} Wh/15min")

        # Write to InfluxDB
        writer = LoadForecastWriter(
            host=influx_config.get("host", "192.168.0.203"),
            port=influx_config.get("port", 8087),
            token=influx_config.get("token", ""),
            org=influx_config.get("org", "energymanagement"),
            bucket=influx_config.get("target_bucket", "load_forecast"),
        )
        writer.connect()
        writer.ensure_bucket(retention_days=30)
        writer.write_forecast(forecast, model="statistical")
        writer.close()

        logger.info("Load forecast complete!")
        return True

    except Exception as e:
        logger.error(f"Forecast failed: {e}", exc_info=True)
        return False
    finally:
        predictor.close()


def main():
    """Main entry point for HA add-on."""
    options = load_options()

    # Set log level from options
    log_level = options.get("log_level", "info").upper()
    logging.getLogger().setLevel(getattr(logging, log_level))

    logger.info("Starting LoadForecast add-on")

    # Run initial forecast
    run_forecast(options)

    # Get schedule
    cron_expr = options.get("schedule", {}).get("cron", "15 * * * *")
    logger.info(f"Scheduling forecasts with cron: {cron_expr}")

    # Handle signals
    running = True

    def signal_handler(signum, frame):
        nonlocal running
        logger.info(f"Received signal {signum}, shutting down...")
        running = False

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Main loop - run on cron schedule
    cron = croniter(cron_expr, datetime.now(timezone.utc))

    while running:
        next_run = cron.get_next(datetime)
        now = datetime.now(timezone.utc)
        wait_seconds = (next_run - now).total_seconds()

        if wait_seconds > 0:
            logger.info(f"Next forecast at {next_run.strftime('%H:%M:%S')} ({wait_seconds:.0f}s)")

            # Sleep in small intervals to allow signal handling
            while wait_seconds > 0 and running:
                time.sleep(min(wait_seconds, 30))
                now = datetime.now(timezone.utc)
                wait_seconds = (next_run - now).total_seconds()

        if running:
            run_forecast(options)

    logger.info("LoadForecast add-on stopped")


if __name__ == "__main__":
    main()
