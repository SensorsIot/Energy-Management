"""
GRIB file parser for MeteoSwiss ICON forecast data.
Handles ICON's unstructured triangular grid.
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

from .config import STAC_API_URL, ICON_COLLECTION, LATITUDE, LONGITUDE

logger = logging.getLogger(__name__)

_GRID_CACHE = {}


def get_grid_coords(cache_dir: Optional[Path] = None) -> tuple[np.ndarray, np.ndarray]:
    """Load ICON grid coordinates, downloading if necessary."""
    global _GRID_CACHE
    
    if 'lats' in _GRID_CACHE and 'lons' in _GRID_CACHE:
        return _GRID_CACHE['lats'], _GRID_CACHE['lons']
    
    if cache_dir is None:
        cache_dir = Path(tempfile.gettempdir()) / "meteoswiss_grib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    cache_file = cache_dir / "grid_coords.npz"
    if cache_file.exists():
        data = np.load(cache_file)
        _GRID_CACHE['lats'] = data['lats']
        _GRID_CACHE['lons'] = data['lons']
        return _GRID_CACHE['lats'], _GRID_CACHE['lons']
    
    logger.info("Downloading ICON grid coordinates...")
    url = f"{STAC_API_URL}/collections/{ICON_COLLECTION}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    
    grid_url = data['assets']['horizontal_constants_icon-ch2-eps.grib2']['href']
    grid_path = cache_dir / "grid_constants.grib2"
    
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
        raise RuntimeError("Could not extract grid coordinates")
    
    if lats.max() < 2:
        lats = np.degrees(lats)
        lons = np.degrees(lons)
    
    np.savez(cache_file, lats=lats, lons=lons)
    _GRID_CACHE['lats'] = lats
    _GRID_CACHE['lons'] = lons
    
    return lats, lons


def find_nearest_index(lat: float, lon: float) -> int:
    """Find the grid index nearest to a given location."""
    lats, lons = get_grid_coords()
    dist = np.sqrt((lats - lat)**2 + (lons - lon)**2)
    return int(np.argmin(dist))


def parse_filename(path: Path) -> dict:
    """Extract metadata from GRIB filename."""
    # Pattern: icon-ch2-eps-YYYYMMDDHHMM-HOUR-VARIABLE-TYPE.grib2
    name = path.stem
    match = re.match(r'icon-ch2-eps-(\d{12})-(\d+)-(\w+)-(\w+)', name)
    if match:
        return {
            'run_time': match.group(1),
            'forecast_hour': int(match.group(2)),
            'variable': match.group(3),
            'type': match.group(4),
        }
    return {}


def read_grib_at_location(
    grib_path: Path,
    lat: float = LATITUDE,
    lon: float = LONGITUDE,
    ensemble_member: int = 0,
) -> dict:
    """Read GRIB file and extract value at nearest grid point."""
    nearest_idx = find_nearest_index(lat, lon)
    
    # Get metadata from filename
    file_meta = parse_filename(grib_path)
    
    result = {
        'variable': file_meta.get('variable', 'unknown'),
        'forecast_hour': file_meta.get('forecast_hour'),
        'value': None,
        'valid_time': None,
        'unit': None,
    }
    
    with open(grib_path, 'rb') as f:
        msg = eccodes.codes_grib_new_from_file(f)
        if msg is None:
            return result
        
        try:
            result['unit'] = eccodes.codes_get(msg, 'units')
        except:
            pass
        
        try:
            date = eccodes.codes_get(msg, 'validityDate')
            time = eccodes.codes_get(msg, 'validityTime')
            result['valid_time'] = pd.Timestamp(
                f"{date // 10000}-{(date // 100) % 100:02d}-{date % 100:02d} "
                f"{time // 100:02d}:{time % 100:02d}:00"
            )
        except:
            pass
        
        values = eccodes.codes_get_array(msg, 'values')
        n_points = 283876
        
        if len(values) > n_points:
            n_members = len(values) // n_points
            values = values.reshape(n_members, n_points)
            result['value'] = float(values[ensemble_member, nearest_idx])
        else:
            result['value'] = float(values[nearest_idx])
        
        eccodes.codes_release(msg)
    
    return result


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
