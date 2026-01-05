#!/usr/bin/env python3
"""
PV Power Forecast - Main Entry Point

Fetches MeteoSwiss ICON forecast data and calculates PV power output
using pvlib. Supports hierarchical config: plants -> inverters -> strings -> panels

Usage:
    python pv_forecast.py [--date YYYY-MM-DD] [--output FILE]
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.config import PLANTS, get_total_dc_power
from src.meteoswiss_fetch import fetch_forecast_data
from src.grib_parser import extract_pv_weather
from src.pv_model import forecast_all_plants

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate PV power forecast using MeteoSwiss ICON data"
    )
    parser.add_argument(
        "--date",
        type=str,
        help="Target date (YYYY-MM-DD). Default: tomorrow",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output CSV file path. Default: prints to console",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


def print_config_summary():
    """Print configuration summary."""
    total_dc = get_total_dc_power()
    
    logger.info("PV Configuration:")
    for plant in PLANTS:
        logger.info(f"  Plant: {plant['name']}")
        loc = plant['location']
        logger.info(f"    Location: {loc['latitude']:.4f}, {loc['longitude']:.4f}")
        
        for inverter in plant['inverters']:
            inv_dc = inverter['total_dc_power']
            logger.info(f"    Inverter: {inverter['name']} "
                       f"(max={inverter['max_power']}W, eff={inverter['efficiency']:.0%})")
            
            for string in inverter['strings']:
                logger.info(f"      String: {string['name']} - "
                           f"{string['count']}x {string['panel']['model']} "
                           f"({string['dc_power']}W DC), "
                           f"az={string['azimuth']}°, tilt={string['tilt']}°")
    
    logger.info(f"  Total installed: {total_dc}W DC")


def main():
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Determine target date
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        target_date = datetime.now() + timedelta(days=1)
    
    logger.info(f"Generating PV forecast for: {target_date.date()}")
    
    # Print configuration
    print_config_summary()
    
    # Step 1: Fetch MeteoSwiss forecast data
    logger.info("Fetching MeteoSwiss ICON forecast data...")
    try:
        grib_paths = fetch_forecast_data(target_date)
        if not grib_paths:
            logger.error("No forecast data available for target date")
            sys.exit(1)
        logger.info(f"Downloaded {len(grib_paths)} GRIB files")
    except Exception as e:
        logger.error(f"Failed to fetch forecast data: {e}")
        sys.exit(1)
    
    # Step 2: Parse GRIB files and extract weather data
    logger.info("Parsing weather data...")
    try:
        weather = extract_pv_weather(grib_paths)
        if weather.empty:
            logger.error("No weather data extracted from GRIB files")
            sys.exit(1)
        logger.info(f"Extracted {len(weather)} weather records")
        logger.info(f"Time range: {weather.index.min()} to {weather.index.max()}")
    except Exception as e:
        logger.error(f"Failed to parse weather data: {e}")
        sys.exit(1)
    
    # Step 3: Calculate PV power forecast
    logger.info("Calculating PV power forecast...")
    try:
        forecast = forecast_all_plants(weather)
        logger.info(f"Generated forecast with {len(forecast)} time steps")
    except Exception as e:
        logger.error(f"Failed to calculate forecast: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Step 4: Output results
    if args.output:
        output_path = Path(args.output)
        forecast.to_csv(output_path)
        logger.info(f"Saved forecast to: {output_path}")
    else:
        # Get inverter names dynamically
        inverter_names = []
        for plant in PLANTS:
            for inverter in plant['inverters']:
                inverter_names.append(inverter['name'])
        
        # Print summary to console
        print("\n" + "="*65)
        print(f"PV POWER FORECAST - {target_date.date()}")
        print("="*65)
        
        # Daily energy summary
        print(f"\nEstimated Daily Energy Production:")
        total_energy = 0
        for inv_name in inverter_names:
            col = f"{inv_name}_ac_power"
            if col in forecast.columns:
                energy = forecast[col].sum() / 1000 * (24 / len(forecast))
                total_energy += energy
                print(f"  {inv_name:15s}: {energy:6.2f} kWh")
        print(f"  {'TOTAL':15s}: {total_energy:6.2f} kWh")
        
        # Peak power
        if "total_ac_power" in forecast.columns:
            peak_power = forecast["total_ac_power"].max()
            peak_time = forecast["total_ac_power"].idxmax()
            print(f"\nPeak Power: {peak_power:.0f} W at {peak_time}")
        
        # Hourly breakdown
        print("\nHourly Forecast (AC Power in Watts):")
        print("-"*65)
        
        # Resample to hourly if needed
        if len(forecast) > 48:
            hourly = forecast.resample("h").mean()
        else:
            hourly = forecast
        
        # Build dynamic header
        header = f"{'Time':>8s}"
        for inv_name in inverter_names:
            # Truncate long names
            display_name = inv_name[:12] if len(inv_name) > 12 else inv_name
            header += f"  {display_name:>12s}"
        header += f"  {'TOTAL':>10s}"
        if "ghi" in hourly.columns:
            header += f"  {'GHI':>6s}"
        print(header)
        print("-"*65)
        
        for idx, row in hourly.iterrows():
            time_str = idx.strftime("%H:%M") if hasattr(idx, 'strftime') else str(idx)[:5]
            line = f"{time_str:>8s}"
            
            for inv_name in inverter_names:
                col = f"{inv_name}_ac_power"
                if col in row:
                    line += f"  {row[col]:>12.0f}"
            
            if "total_ac_power" in row:
                line += f"  {row['total_ac_power']:>10.0f}"
            
            if "ghi" in row:
                line += f"  {row['ghi']:>6.0f}"
            
            print(line)
        
        print("="*65)


if __name__ == "__main__":
    main()
