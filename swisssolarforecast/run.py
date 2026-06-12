#!/usr/bin/env python3
"""SwissSolarForecast Home Assistant Add-on — main entry point.

Runs two decoupled tasks:
1. Fetcher: Downloads ICON GRIB data from MeteoSwiss (scheduled)
2. Calculator: Reads GRIB files, calculates forecast, writes to InfluxDB (scheduled)
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd
import requests
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
from src.pv_model import forecast_ensemble_plants, forecast_all_plants
from src.local_fetcher import LocalFetcher
from src.config import PVSystemConfig
from src.accuracy_tracker import AccuracyTracker, create_accuracy_tracker
from src.shading_tracker import ShadingTracker, create_shading_tracker
from src.data_integrity import weather_run_complete


class SwissSolarForecast:
    """Main add-on application."""

    def __init__(self, options: dict) -> None:
        self.options = options
        self.running = False

        # Data directory
        storage_config = options.get("storage", {})
        self.data_dir = Path(storage_config.get("data_path", "/share/swisssolarforecast"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Data directory: {self.data_dir}")

        # Max age of the latest weather run before we treat it as stale and
        # refuse to publish a (potentially garbage) forecast from it. CH1 runs
        # 3-hourly, CH2 6-hourly, plus a publish delay, so the freshest run is
        # normally < ~9 h old; 12 h tolerates a couple of missed cycles.
        self.weather_max_run_age_hours = float(
            storage_config.get("max_run_age_hours", 12)
        )

        # Initialize PV system config from user options
        self.pv_config = PVSystemConfig.from_options(options)
        logger.info(f"PV system: {self.pv_config.get_total_dc_power():.0f}W total DC power")

        # Location (from PV config, which inherits from options)
        self.latitude = self.pv_config.latitude
        self.longitude = self.pv_config.longitude
        self.timezone = self.pv_config.timezone

        # Home Assistant configuration for decision context
        accuracy_config = options.get("accuracy_tracker", {})
        self.ha_url = accuracy_config.get("ha_url", "http://supervisor/core").rstrip("/")
        self.soc_entity = accuracy_config.get("soc_entity", "sensor.battery_state_of_capacity")
        self.discharge_entity = accuracy_config.get(
            "discharge_control_entity", "number.battery_maximum_discharging_power"
        )

        # Get HA token from environment
        self._ha_token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
        if not self._ha_token:
            try:
                with open("/run/secrets/supervisor_token") as f:
                    self._ha_token = f.read().strip()
            except FileNotFoundError:
                pass

        # Initialize components
        self.influx_writer: ForecastWriter | None = None
        self.scheduler: ForecastScheduler | None = None
        self.accuracy_tracker: AccuracyTracker | None = None
        self.shading_tracker: ShadingTracker | None = None

    def _get_ha_value(self, entity_id: str) -> float | None:
        """Fetch numeric value from Home Assistant entity."""
        if not self._ha_token:
            return None
        try:
            response = requests.get(
                f"{self.ha_url}/api/states/{entity_id}",
                headers={"Authorization": f"Bearer {self._ha_token}"},
                timeout=10,
            )
            response.raise_for_status()
            return float(response.json().get("state", 0))
        except Exception as e:
            logger.debug(f"Could not fetch {entity_id}: {e}")
            return None

    def init_influxdb(self) -> None:
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

    def init_accuracy_tracker(self) -> None:
        """Initialize forecast accuracy tracker."""
        accuracy_config = self.options.get("accuracy_tracker", {})

        if not accuracy_config.get("enabled", True):
            logger.info("Accuracy tracking disabled")
            return

        self.accuracy_tracker = create_accuracy_tracker(self.options)
        self.accuracy_tracker.connect()
        logger.info("Accuracy tracker initialized")

    def init_shading_tracker(self) -> None:
        """Initialize shading correction tracker."""
        self.shading_tracker = create_shading_tracker(self.options)
        self.shading_tracker.connect()
        logger.info("Shading tracker initialized")

    def init_scheduler(self) -> None:
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
            evaluate=self.evaluate_forecast if self.accuracy_tracker else None,
            shading_update=self.update_shading_factors if self.shading_tracker else None,
        )

        # Add local point forecast job (hourly, parallel to GRIB pipeline)
        from apscheduler.triggers.interval import IntervalTrigger

        self.scheduler.scheduler.add_job(
            self._local_forecast_job,
            IntervalTrigger(minutes=60),
            id="local_forecast",
            name="Fetch and calculate local forecast",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def _local_forecast_job(self) -> None:
        """Job wrapper for local forecast calculation."""
        logger.info("Scheduled local forecast starting...")
        try:
            self.calculate_local_forecast()
        except Exception as e:
            logger.error(f"Local forecast job failed: {e}", exc_info=True)

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
            hour_end=120,   # CH2 max horizon (5 days)
        )

        try:
            result = fetcher.fetch_latest()
            logger.info(f"CH2 fetch complete: {result.get('files_downloaded', 0)} files")
            return result
        except Exception as e:
            logger.error(f"CH2 fetch failed: {e}", exc_info=True)
            raise

    def calculate_forecast(self) -> None:
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

            # Load shading factors if available
            shading_factors = None
            if self.shading_tracker:
                shading_factors = self.shading_tracker.load_shading_factors()
                if shading_factors:
                    logger.info(f"Applying shading correction for: {list(shading_factors.keys())}")

            # Calculate PV forecast using configured plants
            pv_forecast = forecast_ensemble_plants(
                ensemble_weather,
                plants=self.pv_config.plants,
                shading_factors=shading_factors,
            )
            logger.info(f"Generated PV forecast with {len(pv_forecast)} time steps")

            # Data-integrity guard: only publish if the underlying weather runs
            # downloaded completely and are fresh. A partial/stale download
            # (e.g. during a WAN outage) is rejected so we keep the last good
            # forecast instead of overwriting it with garbage. This validates
            # the *input*, not the output shape, so it never false-rejects a
            # legitimately unusual (e.g. evening-clearing) weather day.
            for model_dir in ("icon-ch1", "icon-ch2"):
                ok, reason = weather_run_complete(
                    self.data_dir, model_dir, self.weather_max_run_age_hours
                )
                if not ok:
                    logger.error(
                        f"Weather data not usable — keeping last good hybrid "
                        f"forecast, skipping write: {reason}"
                    )
                    return
                logger.debug(f"Weather check: {reason}")

            # Write PV forecast to InfluxDB (15-min intervals)
            if self.influx_writer:
                run_time = datetime.now(UTC)

                # Fetch current battery state from HA for decision context
                battery_soc = self._get_ha_value(self.soc_entity)
                discharge_power = self._get_ha_value(self.discharge_entity)

                self.influx_writer.write_pv_forecast(
                    pv_forecast=pv_forecast,
                    model="hybrid",
                    run_time=run_time,
                    resample_minutes=15,
                    battery_soc=battery_soc,
                    discharge_power_limit=discharge_power,
                )
                # Heartbeat: a plausible forecast was just published. Consumers
                # (energymanager shaving) treat a stale heartbeat as "don't
                # trust the forecast" and fall back to safe greedy charging.
                self.influx_writer.write_metadata(
                    "forecast_heartbeat", run_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                )
                logger.info("PV forecast written to InfluxDB")

        except FileNotFoundError as e:
            logger.warning(f"No forecast data available: {e}")
        except Exception as e:
            logger.error(f"Forecast calculation failed: {e}", exc_info=True)
            raise

    def calculate_local_forecast(self) -> None:
        """Fetch MeteoSwiss local point forecast and calculate PV power."""
        logger.info("Calculating local point forecast...")

        try:
            fetcher = LocalFetcher(point_id=441500)
            weather = fetcher.fetch_latest()

            if weather.empty:
                logger.warning("No local forecast data available")
                return

            # Load shading factors if available
            shading_factors = None
            if self.shading_tracker:
                shading_factors = self.shading_tracker.load_shading_factors()

            # Single deterministic forecast through same PV model
            pv_forecast = forecast_all_plants(
                weather,
                plants=self.pv_config.plants,
                shading_factors=shading_factors,
            )
            logger.info(f"Local PV forecast: {len(pv_forecast)} time steps")

            # Wrap as P10=P50=P90 (deterministic, no ensemble spread)
            output = pd.DataFrame(index=pv_forecast.index)
            for percentile in ["p10", "p50", "p90"]:
                output[f"total_ac_power_{percentile}"] = pv_forecast["total_ac_power"]
                for col in pv_forecast.columns:
                    if col.endswith("_ac_power") and col != "total_ac_power":
                        inv_name = col.replace("_ac_power", "")
                        output[f"{inv_name}_ac_power_{percentile}"] = pv_forecast[col]

            # Copy weather columns
            for col in ["ghi", "temp_air"]:
                if col in pv_forecast.columns:
                    output[col] = pv_forecast[col]

            # Data-integrity guard (local path has no metadata.json): the point
            # forecast must extend well into the future. A stale/partial CSV
            # (e.g. a failed/old download during a WAN outage) won't cover the
            # coming hours → keep last good rather than publish garbage.
            idx = output.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            future_hours = (idx.max() - datetime.now(UTC)).total_seconds() / 3600.0
            if future_hours < 12:
                logger.error(
                    f"Local forecast covers only {future_hours:.1f} h ahead "
                    f"(stale/partial download?) — keeping last good, skipping write"
                )
                return

            # Write to InfluxDB with model="local" tag
            if self.influx_writer:
                self.influx_writer.write_pv_forecast(
                    pv_forecast=output,
                    model="local",
                    run_time=datetime.now(UTC),
                    resample_minutes=15,
                )
                logger.info("Local forecast written to InfluxDB")

        except Exception as e:
            logger.error(f"Local forecast failed: {e}", exc_info=True)

    def snapshot_forecast(self) -> None:
        """Snapshot current forecast for accuracy tracking (21:00 daily).

        Snapshots both GRIB (hybrid) and local forecasts with the same rules
        so they can be compared apples-to-apples against the same actuals.
        """
        if not self.accuracy_tracker:
            return

        for model in ("hybrid", "local"):
            logger.info(f"Snapshotting {model} forecast for accuracy tracking...")
            try:
                success = self.accuracy_tracker.snapshot_forecast(model=model)
                if success:
                    logger.info(f"Forecast snapshot completed (model={model})")
                else:
                    logger.warning(f"Forecast snapshot failed (model={model})")
            except Exception as e:
                logger.error(f"Forecast snapshot failed (model={model}): {e}", exc_info=True)

    def evaluate_forecast(self) -> None:
        """Evaluate forecast accuracy against actuals (21:15 daily).

        Evaluates both GRIB and local forecasts against the same actuals.
        Weather factor decomposition only runs for hybrid (ensemble-based).
        """
        if not self.accuracy_tracker:
            return

        for model in ("hybrid", "local"):
            logger.info(f"Evaluating {model} forecast accuracy...")
            try:
                success = self.accuracy_tracker.evaluate_forecast(model=model)
                if success:
                    logger.info(f"Forecast evaluation completed (model={model})")
                else:
                    logger.warning(f"Forecast evaluation returned no data (model={model})")
            except Exception as e:
                logger.error(f"Forecast evaluation failed (model={model}): {e}", exc_info=True)

    def update_shading_factors(self) -> None:
        """Update shading factors from recent accuracy data."""
        if not self.shading_tracker:
            return

        logger.info("Updating shading factors...")
        try:
            # Get yesterday's snapshot_id
            from datetime import timedelta
            from zoneinfo import ZoneInfo

            local_tz = ZoneInfo(self.timezone)
            yesterday = datetime.now(local_tz) - timedelta(days=1)
            snapshot_id = yesterday.strftime("%Y-%m-%d")

            # Process accuracy data and store shading observations
            if self.shading_tracker.process_accuracy_data(snapshot_id):
                # Update the YAML with recalculated factors
                self.shading_tracker.update_shading_yaml()
                logger.info("Shading factors updated")
            else:
                logger.info("No shading update (not a sunny day)")
        except Exception as e:
            logger.error(f"Shading factors update failed: {e}", exc_info=True)

    def start(self) -> None:
        """Start the add-on."""
        logger.info("Starting SwissSolarForecast add-on...")

        # Initialize components
        self.init_influxdb()
        self.init_accuracy_tracker()  # Must be before scheduler
        self.init_shading_tracker()   # Must be before scheduler
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

        logger.info("Running initial local forecast...")
        try:
            self.calculate_local_forecast()
        except Exception as e:
            logger.warning(f"Initial local forecast failed: {e}")

        self.running = True
        logger.info("SwissSolarForecast add-on started successfully")

        # Keep running
        while self.running:
            time.sleep(1)

    def stop(self) -> None:
        """Stop the add-on gracefully."""
        logger.info("Stopping SwissSolarForecast add-on...")
        self.running = False

        if self.scheduler:
            self.scheduler.stop()

        if self.accuracy_tracker:
            self.accuracy_tracker.close()

        if self.shading_tracker:
            self.shading_tracker.close()

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


def load_options(config_path: str = None) -> dict:
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


def main() -> None:
    """Run the SwissSolarForecast add-on (CLI entry point)."""
    import argparse

    parser = argparse.ArgumentParser(description="SwissSolarForecast Add-on")
    parser.add_argument("--config", help="Path to config file")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("SwissSolarForecast Add-on v1.3.1")
    logger.info("=" * 60)

    # Load options
    options = load_options(args.config)

    # Create application
    app = SwissSolarForecast(options)

    # Handle signals
    def signal_handler(signum, frame) -> None:
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
