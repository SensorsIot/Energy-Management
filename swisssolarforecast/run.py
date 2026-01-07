#!/usr/bin/env python3
"""
SwissSolarForecast Home Assistant Add-on
Main entry point.

Runs two decoupled tasks:
1. Fetcher: Downloads ICON GRIB data from MeteoSwiss (scheduled)
2. Calculator: Reads GRIB files, calculates forecast, writes to InfluxDB (scheduled)
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("swisssolarforecast")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.scheduler import ForecastScheduler
from src.influxdb_writer import ForecastWriter
from src.icon_fetcher import IconFetcher
from src.grib_parser import load_hybrid_ensemble_forecast
from src.pv_model import forecast_ensemble_plants


class SwissSolarForecast:
    """Main add-on application."""

    def __init__(self, options: Dict):
        self.options = options
        self.running = False

        # Data directory
        storage_config = options.get("storage", {})
        self.data_dir = Path(storage_config.get("data_path", "/share/swisssolarforecast"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Data directory: {self.data_dir}")

        # Location
        self.location = options.get("location", {})
        self.latitude = self.location.get("latitude", 47.475)
        self.longitude = self.location.get("longitude", 7.767)
        self.timezone = self.location.get("timezone", "Europe/Zurich")

        # Initialize components
        self.influx_writer: Optional[ForecastWriter] = None
        self.scheduler: Optional[ForecastScheduler] = None

    def init_influxdb(self):
        """Initialize InfluxDB connection."""
        influx_config = self.options.get("influxdb", {})

        self.influx_writer = ForecastWriter(
            host=influx_config.get("host", "192.168.0.203"),
            port=influx_config.get("port", 8087),
            token=influx_config.get("token", ""),
            org=influx_config.get("org", "energymanagement"),
            bucket=influx_config.get("bucket", "pv_forecast"),
        )
        self.influx_writer.connect()
        self.influx_writer.ensure_bucket(retention_days=30)

    def init_scheduler(self):
        """Initialize scheduler with callbacks."""
        schedule_config = self.options.get("schedule", {})

        self.scheduler = ForecastScheduler(
            data_dir=self.data_dir,
            ch1_cron=schedule_config.get("ch1_cron", "30 2,5,8,11,14,17,20,23 * * *"),
            ch2_cron=schedule_config.get("ch2_cron", "45 2,8,14,20 * * *"),
            calculator_interval_minutes=schedule_config.get("calculator_interval_minutes", 15),
            timezone="UTC",  # Cron schedules are in UTC
        )

        # Set callbacks
        self.scheduler.set_callbacks(
            fetch_ch1=self.fetch_ch1,
            fetch_ch2=self.fetch_ch2,
            calculate=self.calculate_forecast,
        )

    def fetch_ch1(self):
        """Fetch ICON-CH1 ensemble data."""
        logger.info("Fetching ICON-CH1 data...")

        fetcher = IconFetcher(
            model="ch1",
            latitude=self.latitude,
            longitude=self.longitude,
            variables=["ASOB_S", "T_2M"],
            output_dir=self.data_dir / "icon-ch1",
        )

        try:
            result = fetcher.fetch_latest()
            logger.info(f"CH1 fetch complete: {result.get('files_downloaded', 0)} files")
            return result
        except Exception as e:
            logger.error(f"CH1 fetch failed: {e}", exc_info=True)
            raise

    def fetch_ch2(self):
        """Fetch ICON-CH2 ensemble data."""
        logger.info("Fetching ICON-CH2 data...")

        fetcher = IconFetcher(
            model="ch2",
            latitude=self.latitude,
            longitude=self.longitude,
            variables=["ASOB_S", "T_2M"],
            output_dir=self.data_dir / "icon-ch2",
            hour_start=33,  # Start after CH1 horizon to avoid overlap
            hour_end=48,
        )

        try:
            result = fetcher.fetch_latest()
            logger.info(f"CH2 fetch complete: {result.get('files_downloaded', 0)} files")
            return result
        except Exception as e:
            logger.error(f"CH2 fetch failed: {e}", exc_info=True)
            raise

    def calculate_forecast(self):
        """Calculate PV forecast from local GRIB data and write to InfluxDB."""
        logger.info("Calculating PV forecast...")

        try:
            # Load ensemble weather data
            ensemble_weather = load_hybrid_ensemble_forecast(self.data_dir)
            if not ensemble_weather:
                logger.warning("No ensemble data available, skipping calculation")
                return

            logger.info(f"Loaded {len(ensemble_weather)} ensemble members")

            # Calculate PV forecast
            pv_forecast = forecast_ensemble_plants(ensemble_weather)
            logger.info(f"Generated PV forecast with {len(pv_forecast)} time steps")

            # Write PV forecast to InfluxDB (15-min intervals)
            if self.influx_writer:
                run_time = datetime.now(timezone.utc)
                self.influx_writer.write_pv_forecast(
                    pv_forecast=pv_forecast,
                    model="hybrid",
                    run_time=run_time,
                    resample_minutes=15,
                )
                logger.info("PV forecast written to InfluxDB")

        except FileNotFoundError as e:
            logger.warning(f"No forecast data available: {e}")
        except Exception as e:
            logger.error(f"Forecast calculation failed: {e}", exc_info=True)
            raise

    def start(self):
        """Start the add-on."""
        logger.info("Starting SwissSolarForecast add-on...")

        # Initialize components
        self.init_influxdb()
        self.init_scheduler()

        # Start scheduler
        self.scheduler.start()

        # Run initial fetch and calculation on startup
        logger.info("Running initial data fetch...")
        try:
            self.fetch_ch1()
        except Exception as e:
            logger.warning(f"Initial CH1 fetch failed: {e}")

        try:
            self.fetch_ch2()
        except Exception as e:
            logger.warning(f"Initial CH2 fetch failed: {e}")

        logger.info("Running initial calculation...")
        try:
            self.calculate_forecast()
        except Exception as e:
            logger.warning(f"Initial calculation failed: {e}")

        self.running = True
        logger.info("SwissSolarForecast add-on started successfully")

        # Keep running
        while self.running:
            time.sleep(1)

    def stop(self):
        """Stop the add-on gracefully."""
        logger.info("Stopping SwissSolarForecast add-on...")
        self.running = False

        if self.scheduler:
            self.scheduler.stop()

        if self.influx_writer:
            self.influx_writer.close()

        logger.info("SwissSolarForecast add-on stopped")


def load_options() -> Dict:
    """Load add-on options from /data/options.json."""
    options_path = Path("/data/options.json")

    if options_path.exists():
        logger.info("Loading options from /data/options.json")
        with open(options_path) as f:
            options = json.load(f)
    else:
        logger.warning("No options.json found, using defaults")
        options = {}

    # Try to load YAML config for panels/plants
    yaml_paths = [
        Path("/config/swisssolarforecast.yaml"),
        Path("/share/swisssolarforecast/config.yaml"),
    ]

    for yaml_path in yaml_paths:
        if yaml_path.exists():
            logger.info(f"Loading PV config from {yaml_path}")
            with open(yaml_path) as f:
                yaml_config = yaml.safe_load(f)
            if yaml_config:
                if "panels" in yaml_config:
                    options["panels"] = yaml_config["panels"]
                if "plants" in yaml_config:
                    options["plants"] = yaml_config["plants"]
            break

    return options


def main():
    """Main entry point."""
    # Load options
    options = load_options()

    # Create application
    app = SwissSolarForecast(options)

    # Handle signals
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        app.start()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
