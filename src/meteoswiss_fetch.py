"""
MeteoSwiss ICON-CH2-EPS forecast data fetcher via STAC API.
"""

import requests
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from typing import Optional
import logging

from .config import STAC_API_URL, ICON_COLLECTION, LATITUDE, LONGITUDE

logger = logging.getLogger(__name__)

# Variables needed for PV forecasting
PV_VARIABLES = ["asob_s", "aswdir_s", "aswdifd_s", "t_2m", "u_10m"]


def get_all_stac_items(bbox_delta: float = 0.1, max_pages: int = 100) -> list[dict]:
    """Fetch all STAC items with pagination."""
    bbox = [
        LONGITUDE - bbox_delta,
        LATITUDE - bbox_delta,
        LONGITUDE + bbox_delta,
        LATITUDE + bbox_delta,
    ]
    
    all_items = []
    url = f"{STAC_API_URL}/search"
    params = {
        "collections": ICON_COLLECTION,
        "bbox": ",".join(map(str, bbox)),
        "limit": 500,
    }
    
    next_url = None
    for page in range(max_pages):
        if next_url:
            r = requests.get(next_url, timeout=120)
        else:
            r = requests.get(url, params=params, timeout=120)
        
        r.raise_for_status()
        data = r.json()
        items = data.get("features", [])
        all_items.extend(items)
        
        links = data.get("links", [])
        next_url = None
        for link in links:
            if link.get("rel") == "next":
                next_url = link.get("href")
                break
        
        if not next_url or not items:
            break
    
    logger.info(f"Fetched {len(all_items)} STAC items total")
    return all_items


def filter_items_for_date(
    items: list[dict],
    target_date: datetime,
    variables: list[str] = PV_VARIABLES,
) -> dict[int, dict[str, str]]:
    """
    Filter items to get URLs for each forecast hour on target date.
    
    Returns:
        Dict mapping forecast_hour -> {variable: asset_url}
    """
    # Calculate which forecast hours correspond to target date
    # Assuming 00:00 model run on previous day
    model_run_date = target_date - timedelta(days=1)
    
    hours_for_date = {}  # hour -> {var: url}
    
    for item in items:
        item_id = item.get("id", "")
        parts = item_id.split("-")
        
        if len(parts) < 4:
            continue
        
        try:
            # Parse item ID: MMDDYYYY-HHMM-HOUR-VARIABLE-TYPE-HASH
            date_str = parts[0]
            run_time = parts[1]
            hour = int(parts[2])
            var = parts[3]
            
            # Check if this is for our target date
            # Model run at 00:00: hours 24-47 = next day 00:00-23:00
            # Model run at 12:00: hours 12-35 = next day 00:00-23:00
            
            if run_time == "0000" and 24 <= hour <= 47:
                hour_of_day = hour - 24
            elif run_time == "1200" and 12 <= hour <= 35:
                hour_of_day = hour - 12
            else:
                continue
            
            if var not in variables:
                continue
            
            # Get asset URL
            assets = item.get("assets", {})
            if not assets:
                continue
            
            asset_url = list(assets.values())[0].get("href")
            if not asset_url:
                continue
            
            if hour not in hours_for_date:
                hours_for_date[hour] = {}
            hours_for_date[hour][var] = asset_url
            
        except (ValueError, IndexError):
            continue
    
    logger.info(f"Found {len(hours_for_date)} forecast hours for target date")
    return hours_for_date


def download_grib(asset_url: str, output_dir: Optional[Path] = None) -> Path:
    """Download a GRIB file from asset URL."""
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir()) / "meteoswiss_grib"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = asset_url.split("/")[-1].split("?")[0]
    output_path = output_dir / filename
    
    if output_path.exists():
        logger.debug(f"Using cached: {filename}")
        return output_path
    
    logger.info(f"Downloading: {filename}")
    response = requests.get(asset_url, timeout=300, stream=True)
    response.raise_for_status()
    
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    return output_path


def fetch_forecast_data(
    target_date: datetime,
    output_dir: Optional[Path] = None,
    hours: Optional[list[int]] = None,
) -> list[Path]:
    """
    Fetch forecast data for a target date.
    
    Args:
        target_date: Date to fetch forecast for
        output_dir: Directory to save GRIB files
        hours: Specific hours to fetch (0-23). Default: all daylight hours
    
    Returns:
        List of paths to downloaded GRIB files
    """
    if hours is None:
        # Daylight hours (6:00-19:00)
        hours = list(range(6, 20))
    
    # Get all items
    items = get_all_stac_items()
    
    # Filter for target date
    hours_data = filter_items_for_date(items, target_date)
    
    if not hours_data:
        logger.warning("No forecast data found for target date")
        return []
    
    # Download needed files
    downloaded = []
    for forecast_hour, var_urls in sorted(hours_data.items()):
        # Convert forecast hour to hour of day
        hour_of_day = forecast_hour - 24 if forecast_hour >= 24 else forecast_hour
        
        if hour_of_day not in hours:
            continue
        
        for var, url in var_urls.items():
            try:
                path = download_grib(url, output_dir)
                downloaded.append(path)
            except Exception as e:
                logger.error(f"Failed to download {var} hour {forecast_hour}: {e}")
    
    logger.info(f"Downloaded {len(downloaded)} GRIB files")
    return downloaded
