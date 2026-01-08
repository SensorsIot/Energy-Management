"""
PV System Configuration.
Loads hierarchical configuration: panels -> plants -> inverters -> strings

This module provides a PVSystemConfig class that is instantiated at runtime
with user configuration, rather than loading from a fixed file at import time.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class PVSystemConfig:
    """
    PV system configuration loaded from user config.

    Replaces the old module-level globals with a proper config object
    that can be instantiated with runtime configuration.
    """
    panels: Dict[str, dict] = field(default_factory=dict)
    plants: List[dict] = field(default_factory=list)
    location: dict = field(default_factory=dict)

    # Derived properties
    latitude: float = 47.475
    longitude: float = 7.767
    altitude: float = 330
    timezone: str = "Europe/Zurich"

    @classmethod
    def from_options(cls, options: dict) -> "PVSystemConfig":
        """
        Create config from user options dict.

        Args:
            options: Full options dict from load_options()

        Returns:
            PVSystemConfig instance
        """
        config = cls()

        # Load location (global location for all plants)
        location = options.get("location", {})
        config.location = location
        config.latitude = location.get("latitude", 47.475)
        config.longitude = location.get("longitude", 7.767)
        config.altitude = location.get("altitude", 330)
        config.timezone = location.get("timezone", "Europe/Zurich")

        # Build panel lookup from config
        panels_list = options.get("panels", [])
        config.panels = {}
        for panel in panels_list:
            config.panels[panel["id"]] = {
                "model": panel.get("model", panel["id"]),
                "pdc0": panel["pdc0"],
                "gamma_pdc": panel.get("gamma_pdc", -0.0035),
            }

        # Build plants with resolved panel references
        plants_list = options.get("plants", [])
        config.plants = []

        for plant_cfg in plants_list:
            # Use plant-specific location if provided, otherwise use global
            plant_location = plant_cfg.get("location", {})
            if not plant_location:
                plant_location = {
                    "latitude": config.latitude,
                    "longitude": config.longitude,
                    "altitude": config.altitude,
                    "timezone": config.timezone,
                }

            plant = {
                "name": plant_cfg["name"],
                "location": plant_location,
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
                    if panel_id not in config.panels:
                        logger.warning(f"Unknown panel id: {panel_id}, using defaults")
                        panel = {"pdc0": 400, "gamma_pdc": -0.0035, "model": "Unknown"}
                    else:
                        panel = config.panels[panel_id]

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

            config.plants.append(plant)

        logger.info(f"Loaded PV config: {len(config.plants)} plants, "
                    f"{sum(len(p['inverters']) for p in config.plants)} inverters")

        return config

    def get_all_inverters(self) -> List[dict]:
        """Get flat list of all inverters with location info."""
        inverters = []
        for plant in self.plants:
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

    def get_total_dc_power(self) -> float:
        """Get total installed DC power across all plants."""
        return sum(p["total_dc_power"] for p in self.plants)


# ============================================================================
# DEPRECATED: Legacy module-level globals for backwards compatibility
# These will be removed in a future version. Use PVSystemConfig instead.
# ============================================================================

def _load_legacy_config():
    """Load legacy config_pv.yaml if it exists (for backwards compatibility)."""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "config_pv.yaml"
    if not config_path.exists():
        logger.debug("No legacy config_pv.yaml found")
        return None

    logger.debug(f"Loading legacy config from {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _build_legacy_globals():
    """Build legacy module-level globals from config_pv.yaml."""
    config = _load_legacy_config()
    if config is None:
        # Return empty defaults - real config will come from PVSystemConfig
        return {}, [], {
            "latitude": 47.475,
            "longitude": 7.767,
            "altitude": 330,
            "timezone": "Europe/Zurich",
        }

    # Build panel lookup
    panels = {}
    for panel in config.get("panels", []):
        panels[panel["id"]] = {
            "model": panel.get("model", panel["id"]),
            "pdc0": panel["pdc0"],
            "gamma_pdc": panel.get("gamma_pdc", -0.0035),
        }

    # Build plants
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
                panel = panels.get(panel_id, {"pdc0": 400, "gamma_pdc": -0.0035})
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

    # Get default location from first plant
    if plants:
        default_loc = plants[0]["location"]
    else:
        default_loc = {
            "latitude": 47.475,
            "longitude": 7.767,
            "altitude": 330,
            "timezone": "Europe/Zurich",
        }

    return panels, plants, default_loc


# Build legacy globals (DEPRECATED - for backwards compatibility only)
_panels, _plants, _default_loc = _build_legacy_globals()

PANELS = _panels
PLANTS = _plants
LATITUDE = _default_loc["latitude"]
LONGITUDE = _default_loc["longitude"]
ALTITUDE = _default_loc["altitude"]
TIMEZONE = _default_loc["timezone"]


def get_all_inverters() -> list:
    """DEPRECATED: Get flat list of all inverters with location info."""
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
    """DEPRECATED: Get total installed DC power across all plants."""
    return sum(p["total_dc_power"] for p in PLANTS)


def get_default_location() -> dict:
    """DEPRECATED: Get default location from first plant."""
    return _default_loc
