#!/usr/bin/env python3
"""
Test script to run the full pipeline once:
1. Fetch ICON data from MeteoSwiss
2. Parse GRIB files
3. Calculate PV forecast with pvlib
4. Write to InfluxDB
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("test_pipeline")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.icon_fetcher import IconFetcher
from src.grib_parser import load_hybrid_ensemble_forecast
from src.pv_model import forecast_ensemble_plants
from src.influxdb_writer import ForecastWriter

# Configuration
LATITUDE = 47.475
LONGITUDE = 7.767
DATA_DIR = Path("/tmp/swisssolarforecast")

INFLUX_HOST = "192.168.0.203"
INFLUX_PORT = 8087
INFLUX_TOKEN = ""
INFLUX_ORG = "spiessa"
INFLUX_BUCKET = "pv_forecast"


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch ICON-CH1 data
    logger.info("=" * 60)
    logger.info("STEP 1: Fetching ICON-CH1 data from MeteoSwiss...")
    logger.info("=" * 60)

    fetcher = IconFetcher(
        model="ch1",
        latitude=LATITUDE,
        longitude=LONGITUDE,
        variables=["ASOB_S", "T_2M"],
        output_dir=DATA_DIR / "icon-ch1",
    )

    try:
        result = fetcher.fetch_latest()
        logger.info(f"CH1 fetch complete: {result}")
    except Exception as e:
        logger.error(f"CH1 fetch failed: {e}")
        logger.info("Trying to use existing data...")

    # Step 2: Load ensemble weather data from GRIB files
    logger.info("=" * 60)
    logger.info("STEP 2: Loading ensemble weather from GRIB files...")
    logger.info("=" * 60)

    try:
        ensemble_weather = load_hybrid_ensemble_forecast(DATA_DIR)
        if not ensemble_weather:
            logger.error("No ensemble data available!")
            return 1
        logger.info(f"Loaded {len(ensemble_weather)} ensemble members")

        # Show sample data
        member0 = ensemble_weather[0]
        logger.info(f"Member 0 data shape: {member0.shape}")
        logger.info(f"Time range: {member0.index.min()} to {member0.index.max()}")
        logger.info(f"Columns: {list(member0.columns)}")
        logger.info(f"Sample GHI values: {member0['ghi'].head(5).tolist()}")
    except Exception as e:
        logger.error(f"Failed to load GRIB data: {e}")
        raise

    # Step 3: Calculate PV forecast
    logger.info("=" * 60)
    logger.info("STEP 3: Calculating PV forecast with pvlib...")
    logger.info("=" * 60)

    try:
        pv_forecast = forecast_ensemble_plants(ensemble_weather)
        logger.info(f"Generated forecast with {len(pv_forecast)} time steps")
        logger.info(f"Columns: {list(pv_forecast.columns)}")

        # Show sample
        logger.info("\nSample forecast (first 10 rows where power > 0):")
        nonzero = pv_forecast[pv_forecast["total_ac_power_p50"] > 0].head(10)
        for idx, row in nonzero.iterrows():
            logger.info(f"  {idx}: P50={row['total_ac_power_p50']:.0f}W, "
                       f"GHI={row.get('ghi', 0):.0f} W/m², "
                       f"T={row.get('temp_air', 0):.1f}°C")
    except Exception as e:
        logger.error(f"Failed to calculate forecast: {e}")
        raise

    # Step 4: Write to InfluxDB
    logger.info("=" * 60)
    logger.info("STEP 4: Writing forecast to InfluxDB...")
    logger.info("=" * 60)

    try:
        writer = ForecastWriter(
            host=INFLUX_HOST,
            port=INFLUX_PORT,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            bucket=INFLUX_BUCKET,
        )
        writer.connect()

        run_time = datetime.now(timezone.utc)
        writer.write_pv_forecast(
            pv_forecast=pv_forecast,
            model="ch1",
            run_time=run_time,
            resample_minutes=15,
        )
        writer.close()

        logger.info("Forecast written successfully!")
    except Exception as e:
        logger.error(f"Failed to write to InfluxDB: {e}")
        raise

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE!")
    logger.info("=" * 60)
    logger.info(f"Check InfluxDB bucket '{INFLUX_BUCKET}' for forecast data")

    return 0


if __name__ == "__main__":
    sys.exit(main())
