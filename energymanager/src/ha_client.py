"""
Home Assistant REST API client.
"""

import logging
import os
from typing import Optional, Any

import requests

logger = logging.getLogger(__name__)


class HAClient:
    """Home Assistant API client."""

    def __init__(
        self,
        url: str = "http://supervisor/core",
        token: Optional[str] = None,
    ):
        self.url = url.rstrip("/")
        # Use supervisor token if available, otherwise use provided token
        self.token = token or os.environ.get("SUPERVISOR_TOKEN")

        if not self.token:
            logger.warning("No HA token available - HA integration disabled")

    def _headers(self) -> dict:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def get_state(self, entity_id: str) -> Optional[dict]:
        """
        Get entity state.

        Returns:
            dict with 'state' and 'attributes', or None on error
        """
        if not self.token:
            return None

        try:
            url = f"{self.url}/api/states/{entity_id}"
            response = requests.get(url, headers=self._headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get state for {entity_id}: {e}")
            return None

    def get_sensor_value(self, entity_id: str) -> Optional[float]:
        """
        Get numeric sensor value.

        Returns:
            float value or None on error
        """
        state = self.get_state(entity_id)
        if not state:
            return None

        try:
            value = float(state["state"])
            return value
        except (ValueError, KeyError) as e:
            logger.error(f"Failed to parse state for {entity_id}: {e}")
            return None

    def set_number(self, entity_id: str, value: float) -> bool:
        """
        Set a number entity value.

        Returns:
            True on success, False on error
        """
        if not self.token:
            return False

        try:
            url = f"{self.url}/api/services/number/set_value"
            data = {
                "entity_id": entity_id,
                "value": value,
            }
            response = requests.post(
                url, headers=self._headers(), json=data, timeout=10
            )
            response.raise_for_status()
            logger.info(f"Set {entity_id} to {value}")
            return True
        except Exception as e:
            logger.error(f"Failed to set {entity_id}: {e}")
            return False

    def get_battery_soc(self, entity_id: str = "sensor.battery_state_of_capacity") -> Optional[float]:
        """
        Get current battery SOC.

        Returns:
            SOC as percentage (0-100) or None on error
        """
        soc = self.get_sensor_value(entity_id)
        if soc is not None:
            logger.debug(f"Battery SOC: {soc}%")
        return soc

    def set_battery_discharge_power(
        self,
        entity_id: str,
        power_w: float,
    ) -> bool:
        """
        Set maximum battery discharge power.

        Args:
            entity_id: The number entity to control
            power_w: Maximum discharge power in watts (0 = block discharge)
        """
        return self.set_number(entity_id, power_w)
