"""
GRIB file parser for MeteoSwiss ICON forecast data.
Handles ICON's unstructured triangular grid for both CH1 and CH2 models.
"""

import eccodes
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional
import logging
import requests
import tempfile
import re

from .config import LATITUDE, LONGITUDE
from .notifications import notify_warning

logger = logging.getLogger(__name__)

# STAC API for grid coordinates
STAC_API_URL = "https://data.geo.admin.ch/api/stac/v1"

# Caches
_GRID_CACHE = {}  # Grid coordinates per model
_INDEX_CACHE = {}  # Nearest index per (lat, lon, model)
_FILENAME_CACHE = {}  # Parsed filename metadata


def get_grid_coords(model: str = "ch2", cache_dir: Optional[Path] = None) -> tuple[np.ndarray, np.ndarray]:
    """
    Load ICON grid coordinates, downloading if necessary.
    
    Args:
        model: "ch1" or "ch2"
        cache_dir: Directory for cached grid files
    """
    global _GRID_CACHE
    
    cache_key = f"{model}_lats"
    if cache_key in _GRID_CACHE:
        return _GRID_CACHE[f"{model}_lats"], _GRID_CACHE[f"{model}_lons"]
    
    if cache_dir is None:
        cache_dir = Path(tempfile.gettempdir()) / "meteoswiss_grib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    cache_file = cache_dir / f"grid_coords_{model}.npz"
    if cache_file.exists():
        data = np.load(cache_file)
        _GRID_CACHE[f"{model}_lats"] = data['lats']
        _GRID_CACHE[f"{model}_lons"] = data['lons']
        return _GRID_CACHE[f"{model}_lats"], _GRID_CACHE[f"{model}_lons"]
    
    logger.info(f"Downloading ICON-{model.upper()} grid coordinates...")
    collection = f"ch.meteoschweiz.ogd-forecasting-icon-{model}"
    url = f"{STAC_API_URL}/collections/{collection}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    
    # Find the horizontal constants file
    grid_asset_key = f"horizontal_constants_icon-{model}-eps.grib2"
    if grid_asset_key not in data.get('assets', {}):
        # Try alternative naming
        for key in data.get('assets', {}).keys():
            if 'horizontal_constants' in key:
                grid_asset_key = key
                break
    
    grid_url = data['assets'][grid_asset_key]['href']
    grid_path = cache_dir / f"grid_constants_{model}.grib2"
    
    r = requests.get(grid_url, timeout=120)
    r.raise_for_status()
    grid_path.write_bytes(r.content)
    
    lats = None
    lons = None
    
    with open(grid_path, 'rb') as f:
        while True:
            msg = eccodes.codes_grib_new_from_file(f)
            if msg is None:
                break
            try:
                name = eccodes.codes_get(msg, 'shortName')
            except:
                name = 'unknown'
            if name == 'tlat':
                lats = eccodes.codes_get_array(msg, 'values')
            elif name == 'tlon':
                lons = eccodes.codes_get_array(msg, 'values')
            eccodes.codes_release(msg)
    
    if lats is None or lons is None:
        raise RuntimeError(f"Could not extract grid coordinates for {model}")
    
    # Convert from radians if necessary
    if lats.max() < 2:
        lats = np.degrees(lats)
        lons = np.degrees(lons)
    
    np.savez(cache_file, lats=lats, lons=lons)
    _GRID_CACHE[f"{model}_lats"] = lats
    _GRID_CACHE[f"{model}_lons"] = lons
    
    return lats, lons


def find_nearest_index(lat: float, lon: float, model: str = "ch2") -> int:
    """Find the grid index nearest to a given location. Results are cached."""
    cache_key = (lat, lon, model)
    if cache_key in _INDEX_CACHE:
        return _INDEX_CACHE[cache_key]

    lats, lons = get_grid_coords(model)
    dist = np.sqrt((lats - lat)**2 + (lons - lon)**2)
    idx = int(np.argmin(dist))
    _INDEX_CACHE[cache_key] = idx
    return idx


def parse_filename(path: Path) -> dict:
    """
    Extract metadata from GRIB filename. Results are cached.

    Returns dict with: model, run_time, forecast_hour, variable, member
    """
    cache_key = str(path)
    if cache_key in _FILENAME_CACHE:
        return _FILENAME_CACHE[cache_key]

    name = path.stem
    result = {}

    # Try to extract model (ch1 or ch2)
    model_match = re.search(r'(ch[12])', name, re.IGNORECASE)
    if model_match:
        result['model'] = model_match.group(1).lower()

    # Try to extract variable name (common PV variables)
    var_patterns = ['asob_s', 'aswdir_s', 'aswdifd_s', 't_2m', 'u_10m', 'v_10m',
                    'clct', 'clch', 'clcm', 'clcl', 'tot_prec']
    for var in var_patterns:
        if var in name.lower():
            result['variable'] = var.lower()
            break

    # Try to extract ensemble member
    # Patterns: m00 (control), m01-m10 (perturbed), ctrl, perturbed
    member_match = re.search(r'[_-](m\d{1,2}|ctrl|perturbed)[._-]?', name, re.IGNORECASE)
    if member_match:
        member_str = member_match.group(1).lower()
        if member_str == 'ctrl' or member_str == 'm00':
            result['member'] = 0
        elif member_str == 'perturbed':
            result['member'] = 1  # Indicates perturbed file (contains all 10 members)
        else:
            result['member'] = int(member_str[1:])
    else:
        result['member'] = 0  # Default to control

    # Try to extract forecast hour - multiple patterns
    # Pattern 1: -hHHH- (e.g., -h003-)
    hour_match = re.search(r'-h(\d{2,3})-', name)
    if hour_match:
        result['forecast_hour'] = int(hour_match.group(1))
    else:
        # Pattern 2: -HH- between timestamp and variable (legacy)
        hour_match = re.search(r'-(\d{1,3})-[a-z]', name, re.IGNORECASE)
        if hour_match:
            result['forecast_hour'] = int(hour_match.group(1))

    # Try to extract run time (12-14 digit timestamp)
    time_match = re.search(r'(\d{12,14})', name)
    if time_match:
        result['run_time'] = time_match.group(1)[:12]

    _FILENAME_CACHE[cache_key] = result
    return result


def read_grib_at_location(
    grib_path: Path,
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
    model: str = None,
) -> dict:
    """
    Read GRIB file and extract value at nearest grid point.

    Uses GRIB metadata as authoritative source for date/time,
    with filename parsing as fallback for variable/member info.
    """
    # Get metadata from filename (for variable, member info)
    file_meta = parse_filename(grib_path)

    # Determine model from filename if not specified
    if model is None:
        model = file_meta.get('model', 'ch2')

    nearest_idx = find_nearest_index(lat, lon, model)

    result = {
        'model': model,
        'variable': file_meta.get('variable', 'unknown'),
        'forecast_hour': file_meta.get('forecast_hour'),
        'value': None,
        'valid_time': None,
        'reference_time': None,
        'unit': None,
    }

    try:
        with open(grib_path, 'rb') as f:
            msg = eccodes.codes_grib_new_from_file(f)
            if msg is None:
                return result

            try:
                result['unit'] = eccodes.codes_get(msg, 'units')
            except Exception:
                pass

            # Get valid time from GRIB metadata (authoritative) - MeteoSwiss data is in UTC
            try:
                val_date = eccodes.codes_get(msg, 'validityDate')
                val_time = eccodes.codes_get(msg, 'validityTime')
                result['valid_time'] = pd.Timestamp(
                    f"{val_date // 10000}-{(val_date // 100) % 100:02d}-{val_date % 100:02d} "
                    f"{val_time // 100:02d}:{val_time % 100:02d}:00",
                    tz='UTC'
                )
            except Exception:
                pass

            # Get reference time (model run time) from GRIB metadata - MeteoSwiss data is in UTC
            try:
                ref_date = eccodes.codes_get(msg, 'dataDate')
                ref_time = eccodes.codes_get(msg, 'dataTime')
                result['reference_time'] = pd.Timestamp(
                    f"{ref_date // 10000}-{(ref_date // 100) % 100:02d}-{ref_date % 100:02d} "
                    f"{ref_time // 100:02d}:{ref_time % 100:02d}:00",
                    tz='UTC'
                )
            except Exception:
                pass

            # Get forecast hour from GRIB if not from filename
            if result['forecast_hour'] is None:
                try:
                    result['forecast_hour'] = eccodes.codes_get(msg, 'forecastTime')
                except Exception:
                    pass

            # Get variable name from GRIB if not from filename
            if result['variable'] == 'unknown':
                try:
                    result['variable'] = eccodes.codes_get(msg, 'shortName')
                except Exception:
                    pass

            # Extract values at location
            values = eccodes.codes_get_array(msg, 'values')
            result['value'] = float(values[nearest_idx])

            eccodes.codes_release(msg)

    except Exception as e:
        logger.debug(f"Error reading {grib_path.name}: {e}")

    return result


def read_grib_all_members(
    grib_path: Path,
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
    model: str = None,
) -> list[dict]:
    """
    Read all GRIB messages from a file (for multi-member perturbed files).

    Returns a list of dicts, one per member, with member number from perturbationNumber.
    """
    file_meta = parse_filename(grib_path)

    if model is None:
        model = file_meta.get('model', 'ch2')

    nearest_idx = find_nearest_index(lat, lon, model)

    results = []

    try:
        with open(grib_path, 'rb') as f:
            while True:
                msg = eccodes.codes_grib_new_from_file(f)
                if msg is None:
                    break

                result = {
                    'model': model,
                    'variable': file_meta.get('variable', 'unknown'),
                    'forecast_hour': file_meta.get('forecast_hour'),
                    'value': None,
                    'valid_time': None,
                    'reference_time': None,
                    'unit': None,
                    'member': 0,
                }

                try:
                    result['unit'] = eccodes.codes_get(msg, 'units')
                except Exception:
                    pass

                # Get perturbation number (0 = control, 1+ = perturbed)
                try:
                    result['member'] = eccodes.codes_get(msg, 'perturbationNumber')
                except Exception:
                    pass

                # Get valid time from GRIB metadata - MeteoSwiss data is in UTC
                try:
                    val_date = eccodes.codes_get(msg, 'validityDate')
                    val_time = eccodes.codes_get(msg, 'validityTime')
                    result['valid_time'] = pd.Timestamp(
                        f"{val_date // 10000}-{(val_date // 100) % 100:02d}-{val_date % 100:02d} "
                        f"{val_time // 100:02d}:{val_time % 100:02d}:00",
                        tz='UTC'
                    )
                except Exception:
                    pass

                # Get reference time from GRIB metadata - MeteoSwiss data is in UTC
                try:
                    ref_date = eccodes.codes_get(msg, 'dataDate')
                    ref_time = eccodes.codes_get(msg, 'dataTime')
                    result['reference_time'] = pd.Timestamp(
                        f"{ref_date // 10000}-{(ref_date // 100) % 100:02d}-{ref_date % 100:02d} "
                        f"{ref_time // 100:02d}:{ref_time % 100:02d}:00",
                        tz='UTC'
                    )
                except Exception:
                    pass

                # Get forecast hour from GRIB if not from filename
                if result['forecast_hour'] is None:
                    try:
                        result['forecast_hour'] = eccodes.codes_get(msg, 'forecastTime')
                    except Exception:
                        pass

                # Get variable name from GRIB if not from filename
                if result['variable'] == 'unknown':
                    try:
                        result['variable'] = eccodes.codes_get(msg, 'shortName')
                    except Exception:
                        pass

                # Extract value at location
                values = eccodes.codes_get_array(msg, 'values')
                result['value'] = float(values[nearest_idx])

                eccodes.codes_release(msg)
                results.append(result)

    except Exception as e:
        logger.debug(f"Error reading {grib_path.name}: {e}")

    return results


def extract_pv_weather(
    grib_paths: list[Path],
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
) -> pd.DataFrame:
    """
    Extract weather variables needed for PV modeling from multiple GRIB files.
    """
    # Group files by time and variable
    data_by_time = {}
    
    for path in grib_paths:
        try:
            result = read_grib_at_location(path, lat, lon)
            
            if result['valid_time'] is None or result['value'] is None:
                continue
            
            time = result['valid_time']
            var = result['variable']
            
            if time not in data_by_time:
                data_by_time[time] = {}
            
            data_by_time[time][var] = result['value']
            
        except Exception as e:
            logger.error(f"Failed to parse {path.name}: {e}")
    
    if not data_by_time:
        logger.warning("No data extracted from GRIB files")
        return pd.DataFrame()
    
    # Build DataFrame
    df = pd.DataFrame.from_dict(data_by_time, orient='index')
    df.index.name = 'time'
    df = df.sort_index()
    
    logger.info(f"Extracted data for {len(df)} time steps: {list(df.columns)}")
    
    # Map to standard PV variable names
    result = pd.DataFrame(index=df.index)
    
    # GHI from net shortwave radiation
    if 'asob_s' in df.columns:
        result['ghi'] = df['asob_s'].clip(lower=0)
    
    # Direct if available
    if 'aswdir_s' in df.columns:
        result['dni'] = df['aswdir_s'].clip(lower=0)
    
    # Diffuse if available  
    if 'aswdifd_s' in df.columns:
        result['dhi'] = df['aswdifd_s'].clip(lower=0)
    
    # Temperature (K to C)
    if 't_2m' in df.columns:
        temp = df['t_2m']
        if temp.mean() > 100:
            temp = temp - 273.15
        result['temp_air'] = temp
    else:
        result['temp_air'] = 5.0  # Winter default
    
    # Wind
    if 'u_10m' in df.columns:
        result['wind_speed'] = np.abs(df['u_10m'])
    else:
        result['wind_speed'] = 2.0
    
    return result


def load_local_forecast(
    forecast_dir: Path,
    model: str = "ch2",
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
) -> pd.DataFrame:
    """
    Load forecast data from local forecastData directory.

    Args:
        forecast_dir: Base forecastData directory
        model: "ch1" or "ch2"
        lat: Location latitude
        lon: Location longitude

    Returns:
        DataFrame with weather variables for PV modeling
    """
    model_dir = forecast_dir / f"icon-{model}"

    if not model_dir.exists():
        raise FileNotFoundError(f"No local data for {model}: {model_dir}")

    # Find the latest run directory
    runs = [d for d in model_dir.iterdir() if d.is_dir()]
    if not runs:
        raise FileNotFoundError(f"No forecast runs found in {model_dir}")

    latest_run = max(runs, key=lambda d: d.name)
    logger.info(f"Using local {model.upper()} run: {latest_run.name}")

    # Get all GRIB files
    grib_files = list(latest_run.glob("*.grib2"))
    if not grib_files:
        raise FileNotFoundError(f"No GRIB files in {latest_run}")

    logger.info(f"Found {len(grib_files)} GRIB files")

    return extract_pv_weather(grib_files, lat, lon)


def deaccumulate_avg(values: np.ndarray, hours: np.ndarray) -> np.ndarray:
    """
    Convert running average to hourly values (vectorized).

    MeteoSwiss radiation variables (ASOB_S, etc.) are time-mean values from hour 0.
    Formula: hourly(h) = avg(h) * h - avg(h-1) * (h-1)
    """
    result = np.zeros_like(values, dtype=float)
    result[0] = values[0]
    if len(values) > 1:
        prev_h = np.concatenate([[0], hours[:-1]])
        prev_val = np.concatenate([[0], values[:-1]])
        result[1:] = values[1:] * hours[1:] - prev_val[1:] * prev_h[1:]
    return np.clip(result, 0, None)


def extract_ensemble_weather(
    grib_paths: list[Path],
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
) -> dict[int, pd.DataFrame]:
    """
    Extract weather for all ensemble members from GRIB files.

    Returns:
        Dict mapping member number to DataFrame with weather variables
    """
    # Filter out incomplete downloads (.tmp files)
    valid_paths = [p for p in grib_paths if p.suffix.lower() == '.grib2']
    skipped = len(grib_paths) - len(valid_paths)
    if skipped > 0:
        logger.debug(f"Skipped {skipped} incomplete files (.tmp)")

    # Separate control files from perturbed files
    control_paths = []
    perturbed_paths = []

    for path in valid_paths:
        file_meta = parse_filename(path)
        member = file_meta.get('member', 0)
        if member == 0:
            control_paths.append(path)
        else:
            perturbed_paths.append(path)

    # Group files by member and then by time/variable
    data_by_member = {}
    reference_time = None  # Track model run time for de-accumulation
    parse_errors = 0
    read_errors = 0

    # Process control files (single member per file)
    for path in control_paths:
        try:
            result = read_grib_at_location(path, lat, lon)

            if result['valid_time'] is None:
                parse_errors += 1
                continue

            if result['value'] is None:
                read_errors += 1
                continue

            time = result['valid_time']
            var = result['variable']

            if var == 'unknown':
                file_meta = parse_filename(path)
                var = file_meta.get('variable', 'unknown')
                if var == 'unknown':
                    continue

            # Capture reference time for de-accumulation
            if reference_time is None and result.get('reference_time') is not None:
                reference_time = result['reference_time']

            if 0 not in data_by_member:
                data_by_member[0] = {}

            if time not in data_by_member[0]:
                data_by_member[0][time] = {}

            data_by_member[0][time][var] = result['value']

        except Exception as e:
            logger.debug(f"Failed to parse control file {path.name}: {e}")
            parse_errors += 1

    # Process perturbed files (multiple members per file)
    # Track which files we've already processed to avoid reading duplicates
    # (m01.grib2, m02.grib2, etc. might be the same file content)
    processed_perturbed = set()

    for path in perturbed_paths:
        try:
            file_meta = parse_filename(path)
            var = file_meta.get('variable', 'unknown')
            hour = file_meta.get('forecast_hour', 0)

            # Create a key to identify unique perturbed files (var + hour)
            # This avoids reading duplicate m01/m02/m03 files that all contain the same data
            file_key = f"{var}-h{hour:03d}"
            if file_key in processed_perturbed:
                continue
            processed_perturbed.add(file_key)

            # Read all members from this perturbed file
            results = read_grib_all_members(path, lat, lon)

            for result in results:
                if result['valid_time'] is None:
                    parse_errors += 1
                    continue

                if result['value'] is None:
                    read_errors += 1
                    continue

                member = result.get('member', 1)  # perturbationNumber from GRIB
                time = result['valid_time']
                result_var = result['variable']

                if result_var == 'unknown':
                    result_var = var
                    if result_var == 'unknown':
                        continue

                if member not in data_by_member:
                    data_by_member[member] = {}

                if time not in data_by_member[member]:
                    data_by_member[member][time] = {}

                data_by_member[member][time][result_var] = result['value']

        except Exception as e:
            logger.debug(f"Failed to parse perturbed file {path.name}: {e}")
            parse_errors += 1

    if parse_errors > 0:
        msg = f"Could not parse {parse_errors} GRIB files"
        logger.warning(msg)
        notify_warning("GRIB Parse Error", msg)
    if read_errors > 0:
        msg = f"Could not read values from {read_errors} GRIB files"
        logger.warning(msg)
        notify_warning("GRIB Read Error", msg)

    if not data_by_member:
        msg = "No ensemble data extracted from GRIB files"
        logger.warning(msg)
        notify_warning("Ensemble Data Missing", msg)
        return {}

    logger.info(f"Extracted ensemble data for members: {sorted(data_by_member.keys())}")

    # Convert to DataFrames
    result = {}
    for member, data_by_time in data_by_member.items():
        df = pd.DataFrame.from_dict(data_by_time, orient='index')
        df.index.name = 'time'
        df = df.sort_index()

        # Map to standard PV variable names
        weather = pd.DataFrame(index=df.index)

        # Calculate forecast hours from model reference time for de-accumulation
        # ICON radiation (asob_s) is running average from hour 0, so we need actual forecast hours
        if reference_time is not None:
            hours = np.array([(t - reference_time).total_seconds() / 3600 for t in df.index])
        else:
            # Fallback: assume hourly data starting at hour 1
            hours = np.arange(1, len(df) + 1, dtype=float)

        logger.debug(f"Member {member}: forecast hours range {hours[0]:.0f} to {hours[-1]:.0f}")

        # De-accumulate radiation variables (running averages -> hourly)
        if 'asob_s' in df.columns:
            weather['ghi'] = deaccumulate_avg(df['asob_s'].clip(lower=0).values, hours)

        if 'aswdir_s' in df.columns:
            weather['dni'] = deaccumulate_avg(df['aswdir_s'].clip(lower=0).values, hours)

        if 'aswdifd_s' in df.columns:
            weather['dhi'] = deaccumulate_avg(df['aswdifd_s'].clip(lower=0).values, hours)
        if 't_2m' in df.columns:
            temp = df['t_2m']
            if temp.mean() > 100:
                temp = temp - 273.15
            weather['temp_air'] = temp
        else:
            weather['temp_air'] = 5.0
        if 'u_10m' in df.columns:
            weather['wind_speed'] = np.abs(df['u_10m'])
        else:
            weather['wind_speed'] = 2.0

        result[member] = weather

    logger.info(f"Extracted ensemble data for {len(result)} members")
    return result


def load_ensemble_forecast(
    forecast_dir: Path,
    model: str = "ch2",
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
) -> dict[int, pd.DataFrame]:
    """
    Load ensemble forecast data from local forecastData directory.

    Fault-tolerant: handles incomplete downloads and various filename formats.

    Args:
        forecast_dir: Base forecastData directory
        model: "ch1" or "ch2"
        lat: Location latitude
        lon: Location longitude

    Returns:
        Dict mapping member number (0=control, 1-N=perturbed) to DataFrame
    """
    model_dir = forecast_dir / f"icon-{model}"

    if not model_dir.exists():
        raise FileNotFoundError(f"No local data for {model}: {model_dir}")

    # Find the latest run directory
    runs = [d for d in model_dir.iterdir() if d.is_dir()]
    if not runs:
        raise FileNotFoundError(f"No forecast runs found in {model_dir}")

    latest_run = max(runs, key=lambda d: d.name)
    logger.info(f"Using local {model.upper()} run: {latest_run.name}")

    # Get all GRIB files (complete downloads only)
    grib_files = list(latest_run.glob("*.grib2"))
    tmp_files = list(latest_run.glob("*.tmp"))

    if not grib_files:
        if tmp_files:
            raise FileNotFoundError(
                f"No complete GRIB files in {latest_run} "
                f"({len(tmp_files)} incomplete downloads found)"
            )
        raise FileNotFoundError(f"No GRIB files in {latest_run}")

    logger.info(f"Found {len(grib_files)} complete GRIB files")
    if tmp_files:
        msg = f"Found {len(tmp_files)} incomplete downloads (.tmp files) in {latest_run.name}"
        logger.warning(msg)
        notify_warning("Incomplete Downloads", msg)

    return extract_ensemble_weather(grib_files, lat, lon)


def load_hybrid_ensemble_forecast(
    forecast_dir: Path,
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
    ch1_hours: tuple[int, int] = (0, 33),
    ch2_hours: tuple[int, int] = (33, 48),
) -> dict[int, pd.DataFrame]:
    """
    Load hybrid CH1+CH2 ensemble forecast data.

    - CH1: hours 0-33 (higher resolution, 11 members)
    - CH2: hours 33-48 (longer horizon, 21 members)

    For hours covered by CH1, uses CH1 data. For hours beyond CH1, uses CH2.
    Members are normalized: returns data for members 0-10 (CH1 member count).

    Args:
        forecast_dir: Base forecastData directory
        lat: Location latitude
        lon: Location longitude
        ch1_hours: (start, end) hours for CH1 data
        ch2_hours: (start, end) hours for CH2 data (only hours > ch1_hours[1] used)

    Returns:
        Dict mapping member number to DataFrame with combined weather
    """
    result = {}

    # Load CH1 ensemble
    try:
        ch1_data = load_ensemble_forecast(forecast_dir, model="ch1", lat=lat, lon=lon)
        logger.info(f"Loaded CH1 ensemble: {len(ch1_data)} members")
    except FileNotFoundError:
        msg = "No CH1 data available, using CH2 only"
        logger.warning(msg)
        notify_warning("CH1 Data Missing", msg)
        ch1_data = {}

    # Load CH2 ensemble (for hours beyond CH1)
    try:
        ch2_data = load_ensemble_forecast(forecast_dir, model="ch2", lat=lat, lon=lon)
        logger.info(f"Loaded CH2 ensemble: {len(ch2_data)} members")
    except FileNotFoundError:
        msg = "No CH2 data available"
        logger.warning(msg)
        notify_warning("CH2 Data Missing", msg)
        ch2_data = {}

    if not ch1_data and not ch2_data:
        raise FileNotFoundError("No forecast data available for either CH1 or CH2")

    # Determine number of members to use (min of both, or available)
    if ch1_data and ch2_data:
        n_members = min(len(ch1_data), len(ch2_data))
    elif ch1_data:
        n_members = len(ch1_data)
    else:
        n_members = len(ch2_data)

    # Combine data for each member
    for member in range(n_members):
        dfs_to_concat = []

        # CH1 data (hours 0-33)
        if member in ch1_data:
            df_ch1 = ch1_data[member].copy()
            if not df_ch1.empty:
                dfs_to_concat.append(df_ch1)

        # CH2 data (hours beyond CH1)
        # Map CH2 members to CH1 members (CH2 has more members)
        ch2_member = member
        if ch2_member in ch2_data:
            df_ch2 = ch2_data[ch2_member].copy()
            if not df_ch2.empty:
                # Only keep hours not already covered by CH1
                if dfs_to_concat:
                    ch1_end = dfs_to_concat[0].index.max()
                    df_ch2 = df_ch2[df_ch2.index > ch1_end]
                if not df_ch2.empty:
                    dfs_to_concat.append(df_ch2)

        if dfs_to_concat:
            combined = pd.concat(dfs_to_concat)
            combined = combined.sort_index()
            # Remove duplicates (keep first = CH1 where overlap)
            combined = combined[~combined.index.duplicated(keep='first')]
            result[member] = combined

    logger.info(f"Combined hybrid forecast: {len(result)} members, "
                f"{len(result[0]) if result else 0} time steps")

    return result
