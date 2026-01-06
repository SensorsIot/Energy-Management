#!/usr/bin/env python3
"""
Fetch ICON-CH1-EPS forecast data from MeteoSwiss.
Scheduled to run every 3 hours via systemd timer.

ICON-CH1-EPS: 1km resolution, 33h horizon, runs at 00,03,06,09,12,15,18,21 UTC
"""

import argparse
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.icon_fetcher import fetch_icon_data, PUBLICATION_DELAY_HOURS
from src.config import LATITUDE, LONGITUDE


def calculate_start_hour(skip_past: bool = True) -> int:
    """
    Calculate the starting hour to avoid downloading past forecast data.

    Returns the forecast hour offset from the latest available model run.
    """
    if not skip_past:
        return 0

    now = datetime.now(timezone.utc)

    # Find the latest available run (same logic as IconFetcher)
    schedule = [0, 3, 6, 9, 12, 15, 18, 21]
    for hours_ago in range(48):
        check_time = now - timedelta(hours=hours_ago)
        if check_time.hour in schedule:
            run_time = check_time.replace(minute=0, second=0, microsecond=0)
            pub_time = run_time + timedelta(hours=PUBLICATION_DELAY_HOURS)
            if pub_time <= now:
                # Calculate hours since model run
                hours_since_run = int((now - run_time).total_seconds() / 3600)
                # Start from current hour (skip past hours)
                return max(0, hours_since_run)

    return 0

# Configure logging for systemd (stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Output directory
FORECAST_DATA_DIR = Path("/home/energymanagement/forecastData")


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch ICON-CH1-EPS forecast data")
    parser.add_argument(
        "--all-hours",
        action="store_true",
        help="Download all hours including past (default: skip past hours to save bandwidth)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("Starting ICON-CH1-EPS fetch")
    logger.info(f"Location: {LATITUDE:.4f}, {LONGITUDE:.4f}")

    # Calculate start hour to skip past data
    hour_start = calculate_start_hour(skip_past=not args.all_hours)
    if hour_start > 0:
        logger.info(f"Skipping past hours 0-{hour_start-1}, starting from hour {hour_start}")

    try:
        metadata = fetch_icon_data(
            model="ch1",
            latitude=LATITUDE,
            longitude=LONGITUDE,
            output_dir=FORECAST_DATA_DIR,
            hour_start=hour_start,
        )

        logger.info(f"Successfully fetched ICON-CH1 run: {metadata['run_str']}")
        logger.info(f"Hours: {metadata['hour_start']}-{metadata['hour_end']}")
        logger.info(f"Files downloaded: {metadata['files_downloaded']}")
        logger.info(f"Files failed: {metadata['files_failed']}")

        if metadata['files_failed'] > 0:
            logger.warning("Some files failed to download")
            sys.exit(1)

        sys.exit(0)

    except Exception as e:
        logger.error(f"Failed to fetch ICON-CH1 data: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
