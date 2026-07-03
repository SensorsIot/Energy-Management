"""MeteoSwiss local point forecast fetcher.

Downloads pre-extracted point forecasts from the ogd-local-forecasting collection.
Data is available per ZIP code, updated hourly, with 192h horizon.
This is the same forecast data used in the MeteoSwiss app.

No GRIB parsing, no grid interpolation — just CSV download and filter.
"""

from __future__ import annotations

import logging
import re
from io import StringIO

import pandas as pd
import requests

from .icon_fetcher import STAC_API_URL

logger = logging.getLogger(__name__)

COLLECTION = "ch.meteoschweiz.ogd-local-forecasting"

# MeteoSwiss parameter codes -> standard weather variable names
DEFAULT_PARAMETERS = {
    "ghi": "gre000h0",     # Global radiation, hourly mean (W/m²)
    "dhi": "ods000h0",     # Diffuse radiation, hourly mean (W/m²)
    "temp_air": "tre200h0",  # Air temperature 2m, hourly mean (°C)
    "wind_speed": "fu3010h0",  # Wind speed, hourly mean (km/h)
}


class LocalFetcher:
    """Fetches point forecast data from MeteoSwiss local forecasting API."""

    def __init__(
        self,
        point_id: int = 441500,
        parameters: dict[str, str] | None = None,
    ) -> None:
        """Initialize the fetcher.

        Args:
            point_id: MeteoSwiss point ID (441500 = Lausen/4415)
            parameters: Mapping of standard names to MeteoSwiss parameter codes.

        """
        self.point_id = point_id
        self.parameters = parameters or DEFAULT_PARAMETERS

    def _find_latest_item(self) -> dict | None:
        """Find the STAC item with the most recent forecast run.

        MeteoSwiss groups items by day and doesn't support sortby,
        so we fetch several recent items and pick the one whose assets
        contain the highest run timestamp.
        """
        url = f"{STAC_API_URL}/collections/{COLLECTION}/items?limit=5"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            features = data.get("features", [])
            if not features:
                return None

            # Find the item with the newest run timestamp in its assets
            best_item = None
            best_run = ""
            first_param = next(iter(self.parameters.values()))

            for item in features:
                for key in item.get("assets", {}):
                    if first_param in key:
                        match = re.search(r"(\d{12})", key)
                        if match and match.group(1) > best_run:
                            best_run = match.group(1)
                            best_item = item

            if best_item:
                logger.debug(f"Selected item with latest run {best_run}")
            return best_item

        except Exception as e:
            logger.error(f"Failed to query local forecasting collection: {e}")
        return None

    def _find_latest_run_assets(self, item: dict) -> dict[str, str]:
        """Find download URLs for the latest run within a STAC item.

        Each item contains assets for multiple runs (hourly updates).
        Asset keys follow: vnut12.lssw.YYYYMMDDHHMM.{param}.csv
        We find the highest timestamp and return URLs for all parameters.
        """
        assets = item.get("assets", {})

        # Find the latest run timestamp by scanning asset keys for our first parameter
        first_param = next(iter(self.parameters.values()))
        run_timestamps = []

        for key in assets:
            if first_param in key:
                match = re.search(r"(\d{12})", key)
                if match:
                    run_timestamps.append(match.group(1))

        if not run_timestamps:
            logger.warning(f"No assets found for parameter {first_param}")
            return {}

        latest_run = max(run_timestamps)
        logger.info(f"Latest local forecast run: {latest_run}")

        # Build URLs for all parameters at this run timestamp
        urls = {}
        for var_name, param_code in self.parameters.items():
            asset_key = f"vnut12.lssw.{latest_run}.{param_code}.csv"
            if asset_key in assets:
                urls[var_name] = assets[asset_key]["href"]
            else:
                logger.warning(f"Asset not found: {asset_key}")

        return urls

    def _download_and_filter(self, url: str, var_name: str) -> pd.Series | None:
        """Download CSV and extract data for our point_id."""
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()

            df = pd.read_csv(StringIO(r.text), sep=";")
            filtered = df[df["point_id"] == self.point_id].copy()

            if filtered.empty:
                logger.warning(f"No data for point_id {self.point_id} in {var_name}")
                return None

            # Parse Date column (YYYYMMDDHHMM) to UTC timestamps
            filtered["time"] = pd.to_datetime(
                filtered["Date"].astype(str), format="%Y%m%d%H%M", utc=True
            )
            filtered = filtered.set_index("time").sort_index()

            # The value column is the last column (parameter name varies)
            value_col = filtered.columns[-1]
            series = filtered[value_col].astype(float)
            series.name = var_name

            return series

        except Exception as e:
            logger.error(f"Failed to download {var_name}: {e}")
            return None

    def fetch_latest(self, max_hours: int = 120) -> pd.DataFrame:
        """Fetch the latest local point forecast.

        Args:
            max_hours: Trim forecast to this many hours from now (default 120,
                       matching CH2 max horizon for apples-to-apples comparison)

        Returns:
            DataFrame with columns: ghi, dhi, dni, temp_air, wind_speed
            Index: DatetimeIndex (UTC)
            Empty DataFrame if fetch fails.

        """
        item = self._find_latest_item()
        if not item:
            logger.error("No local forecasting item found")
            return pd.DataFrame()

        urls = self._find_latest_run_assets(item)
        if not urls:
            logger.error("No asset URLs found for latest run")
            return pd.DataFrame()

        logger.info(f"Downloading {len(urls)} local forecast parameters...")

        # Download all parameters
        series_dict = {}
        for var_name, url in urls.items():
            series = self._download_and_filter(url, var_name)
            if series is not None:
                series_dict[var_name] = series

        if "ghi" not in series_dict:
            logger.error("GHI data missing — cannot produce PV forecast")
            return pd.DataFrame()

        # Build weather DataFrame
        weather = pd.DataFrame(series_dict)

        # Hourly-mean parameters (gre000h0 etc.) represent the hour ENDING at the
        # stamp; shift to the interval midpoint so interpolation doesn't lag the
        # diurnal ramp by half an hour.
        weather.index = weather.index - pd.Timedelta(minutes=30)

        # Derive DNI from GHI - DHI (if DHI available)
        if "dhi" in weather.columns:
            weather["dni"] = (weather["ghi"] - weather["dhi"]).clip(lower=0)

        # Convert wind speed from km/h to m/s
        if "wind_speed" in weather.columns:
            weather["wind_speed"] = weather["wind_speed"] / 3.6

        # Fill defaults for missing optional columns
        if "temp_air" not in weather.columns:
            weather["temp_air"] = 10.0
        if "wind_speed" not in weather.columns:
            weather["wind_speed"] = 2.0

        # Clip radiation to non-negative
        for col in ["ghi", "dhi", "dni"]:
            if col in weather.columns:
                weather[col] = weather[col].clip(lower=0)

        # Trim to max_hours from now (match GRIB horizon for comparison)
        if max_hours:
            cutoff = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=max_hours)
            before = len(weather)
            weather = weather[weather.index <= cutoff]
            if len(weather) < before:
                logger.info(
                    f"Trimmed forecast from {before} to {len(weather)} hours "
                    f"(max {max_hours}h)"
                )

        logger.info(
            f"Local forecast: {len(weather)} hours, "
            f"{weather.index.min()} to {weather.index.max()}"
        )

        return weather
