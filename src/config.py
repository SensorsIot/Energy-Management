"""
PV System Configuration loader.
Loads hierarchical configuration: panels -> plants -> inverters -> strings
"""

import yaml
from pathlib import Path

# Load configuration from YAML
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load configuration from YAML file."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def build_panel_lookup(config: dict) -> dict:
    """Build panel lookup dictionary by id."""
    panels = {}
    for panel in config.get("panels", []):
        panels[panel["id"]] = {
            "model": panel.get("model", panel["id"]),
            "pdc0": panel["pdc0"],
            "gamma_pdc": panel.get("gamma_pdc", -0.0035),
        }
    return panels


def build_plants(config: dict, panel_lookup: dict) -> list:
    """
    Build plants list with resolved panel references.
    
    Structure:
    - plants[]
      - name, location
      - inverters[]
        - name, max_power, efficiency
        - strings[]
          - name, azimuth, tilt, panel (resolved), count, dc_power
    """
    plants = []
    
    for plant_cfg in config.get("plants", []):
        plant = {
            "name": plant_cfg["name"],
            "location": plant_cfg["location"],
            "inverters": [],
            "total_dc_power": 0,
        }
        
        for inv_cfg in plant_cfg.get("inverters", []):
            inverter = {
                "name": inv_cfg["name"],
                "max_power": inv_cfg["max_power"],
                "efficiency": inv_cfg.get("efficiency", 0.85),
                "strings": [],
                "total_dc_power": 0,
            }
            
            for string_cfg in inv_cfg.get("strings", []):
                panel_id = string_cfg["panel"]
                if panel_id not in panel_lookup:
                    raise ValueError(f"Unknown panel id: {panel_id}")
                
                panel = panel_lookup[panel_id]
                count = string_cfg["count"]
                dc_power = count * panel["pdc0"]
                
                string = {
                    "name": string_cfg["name"],
                    "azimuth": string_cfg["azimuth"],
                    "tilt": string_cfg["tilt"],
                    "panel": panel,
                    "panel_id": panel_id,
                    "count": count,
                    "dc_power": dc_power,
                }
                
                inverter["strings"].append(string)
                inverter["total_dc_power"] += dc_power
            
            plant["inverters"].append(inverter)
            plant["total_dc_power"] += inverter["total_dc_power"]
        
        plants.append(plant)
    
    return plants


# Load config on import
_config = load_config()

# Panel lookup
PANELS = build_panel_lookup(_config)

# Plants with full hierarchy
PLANTS = build_plants(_config, PANELS)

# MeteoSwiss API
STAC_API_URL = _config["meteoswiss"]["stac_api_url"]
ICON_COLLECTION = _config["meteoswiss"]["icon_collection"]


# Helper functions
def get_all_inverters() -> list:
    """Get flat list of all inverters with location info."""
    inverters = []
    for plant in PLANTS:
        loc = plant["location"]
        for inverter in plant["inverters"]:
            inverters.append({
                **inverter,
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "altitude": loc["altitude"],
                "timezone": loc["timezone"],
                "plant_name": plant["name"],
            })
    return inverters


def get_total_dc_power() -> float:
    """Get total installed DC power across all plants."""
    return sum(p["total_dc_power"] for p in PLANTS)


# Default location (from first plant) for weather fetching
def get_default_location() -> dict:
    """Get default location from first plant."""
    if PLANTS:
        return PLANTS[0]["location"]
    return {
        "latitude": 47.475,
        "longitude": 7.767,
        "altitude": 330,
        "timezone": "Europe/Zurich",
    }

_default_loc = get_default_location()
LATITUDE = _default_loc["latitude"]
LONGITUDE = _default_loc["longitude"]
ALTITUDE = _default_loc["altitude"]
TIMEZONE = _default_loc["timezone"]
