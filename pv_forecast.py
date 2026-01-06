#!/usr/bin/env python3
"""
PV Power Forecast - Calculation Program

Reads locally stored ICON forecast data and calculates PV power output.
All modes use ensemble members for uncertainty bands (P10/P50/P90).

- CH1: 1km resolution, 33h horizon, 11 ensemble members
- CH2: 2.1km resolution, 120h horizon, 21 ensemble members

Usage:
    python pv_forecast.py --today           # Today with P10/P50/P90 (uses CH1)
    python pv_forecast.py --tomorrow        # Tomorrow with P10/P50/P90 (uses CH2, default)
    python pv_forecast.py --ensemble        # Hybrid CH1+CH2 for 48h with P10/P50/P90
    python pv_forecast.py --date 2026-01-15 # Specific date with P10/P50/P90
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
from src.grib_parser import load_local_forecast, load_ensemble_forecast, load_hybrid_ensemble_forecast
from src.pv_model import forecast_all_plants, forecast_ensemble_plants

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Local forecast data directory
FORECAST_DATA_DIR = Path("/home/energymanagement/forecastData")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate PV power forecast using local MeteoSwiss ICON data"
    )

    # Date selection (mutually exclusive)
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--today",
        action="store_true",
        help="Forecast for today with P10/P50/P90 uncertainty (uses ICON-CH1)",
    )
    date_group.add_argument(
        "--tomorrow",
        action="store_true",
        help="Forecast for tomorrow with P10/P50/P90 uncertainty (uses ICON-CH2, default)",
    )
    date_group.add_argument(
        "--date",
        type=str,
        help="Target date (YYYY-MM-DD)",
    )
    date_group.add_argument(
        "--ensemble",
        action="store_true",
        help="Hybrid CH1+CH2 ensemble forecast with P10/P50/P90 uncertainty bands",
    )

    # Model selection override
    parser.add_argument(
        "--model",
        type=str,
        choices=["ch1", "ch2", "auto"],
        default="auto",
        help="Force specific model (default: auto-select based on date)",
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


def select_model(target_date: datetime) -> str:
    """
    Auto-select the best model based on target date.

    - ICON-CH1: Higher resolution (1km), horizon 0-33h
    - ICON-CH2: Lower resolution (2.1km), horizon 33-48h (no overlap)
    - Hybrid: CH1 + CH2 combined for full 48h coverage

    Since CH2 only stores hours 33-48 to avoid redundancy,
    tomorrow's forecast needs hybrid mode.
    """
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target = target_date.replace(hour=0, minute=0, second=0, microsecond=0)

    days_ahead = (target - today).days

    if days_ahead == 0:
        # Today: CH1 alone is sufficient (0-33h covers today)
        return "ch1"
    else:
        # Tomorrow or later: need hybrid CH1+CH2
        return "hybrid"


def filter_weather_for_date(weather: pd.DataFrame, target_date: datetime) -> pd.DataFrame:
    """Filter weather data to only include the target date (in local time)."""
    import pytz

    # Convert target date to local timezone (Europe/Zurich)
    local_tz = pytz.timezone('Europe/Zurich')
    target_start = local_tz.localize(target_date.replace(hour=0, minute=0, second=0, microsecond=0))
    target_end = target_start + timedelta(days=1)

    # Convert weather index to local time for comparison if it's in UTC
    if weather.index.tz is not None:
        weather_local = weather.copy()
        weather_local.index = weather_local.index.tz_convert(local_tz)
        mask = (weather_local.index >= target_start) & (weather_local.index < target_end)
        return weather[mask]
    else:
        mask = (weather.index >= target_start.replace(tzinfo=None)) & (weather.index < target_end.replace(tzinfo=None))
        return weather[mask]


def print_ensemble_output(forecast: pd.DataFrame, date_label: str):
    """Print ensemble forecast with P10/P50/P90 uncertainty bands."""
    print("\n" + "="*80)
    print(f"PV POWER FORECAST WITH UNCERTAINTY - {date_label}")
    print("Model: Hybrid ICON-CH1 (0-33h) + ICON-CH2 (33-48h) Ensemble")
    print("="*80)

    # Daily energy summary with uncertainty
    hours_factor = 24 / len(forecast) if len(forecast) > 0 else 1

    if "total_ac_power_p50" in forecast.columns:
        energy_p10 = forecast["total_ac_power_p10"].sum() / 1000 * hours_factor
        energy_p50 = forecast["total_ac_power_p50"].sum() / 1000 * hours_factor
        energy_p90 = forecast["total_ac_power_p90"].sum() / 1000 * hours_factor

        print(f"\nEstimated Daily Energy Production:")
        print(f"  Pessimistic (P10): {energy_p10:6.2f} kWh")
        print(f"  Expected   (P50): {energy_p50:6.2f} kWh")
        print(f"  Optimistic (P90): {energy_p90:6.2f} kWh")
        print(f"  Uncertainty range: {energy_p90 - energy_p10:.2f} kWh")

        # Peak power
        peak_p50 = forecast["total_ac_power_p50"].max()
        peak_time = forecast["total_ac_power_p50"].idxmax()
        peak_p10 = forecast.loc[peak_time, "total_ac_power_p10"]
        peak_p90 = forecast.loc[peak_time, "total_ac_power_p90"]
        print(f"\nPeak Power at {peak_time}:")
        print(f"  P10: {peak_p10:.0f} W | P50: {peak_p50:.0f} W | P90: {peak_p90:.0f} W")

    # Hourly breakdown
    print("\nHourly Forecast (AC Power in Watts):")
    print("-"*80)

    # Resample to hourly if needed
    if len(forecast) > 48:
        hourly = forecast.resample("h").mean()
    else:
        hourly = forecast

    # Header
    print(f"{'Time':>8s}  {'P10':>8s}  {'P50':>8s}  {'P90':>8s}  {'Spread':>8s}  {'GHI':>6s}")
    print("-"*80)

    for idx, row in hourly.iterrows():
        time_str = idx.strftime("%H:%M") if hasattr(idx, 'strftime') else str(idx)[:5]

        p10 = row.get("total_ac_power_p10", 0)
        p50 = row.get("total_ac_power_p50", 0)
        p90 = row.get("total_ac_power_p90", 0)
        spread = row.get("ensemble_spread", p90 - p10)
        ghi = row.get("ghi", 0)

        print(f"{time_str:>8s}  {p10:>8.0f}  {p50:>8.0f}  {p90:>8.0f}  {spread:>8.0f}  {ghi:>6.0f}")

    print("="*80)
    print("\nLegend:")
    print("  P10 = Pessimistic (10th percentile) - 90% chance of exceeding this")
    print("  P50 = Expected (median) - 50% chance of exceeding this")
    print("  P90 = Optimistic (90th percentile) - 10% chance of exceeding this")


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle ensemble mode separately
    if args.ensemble:
        logger.info("Generating hybrid ensemble forecast with P10/P50/P90...")
        print_config_summary()

        try:
            ensemble_weather = load_hybrid_ensemble_forecast(FORECAST_DATA_DIR)
            logger.info(f"Loaded ensemble weather for {len(ensemble_weather)} members")
        except FileNotFoundError as e:
            logger.error(f"No ensemble forecast data: {e}")
            logger.error("Run fetch_icon_ch1.py and fetch_icon_ch2.py first")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to load ensemble data: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        try:
            forecast = forecast_ensemble_plants(ensemble_weather)
            logger.info(f"Generated ensemble forecast with {len(forecast)} time steps")
        except Exception as e:
            logger.error(f"Failed to calculate ensemble forecast: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        if args.output:
            output_path = Path(args.output)
            forecast.to_csv(output_path)
            logger.info(f"Saved ensemble forecast to: {output_path}")
        else:
            now = datetime.now()
            date_label = f"Today+Tomorrow ({now.date()} - {(now + timedelta(days=1)).date()})"
            print_ensemble_output(forecast, date_label)

        return

    # Standard mode with ensemble uncertainty
    now = datetime.now()
    if args.today:
        target_date = now
        date_label = "today"
    elif args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
        date_label = args.date
    else:
        # Default to tomorrow
        target_date = now + timedelta(days=1)
        date_label = "tomorrow"

    # Select model
    if args.model == "auto":
        model = select_model(target_date)
    else:
        model = args.model

    logger.info(f"Generating PV forecast for {date_label}: {target_date.date()}")
    if model == "hybrid":
        logger.info("Using hybrid ICON-CH1 (0-33h) + CH2 (33-48h) with ensemble uncertainty")
    else:
        logger.info(f"Using ICON-{model.upper()} model with ensemble uncertainty")

    # Print configuration
    print_config_summary()

    # Step 1: Load ensemble forecast data
    logger.info("Loading ensemble forecast data...")
    try:
        if model == "hybrid":
            ensemble_weather = load_hybrid_ensemble_forecast(FORECAST_DATA_DIR)
        else:
            ensemble_weather = load_ensemble_forecast(FORECAST_DATA_DIR, model=model)
        if not ensemble_weather:
            logger.error("No ensemble weather data available")
            sys.exit(1)
        logger.info(f"Loaded ensemble data for {len(ensemble_weather)} members")
        first_member = list(ensemble_weather.values())[0]
        logger.info(f"Time range: {first_member.index.min()} to {first_member.index.max()}")
    except FileNotFoundError as e:
        logger.error(f"No local forecast data: {e}")
        if model == "hybrid":
            logger.error("Run fetch_icon_ch1.py and fetch_icon_ch2.py first")
        else:
            logger.error(f"Run fetch_icon_{model}.py first")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load forecast data: {e}")
        sys.exit(1)

    # Step 2: Filter ensemble data for target date
    ensemble_filtered = {}
    for member, weather in ensemble_weather.items():
        filtered = filter_weather_for_date(weather, target_date)
        if not filtered.empty:
            ensemble_filtered[member] = filtered

    if not ensemble_filtered:
        logger.warning(f"No forecast data for {target_date.date()}, using all available data")
        ensemble_filtered = ensemble_weather
    else:
        first_filtered = list(ensemble_filtered.values())[0]
        logger.info(f"Filtered to {len(first_filtered)} records for {target_date.date()}")

    # Step 3: Calculate PV power forecast with uncertainty
    logger.info("Calculating ensemble PV power forecast...")
    try:
        forecast = forecast_ensemble_plants(ensemble_filtered)
        logger.info(f"Generated ensemble forecast with {len(forecast)} time steps")
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
        print_ensemble_output(forecast, f"{target_date.date()} ({date_label}) - ICON-{model.upper()}")


if __name__ == "__main__":
    main()
