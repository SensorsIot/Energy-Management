#!/usr/bin/env python3
"""
Load Forecast - Statistical load prediction for MPC.

Reads historical load data from InfluxDB, builds statistical profiles,
and writes forecasts to the load_forecast bucket.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

# Add src to path
sys.path.insert(0, "/home/energymanagement/loadforecast")

from src.load_predictor import LoadPredictor
from src.influxdb_writer import LoadForecastWriter

# Configuration
INFLUXDB_HOST = "192.168.0.203"
INFLUXDB_PORT = 8087
INFLUXDB_TOKEN = ""
INFLUXDB_ORG = "spiessa"
SOURCE_BUCKET = "HomeAssistant"
SOURCE_ENTITY = "load_power"
TARGET_BUCKET = "load_forecast"
HISTORY_DAYS = 90
FORECAST_HOURS = 48

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("loadforecast")


def main():
    parser = argparse.ArgumentParser(description="Generate load forecast")
    parser.add_argument("--hours", type=int, default=FORECAST_HOURS, help="Forecast horizon")
    parser.add_argument("--history", type=int, default=HISTORY_DAYS, help="History days for profiles")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to InfluxDB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Occupancy overrides
    parser.add_argument("--occupancy", choices=["auto", "nobody_home", "woman_home", "man_home", "both_home"],
                        default="auto", help="Override occupancy profile (default: auto-detect)")
    parser.add_argument("--man-away", action="store_true", help="Man will be away tomorrow")
    parser.add_argument("--woman-away", action="store_true", help="Woman will be away tomorrow")
    args = parser.parse_args()

    # Determine occupancy from flags
    if args.occupancy != "auto":
        occupancy = args.occupancy
    elif args.man_away and args.woman_away:
        occupancy = "nobody_home"
    elif args.man_away:
        occupancy = "woman_home"
    elif args.woman_away:
        occupancy = "man_home"
    else:
        occupancy = "auto"  # Will use auto-detection

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("Load Forecast Generator")
    logger.info("=" * 60)

    # Initialize predictor
    predictor = LoadPredictor(
        host=INFLUXDB_HOST,
        port=INFLUXDB_PORT,
        token=INFLUXDB_TOKEN,
        org=INFLUXDB_ORG,
        source_bucket=SOURCE_BUCKET,
        house_load_entity=SOURCE_ENTITY,
        history_days=args.history,
    )

    try:
        # Connect and load data
        predictor.connect()
        historical_data = predictor.load_historical_data()

        # Build profiles
        profiles = predictor.build_profiles(historical_data)
        summary = predictor.get_profile_summary()

        logger.info("Occupancy profiles:")
        for occ, stats in summary["occupancy_stats"].items():
            logger.info(f"  {occ}: {stats['days']} days ({stats['pct']:.0f}%), avg {stats['daytime_mean']:.0f}W")
        logger.info(f"  Available profiles: {', '.join(summary['profiles'])}")

        # Determine which profile to use
        if occupancy == "auto":
            occupancy = predictor.get_current_occupancy()
            logger.info(f"Auto-detected occupancy: {occupancy}")
        else:
            logger.info(f"Using specified occupancy: {occupancy}")

        # Generate forecast
        forecast = predictor.generate_forecast(hours=args.hours, occupancy=occupancy)

        logger.info(f"Forecast range: {forecast.index.min()} to {forecast.index.max()}")
        logger.info(f"Forecast P50 range: {forecast['energy_wh_p50'].min():.0f} - {forecast['energy_wh_p50'].max():.0f} Wh/15min")

        if args.dry_run:
            logger.info("Dry run - not writing to InfluxDB")
            print("\nForecast preview (first 10 rows):")
            print(forecast.head(10).to_string())
        else:
            # Write to InfluxDB
            writer = LoadForecastWriter(
                host=INFLUXDB_HOST,
                port=INFLUXDB_PORT,
                token=INFLUXDB_TOKEN,
                org=INFLUXDB_ORG,
                bucket=TARGET_BUCKET,
            )
            writer.connect()
            writer.ensure_bucket(retention_days=30)
            writer.write_forecast(forecast, model=f"occupancy_{occupancy}")
            writer.close()

            logger.info(f"Load forecast complete! (profile: {occupancy})")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise
    finally:
        predictor.close()


if __name__ == "__main__":
    main()
