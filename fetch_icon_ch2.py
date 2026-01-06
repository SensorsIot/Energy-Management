#!/usr/bin/env python3
"""
Fetch ICON-CH2-EPS forecast data from MeteoSwiss.
Scheduled to run every 6 hours via systemd timer.

ICON-CH2-EPS: 2.1km resolution, 5-day horizon, runs at 00,06,12,18 UTC
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.icon_fetcher import fetch_icon_data
from src.config import LATITUDE, LONGITUDE

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


def main():
    logger.info("Starting ICON-CH2-EPS fetch")
    logger.info(f"Location: {LATITUDE:.4f}, {LONGITUDE:.4f}")
    
    try:
        metadata = fetch_icon_data(
            model="ch2",
            latitude=LATITUDE,
            longitude=LONGITUDE,
            output_dir=FORECAST_DATA_DIR,
        )
        
        logger.info(f"Successfully fetched ICON-CH2 run: {metadata['run_str']}")
        logger.info(f"Files downloaded: {metadata['files_downloaded']}")
        logger.info(f"Files failed: {metadata['files_failed']}")
        
        if metadata['files_failed'] > 0:
            logger.warning("Some files failed to download")
            sys.exit(1)
            
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Failed to fetch ICON-CH2 data: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
