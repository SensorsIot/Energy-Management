"""
PV power forecast model using pvlib.
Supports hierarchical config: plants -> inverters -> strings -> panels

All forecast functions accept plants as a parameter, allowing runtime
configuration from user config files rather than import-time globals.
"""

import pvlib
import pandas as pd
import numpy as np
import logging
from typing import List, Optional

from .config import PVSystemConfig

# Legacy import for backwards compatibility (DEPRECATED)
from .config import PLANTS as _LEGACY_PLANTS

logger = logging.getLogger(__name__)


def align_weather_index(weather: pd.DataFrame, target_tz) -> pd.DataFrame:
    """Align weather DataFrame index to target timezone."""
    aligned = weather.copy()
    if aligned.index.tz is None:
        aligned.index = aligned.index.tz_localize("UTC")
    aligned.index = aligned.index.tz_convert(target_tz)
    return aligned


def forecast_string_dc_power(
    weather: pd.DataFrame,
    string: dict,
    latitude: float,
    longitude: float,
    altitude: float,
    timezone: str,
) -> pd.Series:
    """Calculate DC power forecast for a single string."""
    
    tilt = string["tilt"]
    azimuth = string["azimuth"]
    panel = string["panel"]
    dc_power_stc = string["dc_power"]  # count * pdc0
    gamma_pdc = panel["gamma_pdc"]
    
    # Ensure timezone-aware index
    times = weather.index.copy()
    if times.tz is None:
        times = times.tz_localize("UTC")
    times = times.tz_convert(timezone)
    
    # Create weather DataFrame with proper timezone
    weather_tz = weather.copy()
    weather_tz.index = times
    
    # Solar position
    solar_pos = pvlib.solarposition.get_solarposition(
        times, latitude, longitude, altitude=altitude
    )
    
    # Get irradiance as numpy arrays
    ghi = np.asarray(weather_tz["ghi"])
    
    if "dni" in weather_tz.columns and "dhi" in weather_tz.columns:
        dni = np.asarray(weather_tz["dni"])
        dhi = np.asarray(weather_tz["dhi"])
    else:
        # Decompose GHI
        erbs = pvlib.irradiance.erbs(
            pd.Series(ghi, index=times),
            solar_pos["zenith"],
            times
        )
        dni = np.asarray(erbs["dni"])
        dhi = np.asarray(erbs["dhi"])
    
    # Extraterrestrial radiation
    dni_extra = np.asarray(pvlib.irradiance.get_extra_radiation(times))
    
    # POA irradiance
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=np.asarray(solar_pos["zenith"]),
        solar_azimuth=np.asarray(solar_pos["azimuth"]),
        dni=dni,
        ghi=ghi,
        dhi=dhi,
        dni_extra=dni_extra,
        model="isotropic",
    )
    
    # Handle both DataFrame and dict returns
    if isinstance(poa, pd.DataFrame):
        poa_global = np.asarray(poa["poa_global"])
    else:
        poa_global = np.asarray(poa["poa_global"])
    
    poa_global = np.clip(np.nan_to_num(poa_global), 0, None)
    
    # Cell temperature
    temp_air = np.asarray(weather_tz.get("temp_air", pd.Series(10, index=times)))
    wind_speed = np.asarray(weather_tz.get("wind_speed", pd.Series(2, index=times)))
    
    cell_temp = pvlib.temperature.faiman(
        poa_global, temp_air, wind_speed, u0=25.0, u1=6.84
    )
    
    # DC power using PVWatts model
    dc_power = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=poa_global,
        temp_cell=cell_temp,
        pdc0=dc_power_stc,
        gamma_pdc=gamma_pdc,
        temp_ref=25,
    )
    
    return pd.Series(np.asarray(dc_power), index=times, name=string["name"])


def forecast_inverter_power(
    weather: pd.DataFrame,
    inverter: dict,
) -> pd.DataFrame:
    """
    Calculate power forecast for an inverter and all its strings.
    
    Returns DataFrame with:
    - DC power per string
    - Total DC power
    - AC power (after efficiency and clipping)
    """
    lat = inverter["latitude"]
    lon = inverter["longitude"]
    alt = inverter["altitude"]
    tz = inverter["timezone"]
    max_power = inverter["max_power"]
    efficiency = inverter["efficiency"]
    
    results = {}
    
    # Calculate DC power for each string
    for string in inverter["strings"]:
        dc_power = forecast_string_dc_power(weather, string, lat, lon, alt, tz)
        results[f"{string['name']}_dc"] = dc_power
    
    # Get index from first result
    index = list(results.values())[0].index
    
    # Build DataFrame
    df = pd.DataFrame(results, index=index)
    
    # Total DC power for this inverter
    dc_cols = [c for c in df.columns if c.endswith("_dc")]
    df["total_dc"] = df[dc_cols].sum(axis=1)
    
    # AC power: apply efficiency and clip to max_power
    df["ac_power"] = np.clip(df["total_dc"] * efficiency, 0, max_power)
    
    return df


def forecast_plant_power(
    weather: pd.DataFrame,
    plant: dict,
) -> pd.DataFrame:
    """
    Calculate power forecast for a plant and all its inverters.
    """
    loc = plant["location"]
    results = {}
    first_index = None
    
    for inverter in plant["inverters"]:
        # Add location to inverter for processing
        inv_with_loc = {
            **inverter,
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "altitude": loc["altitude"],
            "timezone": loc["timezone"],
        }
        
        logger.info(f"Calculating forecast for inverter: {inverter['name']}")
        inv_result = forecast_inverter_power(weather, inv_with_loc)
        
        # Store inverter AC power
        results[f"{inverter['name']}_ac_power"] = inv_result["ac_power"]
        
        if first_index is None:
            first_index = inv_result.index
    
    # Build output DataFrame
    output = pd.DataFrame(results, index=first_index)
    
    # Total AC power for this plant
    ac_cols = [c for c in output.columns if c.endswith("_ac_power")]
    output["total_ac_power"] = output[ac_cols].sum(axis=1)
    
    return output


def forecast_all_plants(
    weather: pd.DataFrame,
    plants: Optional[List[dict]] = None,
) -> pd.DataFrame:
    """
    Calculate power forecast for all plants.

    Args:
        weather: Weather DataFrame with ghi, temp_air, wind_speed
        plants: List of plant dicts from PVSystemConfig.plants.
                If None, uses legacy config_pv.yaml (DEPRECATED).

    Returns:
        DataFrame with power forecast for all plants
    """
    # Use provided plants or fall back to legacy for backwards compatibility
    if plants is None:
        logger.debug("No plants provided, using legacy config (DEPRECATED)")
        plants = _LEGACY_PLANTS

    results = {}
    first_result = None

    for plant in plants:
        logger.info(f"Calculating forecast for plant: {plant['name']}")
        plant_result = forecast_plant_power(weather, plant)

        # Prefix columns with plant name if multiple plants
        if len(plants) > 1:
            for col in plant_result.columns:
                results[f"{plant['name']}_{col}"] = plant_result[col]
        else:
            for col in plant_result.columns:
                results[col] = plant_result[col]

        if first_result is None:
            first_result = plant_result

    # Build output DataFrame
    output = pd.DataFrame(results, index=first_result.index)

    # Add GHI and temp from weather
    weather_aligned = align_weather_index(weather, first_result.index.tz)
    if "ghi" in weather.columns:
        output["ghi"] = weather_aligned["ghi"].values
    if "temp_air" in weather.columns:
        output["temp_air"] = weather_aligned["temp_air"].values

    # Grand total if multiple plants
    if len(plants) > 1:
        total_cols = [c for c in output.columns if c.endswith("_total_ac_power")]
        output["grand_total_ac_power"] = output[total_cols].sum(axis=1)

    return output


def forecast_ensemble_plants(
    ensemble_weather: dict[int, pd.DataFrame],
    plants: Optional[List[dict]] = None,
) -> pd.DataFrame:
    """
    Calculate power forecast with uncertainty bands using ensemble weather data.

    Args:
        ensemble_weather: Dict mapping member number to weather DataFrame
        plants: List of plant dicts from PVSystemConfig.plants.
                If None, uses legacy config_pv.yaml (DEPRECATED).

    Returns:
        DataFrame with P10, P50, P90 columns for total AC power,
        plus per-inverter percentiles.
    """
    # Use provided plants or fall back to legacy for backwards compatibility
    if plants is None:
        logger.debug("No plants provided, using legacy config (DEPRECATED)")
        plants = _LEGACY_PLANTS

    if not ensemble_weather:
        raise ValueError("No ensemble weather data provided")

    # Calculate forecast for each ensemble member
    member_forecasts = {}
    for member, weather in ensemble_weather.items():
        try:
            forecast = forecast_all_plants(weather, plants=plants)
            member_forecasts[member] = forecast
        except Exception as e:
            logger.warning(f"Failed to calculate forecast for member {member}: {e}")

    if not member_forecasts:
        raise RuntimeError("Could not calculate forecast for any ensemble member")

    logger.info(f"Calculated forecasts for {len(member_forecasts)} ensemble members")

    # Get common index (use first member's index)
    first_member = list(member_forecasts.values())[0]
    index = first_member.index

    # Stack all members' total AC power for percentile calculation
    all_totals = []
    for member, forecast in member_forecasts.items():
        # Align indices
        if "total_ac_power" in forecast.columns:
            aligned = forecast["total_ac_power"].reindex(index)
            all_totals.append(aligned.values)
        elif "grand_total_ac_power" in forecast.columns:
            aligned = forecast["grand_total_ac_power"].reindex(index)
            all_totals.append(aligned.values)

    if not all_totals:
        raise RuntimeError("No total_ac_power found in forecasts")

    # Stack into 2D array: (n_members, n_times)
    totals_array = np.array(all_totals)

    # Calculate percentiles along member axis (axis=0)
    p10 = np.percentile(totals_array, 10, axis=0)
    p50 = np.percentile(totals_array, 50, axis=0)
    p90 = np.percentile(totals_array, 90, axis=0)

    # Build output DataFrame
    output = pd.DataFrame(index=index)
    output["total_ac_power_p10"] = p10
    output["total_ac_power_p50"] = p50
    output["total_ac_power_p90"] = p90

    # Also add mean for comparison
    output["total_ac_power_mean"] = np.mean(totals_array, axis=0)

    # Calculate percentiles for each inverter
    inverter_names = []
    for plant in plants:
        for inverter in plant["inverters"]:
            inverter_names.append(inverter["name"])

    for inv_name in inverter_names:
        col = f"{inv_name}_ac_power"
        inv_arrays = []
        for member, forecast in member_forecasts.items():
            if col in forecast.columns:
                aligned = forecast[col].reindex(index)
                inv_arrays.append(aligned.values)

        if inv_arrays:
            inv_array = np.array(inv_arrays)
            output[f"{inv_name}_ac_power_p10"] = np.percentile(inv_array, 10, axis=0)
            output[f"{inv_name}_ac_power_p50"] = np.percentile(inv_array, 50, axis=0)
            output[f"{inv_name}_ac_power_p90"] = np.percentile(inv_array, 90, axis=0)

    # Add weather from control member (member 0) as reference
    ref_weather = align_weather_index(ensemble_weather[0], index.tz)
    if "ghi" in ref_weather.columns:
        output["ghi"] = ref_weather["ghi"].reindex(index).values
    if "temp_air" in ref_weather.columns:
        output["temp_air"] = ref_weather["temp_air"].reindex(index).values

    # Add ensemble spread info
    output["ensemble_spread"] = p90 - p10
    output["ensemble_cv"] = np.std(totals_array, axis=0) / (np.mean(totals_array, axis=0) + 1e-6)

    logger.info(f"Generated ensemble forecast with P10/P50/P90 for {len(output)} time steps")

    return output
