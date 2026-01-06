#!/usr/bin/env python3
"""
Fetch hybrid ICON-CH1+CH2 ensemble forecast data.

Downloads all ensemble members for:
- CH1: hours 0-33, 11 members (1 control + 10 perturbed)
- CH2: hours 33-48, 11 members (uses first 11 of 21 available)

This provides data for uncertainty bands (P10/P50/P90) in the PV forecast.
"""

import logging
import sys
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent))

from src.icon_fetcher import fetch_hybrid_forecast
from src.config import LATITUDE, LONGITUDE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

FORECAST_DATA_DIR = Path("/home/energymanagement/forecastData")


def main():
    logger.info("Starting hybrid ensemble forecast download...")
    logger.info(f"Location: {LATITUDE:.4f}, {LONGITUDE:.4f}")
    logger.info(f"Output: {FORECAST_DATA_DIR}")

    try:
        results = fetch_hybrid_forecast(
            latitude=LATITUDE,
            longitude=LONGITUDE,
            output_dir=FORECAST_DATA_DIR,
            target_hours=48,
        )

        logger.info("Download complete!")

        for model, meta in results.items():
            logger.info(f"  {model.upper()}: run={meta['run_str']}, "
                       f"files={meta['files_downloaded']}, "
                       f"failed={meta['files_failed']}")

        # Calculate total files
        total_files = sum(m['files_downloaded'] for m in results.values())
        total_failed = sum(m['files_failed'] for m in results.values())

        logger.info(f"Total: {total_files} files downloaded, {total_failed} failed")

        if total_failed > 0:
            logger.warning("Some files failed to download - forecast may have gaps")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Failed to fetch ensemble data: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
