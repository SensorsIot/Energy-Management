"""
PV power forecast model using pvlib.
Supports hierarchical config: plants -> inverters -> strings -> panels
"""

import pvlib
import pandas as pd
import numpy as np
import logging

from .config import PLANTS, get_all_inverters

logger = logging.getLogger(__name__)


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
) -> pd.DataFrame:
    """
    Calculate power forecast for all plants.
    """
    results = {}
    first_result = None
    
    for plant in PLANTS:
        logger.info(f"Calculating forecast for plant: {plant['name']}")
        plant_result = forecast_plant_power(weather, plant)
        
        # Prefix columns with plant name if multiple plants
        if len(PLANTS) > 1:
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
    if "ghi" in weather.columns:
        # Align weather index with output index
        weather_aligned = weather.copy()
        if weather_aligned.index.tz is None:
            weather_aligned.index = weather_aligned.index.tz_localize("UTC")
        weather_aligned.index = weather_aligned.index.tz_convert(first_result.index.tz)
        output["ghi"] = weather_aligned["ghi"].values
    
    if "temp_air" in weather.columns:
        weather_aligned = weather.copy()
        if weather_aligned.index.tz is None:
            weather_aligned.index = weather_aligned.index.tz_localize("UTC")
        weather_aligned.index = weather_aligned.index.tz_convert(first_result.index.tz)
        output["temp_air"] = weather_aligned["temp_air"].values
    
    # Grand total if multiple plants
    if len(PLANTS) > 1:
        total_cols = [c for c in output.columns if c.endswith("_total_ac_power")]
        output["grand_total_ac_power"] = output[total_cols].sum(axis=1)
    
    return output
