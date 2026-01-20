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
from src.config import PVSystemConfig
from src.accuracy_tracker import AccuracyTracker, create_accuracy_tracker


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

        # Initialize PV system config from user options
        self.pv_config = PVSystemConfig.from_options(options)
        logger.info(f"PV system: {self.pv_config.get_total_dc_power():.0f}W total DC power")

        # Location (from PV config, which inherits from options)
        self.latitude = self.pv_config.latitude
        self.longitude = self.pv_config.longitude
        self.timezone = self.pv_config.timezone

        # Initialize components
        self.influx_writer: Optional[ForecastWriter] = None
        self.scheduler: Optional[ForecastScheduler] = None
        self.accuracy_tracker: Optional[AccuracyTracker] = None

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

    def init_accuracy_tracker(self):
        """Initialize forecast accuracy tracker."""
        accuracy_config = self.options.get("accuracy_tracker", {})

        if not accuracy_config.get("enabled", True):
            logger.info("Accuracy tracking disabled")
            return

        self.accuracy_tracker = create_accuracy_tracker(self.options)
        self.accuracy_tracker.connect()
        logger.info("Accuracy tracker initialized")

    def init_scheduler(self):
        """Initialize scheduler with callbacks."""
        schedule_config = self.options.get("schedule", {})

        self.scheduler = ForecastScheduler(
            data_dir=self.data_dir,
            ch1_cron=schedule_config.get("ch1_cron", "30 2,5,8,11,14,17,20,23 * * *"),
            ch2_cron=schedule_config.get("ch2_cron", "45 2,8,14,20 * * *"),
            calculator_interval_minutes=schedule_config.get("calculator_interval_minutes", 15),
            timezone="UTC",  # Weather fetch cron schedules are in UTC
            local_timezone=self.timezone,  # Accuracy tracking uses local time (21:00 decision)
        )

        # Set callbacks (including accuracy tracking if enabled)
        self.scheduler.set_callbacks(
            fetch_ch1=self.fetch_ch1,
            fetch_ch2=self.fetch_ch2,
            calculate=self.calculate_forecast,
            snapshot=self.snapshot_forecast if self.accuracy_tracker else None,
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
            hour_end=60,    # Must reach next 21:00 cheap tariff (~48h worst case)
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
            # Load ensemble weather data using configured location
            ensemble_weather = load_hybrid_ensemble_forecast(
                self.data_dir,
                lat=self.latitude,
                lon=self.longitude,
            )
            if not ensemble_weather:
                logger.warning("No ensemble data available, skipping calculation")
                return

            logger.info(f"Loaded {len(ensemble_weather)} ensemble members")

            # Calculate PV forecast using configured plants
            pv_forecast = forecast_ensemble_plants(
                ensemble_weather,
                plants=self.pv_config.plants,
            )
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

    def snapshot_forecast(self):
        """Snapshot current forecast for accuracy tracking (21:00 daily)."""
        if not self.accuracy_tracker:
            return

        logger.info("Snapshotting forecast for accuracy tracking...")
        try:
            success = self.accuracy_tracker.snapshot_forecast()
            if success:
                logger.info("Forecast snapshot completed")
            else:
                logger.warning("Forecast snapshot failed")
        except Exception as e:
            logger.error(f"Forecast snapshot failed: {e}", exc_info=True)

    def start(self):
        """Start the add-on."""
        logger.info("Starting SwissSolarForecast add-on...")

        # Initialize components
        self.init_influxdb()
        self.init_accuracy_tracker()  # Must be before scheduler
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

        if self.accuracy_tracker:
            self.accuracy_tracker.close()

        if self.influx_writer:
            self.influx_writer.close()

        logger.info("SwissSolarForecast add-on stopped")


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_options(config_path: str = None) -> Dict:
    """Load configuration with secrets from environment.

    Strategy:
    1. Load defaults from /usr/share/swisssolarforecast/swisssolarforecast.yaml.example
    2. Load user config from /config/swisssolarforecast.yaml (via --config)
    3. Deep-merge: defaults first, user values win
    4. Overlay secrets from environment variables (set by startup script from HA UI)
    5. User file is never overwritten (source of truth for non-secrets)
    """
    defaults = {}
    user_config = {}

    # Load defaults from template (shipped in image)
    defaults_path = Path("/usr/share/swisssolarforecast/swisssolarforecast.yaml.example")
    if defaults_path.exists():
        logger.debug(f"Loading defaults from {defaults_path}")
        with open(defaults_path) as f:
            defaults = yaml.safe_load(f) or {}

    # Load user config (non-secrets from /config/swisssolarforecast.yaml)
    if config_path:
        path = Path(config_path)
        if path.exists():
            logger.info(f"Loading user config from {path}")
            with open(path) as f:
                user_config = yaml.safe_load(f) or {}
        else:
            logger.warning(f"User config not found: {path}, using defaults only")
    else:
        # Fallback: try legacy paths for backwards compatibility
        legacy_paths = [
            Path("/config/swisssolarforecast.yaml"),
            Path("/share/swisssolarforecast/config.yaml"),
        ]
        for legacy_path in legacy_paths:
            if legacy_path.exists():
                logger.info(f"Loading config from legacy path: {legacy_path}")
                with open(legacy_path) as f:
                    user_config = yaml.safe_load(f) or {}
                break

    # Merge: defaults first, user wins
    options = deep_merge(defaults, user_config)

    # Overlay secrets from environment variables (set by HA Configuration UI)
    influxdb_token = os.environ.get("INFLUXDB_TOKEN")
    if influxdb_token:
        if "influxdb" not in options:
            options["influxdb"] = {}
        options["influxdb"]["token"] = influxdb_token
        logger.info("InfluxDB token loaded from environment")
    else:
        logger.warning("InfluxDB token not set - configure it in the add-on Configuration tab")

    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if telegram_bot_token or telegram_chat_id:
        if "notifications" not in options:
            options["notifications"] = {}
        if telegram_bot_token:
            options["notifications"]["telegram_bot_token"] = telegram_bot_token
            options["notifications"]["telegram_enabled"] = True
        if telegram_chat_id:
            options["notifications"]["telegram_chat_id"] = telegram_chat_id
        logger.info("Telegram credentials loaded from environment")

    return options


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="SwissSolarForecast Add-on")
    parser.add_argument("--config", help="Path to config file")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("SwissSolarForecast Add-on v1.1.0")
    logger.info("=" * 60)

    # Load options
    options = load_options(args.config)

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
