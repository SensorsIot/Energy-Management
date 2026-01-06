"""
ICON forecast data fetcher for MeteoSwiss STAC API.
Supports hybrid CH1+CH2 approach with ensemble members for uncertainty bands.

- CH1: 1km resolution, 33h horizon, 11 ensemble members, runs every 3h
- CH2: 2.1km resolution, 120h horizon, 21 ensemble members, runs every 6h

Hybrid approach: Use CH1 for hours 0-33, CH2 for hours 33-48
"""

import requests
import shutil
import logging
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# STAC API configuration
STAC_API_URL = "https://data.geo.admin.ch/api/stac/v1"

# Collection names
COLLECTIONS = {
    "ch1": "ch.meteoschweiz.ogd-forecasting-icon-ch1",
    "ch2": "ch.meteoschweiz.ogd-forecasting-icon-ch2",
}

# Variables needed for PV forecasting (minimal set)
# GHI + temperature - DNI/DHI derived via Erbs decomposition
PV_VARIABLES = ["ASOB_S", "T_2M"]

# Model configurations
MODEL_CONFIG = {
    "ch1": {
        "schedule": [0, 3, 6, 9, 12, 15, 18, 21],
        "max_horizon": 33,
        "ensemble_members": 11,  # 1 control + 10 perturbed (all in one perturbed file)
    },
    "ch2": {
        "schedule": [0, 6, 12, 18],
        "max_horizon": 120,
        "ensemble_members": 21,  # 1 control + 20 perturbed (all in one perturbed file)
    },
}

# Publication delay (approximate hours after run time)
PUBLICATION_DELAY_HOURS = 2


class IconFetcher:
    """Fetches ICON forecast data from MeteoSwiss STAC API."""

    def __init__(
        self,
        model: str,
        latitude: float,
        longitude: float,
        output_dir: Path,
        variables: list[str] = None,
        hour_start: int = 0,
        hour_end: int = None,
        include_ensemble: bool = True,
        max_workers: int = 4,
    ):
        """
        Initialize the fetcher.

        Args:
            model: "ch1" or "ch2"
            latitude: Location latitude
            longitude: Location longitude
            output_dir: Directory to store downloaded files
            variables: List of variables to download (default: minimal set)
            hour_start: First forecast hour to download
            hour_end: Last forecast hour to download (inclusive)
            include_ensemble: If True, download ensemble members (all 11/21 members)
            max_workers: Number of parallel download threads
        """
        if model not in COLLECTIONS:
            raise ValueError(f"Unknown model: {model}. Use 'ch1' or 'ch2'")

        self.model = model
        self.collection = COLLECTIONS[model]
        self.config = MODEL_CONFIG[model].copy()
        self.latitude = latitude
        self.longitude = longitude
        self.output_dir = Path(output_dir)
        self.variables = variables or PV_VARIABLES
        self.hour_start = hour_start
        self.hour_end = hour_end if hour_end is not None else self.config["max_horizon"]
        self.include_ensemble = include_ensemble
        self.max_workers = max_workers

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_local_run(self) -> Optional[str]:
        """Get the run ID of locally stored data, if any."""
        if not self.output_dir.exists():
            return None
        
        runs = [d.name for d in self.output_dir.iterdir() if d.is_dir()]
        if not runs:
            return None
        
        return max(runs)

    def get_expected_run_times(self, lookback_hours: int = 48) -> list[datetime]:
        """Calculate expected model run times within the lookback period."""
        now = datetime.now(timezone.utc)
        runs = []
        
        for hours_ago in range(0, lookback_hours):
            check_time = now - timedelta(hours=hours_ago)
            if check_time.hour in self.config["schedule"]:
                run_time = check_time.replace(minute=0, second=0, microsecond=0)
                pub_time = run_time + timedelta(hours=PUBLICATION_DELAY_HOURS)
                if pub_time <= now:
                    runs.append(run_time)
        
        return runs

    def check_run_available(self, run_datetime: datetime) -> bool:
        """Check if a specific run has data available."""
        url = f"{STAC_API_URL}/search"
        run_str = run_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        params = {
            "collections": [self.collection],
            "forecast:reference_datetime": run_str,
            "forecast:perturbed": False,
            "limit": 1,
        }
        
        try:
            r = requests.post(url, json=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            return len(data.get("features", [])) > 0
        except:
            return False

    def find_latest_available_run(self) -> Optional[datetime]:
        """Find the most recent run that has data available."""
        expected_runs = self.get_expected_run_times()
        
        logger.info(f"Checking {len(expected_runs)} expected {self.model.upper()} runs...")
        
        for run_time in expected_runs:
            if self.check_run_available(run_time):
                run_str = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.info(f"Found available run: {run_str}")
                return run_time
        
        return None

    def fetch_item(self, run_str: str, var: str, hour: int, perturbed: bool) -> Optional[dict]:
        """Fetch a single STAC item."""
        url = f"{STAC_API_URL}/search"
        
        days = hour // 24
        hours = hour % 24
        horizon_str = f"P{days}DT{hours:02d}H00M00S"
        
        params = {
            "collections": [self.collection],
            "forecast:reference_datetime": run_str,
            "forecast:variable": var,
            "forecast:perturbed": perturbed,
            "forecast:horizon": horizon_str,
            "limit": 1,
        }
        
        try:
            r = requests.post(url, json=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            items = data.get("features", [])
            if items:
                return items[0]
        except Exception as e:
            logger.debug(f"Error fetching {var} h{hour} perturbed={perturbed}: {e}")
        
        return None

    def extract_asset_url(self, item: dict) -> Optional[str]:
        """Extract download URL from STAC item."""
        assets = item.get("assets", {})
        for asset in assets.values():
            href = asset.get("href", "")
            if href:
                return href
        return None

    def download_file(self, url: str, output_path: Path) -> bool:
        """Download a single file."""
        try:
            response = requests.get(url, timeout=300, stream=True)
            response.raise_for_status()

            temp_path = output_path.with_suffix('.tmp')
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            temp_path.rename(output_path)
            return True
        except Exception as e:
            logger.debug(f"Download failed: {e}")
            return False

    def download_item(self, run_iso: str, run_dir: Path, var: str, hour: int, member: int) -> Optional[str]:
        """
        Download a single forecast item (one variable, one hour, one member type).

        MeteoSwiss provides two file types:
        - Control (perturbed=False): Single member (perturbationNumber=0)
        - Perturbed (perturbed=True): All 10 perturbed members in one file

        Args:
            run_iso: Model run time in ISO format (YYYY-MM-DDTHH:MM:SSZ)
            run_dir: Directory to save the file
            var: Variable name (e.g., ASOB_S)
            hour: Forecast hour
            member: Ensemble member (0=control, 1+=perturbed - but perturbed file contains ALL members)
        """
        perturbed = member > 0
        member_str = "perturbed" if perturbed else "m00"

        run_compact = run_iso[:16].replace('-', '').replace('T', '').replace(':', '')
        filename = f"icon-{self.model}-{run_compact}-h{hour:03d}-{var.lower()}-{member_str}.grib2"
        output_path = run_dir / filename

        if output_path.exists():
            return str(output_path)

        item = self.fetch_item(run_iso, var, hour, perturbed)
        if not item:
            return None

        url = self.extract_asset_url(item)
        if not url:
            return None

        if self.download_file(url, output_path):
            return str(output_path)

        return None

    def fetch_latest(self, force: bool = False) -> dict:
        """Fetch the latest forecast data with all ensemble members."""
        latest_run = self.find_latest_available_run()
        if not latest_run:
            raise RuntimeError(f"No available {self.model.upper()} runs found")

        run_iso = latest_run.strftime("%Y-%m-%dT%H:%M:%SZ")
        run_str = latest_run.strftime("%Y%m%d%H%M")
        
        # Check if we already have this run
        local_run = self.get_local_run()
        metadata_file = self.output_dir / run_str / "metadata.json"
        
        if local_run and local_run >= run_str and not force and metadata_file.exists():
            logger.info(f"Already have latest {self.model.upper()} run: {local_run}")
            with open(metadata_file) as f:
                return json.load(f)

        # Cleanup old runs BEFORE downloading to free disk space
        self._cleanup_old_runs(run_str)

        run_dir = self.output_dir / run_str
        run_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Fetching {self.model.upper()} run {run_iso}, hours {self.hour_start}-{self.hour_end}")

        # Build download task list: control (0) + perturbed trigger (1)
        download_tasks = []
        members = [0, 1] if self.include_ensemble else [0]

        for var in self.variables:
            for hour in range(self.hour_start, self.hour_end + 1):
                for member in members:
                    download_tasks.append((var, hour, member))

        logger.info(f"Downloading {len(download_tasks)} items...")

        # Download in parallel
        downloaded = []
        failed = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.download_item, run_iso, run_dir, var, hour, member): (var, hour, member)
                for var, hour, member in download_tasks
            }
            
            for future in as_completed(futures):
                var, hour, member = futures[future]
                try:
                    result = future.result()
                    if result:
                        downloaded.append(result)
                    else:
                        failed.append(f"{var} h{hour} m{member}")
                except Exception as e:
                    failed.append(f"{var} h{hour} m{member}: {e}")

        if failed:
            logger.warning(f"Failed to download {len(failed)} items")

        # Save metadata
        # Note: perturbed file contains all 10 perturbed members, so total is always 11 for CH1, 21 for CH2
        actual_members = MODEL_CONFIG[self.model]["ensemble_members"] if self.include_ensemble else 1
        metadata = {
            "model": self.model,
            "collection": self.collection,
            "run_datetime": run_iso,
            "run_str": run_str,
            "hour_start": self.hour_start,
            "hour_end": self.hour_end,
            "variables": self.variables,
            "ensemble_members": actual_members,
            "include_ensemble": self.include_ensemble,
            "files_downloaded": len(downloaded),
            "files_failed": len(failed),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Downloaded {len(downloaded)} files to {run_dir}")

        # Cleanup old runs
        self._cleanup_old_runs(run_str)

        return metadata

    def _cleanup_old_runs(self, keep_run: str):
        """Remove old run directories and incomplete downloads."""
        if not self.output_dir.exists():
            return

        for item in self.output_dir.iterdir():
            if item.is_dir() and item.name != keep_run:
                logger.info(f"Removing old run: {item.name}")
                try:
                    shutil.rmtree(item)
                except Exception as e:
                    logger.warning(f"Failed to remove {item}: {e}")

        # Also clean up any .tmp files in the kept run
        keep_dir = self.output_dir / keep_run
        if keep_dir.exists():
            tmp_files = list(keep_dir.glob("*.tmp"))
            if tmp_files:
                logger.info(f"Removing {len(tmp_files)} incomplete .tmp files")
                for tmp in tmp_files:
                    try:
                        tmp.unlink()
                    except Exception as e:
                        logger.debug(f"Failed to remove {tmp}: {e}")

def fetch_hybrid_forecast(
    latitude: float,
    longitude: float,
    output_dir: Path,
    target_hours: int = 48,
) -> dict:
    """
    Fetch hybrid CH1+CH2 forecast with ensemble data.
    
    - CH1: hours 0-33 (or until target_hours if less)
    - CH2: hours 33-target_hours (to fill the gap)
    
    Args:
        latitude: Location latitude
        longitude: Location longitude
        output_dir: Base output directory
        target_hours: Total forecast hours needed (default 48)
    
    Returns:
        Dict with metadata for both models
    """
    results = {}
    
    # CH1: hours 0-33 (or less if target is shorter)
    ch1_end = min(33, target_hours)
    logger.info(f"Fetching CH1 ensemble: hours 0-{ch1_end}")
    
    ch1_fetcher = IconFetcher(
        model="ch1",
        latitude=latitude,
        longitude=longitude,
        output_dir=output_dir / "icon-ch1",
        hour_start=0,
        hour_end=ch1_end,
        include_ensemble=True,
    )
    results["ch1"] = ch1_fetcher.fetch_latest()
    
    # CH2: hours 33-target_hours (only if needed)
    if target_hours > 33:
        ch2_start = 33
        ch2_end = target_hours
        logger.info(f"Fetching CH2 ensemble: hours {ch2_start}-{ch2_end}")
        
        ch2_fetcher = IconFetcher(
            model="ch2",
            latitude=latitude,
            longitude=longitude,
            output_dir=output_dir / "icon-ch2",
            hour_start=ch2_start,
            hour_end=ch2_end,
            include_ensemble=True,
        )
        results["ch2"] = ch2_fetcher.fetch_latest()

    return results


def fetch_icon_data(
    model: str,
    latitude: float,
    longitude: float,
    output_dir: Path,
    hour_start: int = 0,
    hour_end: int = None,
    include_ensemble: bool = True,
) -> dict:
    """
    Convenience function to fetch ICON forecast data.

    Args:
        model: "ch1" or "ch2"
        latitude: Location latitude
        longitude: Location longitude
        output_dir: Base output directory (will create icon-{model} subdir)
        hour_start: First forecast hour to fetch
        hour_end: Last forecast hour (None = model max: 33 for CH1, 48 for CH2)
        include_ensemble: Fetch ensemble members (all 11/21 members)

    Returns:
        Dict with metadata about the fetch
    """
    # Default hours: CH1 0-33, CH2 33-48 (no overlap)
    if hour_end is None:
        hour_end = 33 if model == "ch1" else 48
    if hour_start == 0 and model == "ch2":
        hour_start = 33

    model_dir = output_dir / f"icon-{model}"

    fetcher = IconFetcher(
        model=model,
        latitude=latitude,
        longitude=longitude,
        output_dir=model_dir,
        hour_start=hour_start,
        hour_end=hour_end,
        include_ensemble=include_ensemble,
    )

    return fetcher.fetch_latest()
