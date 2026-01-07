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
        self._provided_token = token
        self._token = None

    @property
    def token(self) -> Optional[str]:
        """Get token - check environment each time (no caching)."""
        # Use provided token first
        if self._provided_token:
            return self._provided_token

        # Try environment variables
        token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
        if token:
            return token

        # Try token file (used by some HA add-on versions)
        try:
            with open("/run/secrets/supervisor_token", "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except FileNotFoundError:
            pass

        return None

    def _headers(self) -> dict:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _api_url(self, path: str) -> str:
        """Build API URL - handle supervisor vs direct access."""
        # For supervisor access, URL is http://supervisor/core
        # API path should be /api/...
        if "supervisor" in self.url:
            return f"{self.url}/api{path}"
        else:
            return f"{self.url}/api{path}"

    def get_state(self, entity_id: str) -> Optional[dict]:
        """
        Get entity state.

        Returns:
            dict with 'state' and 'attributes', or None on error
        """
        if not self.token:
            logger.warning("No token available for get_state")
            return None

        try:
            url = self._api_url(f"/states/{entity_id}")
            logger.debug(f"GET {url}")
            response = requests.get(url, headers=self._headers(), timeout=30)
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
            logger.warning("No token available for set_number")
            return False

        try:
            url = self._api_url("/services/number/set_value")
            data = {
                "entity_id": entity_id,
                "value": value,
            }
            logger.debug(f"POST {url} with {data}")
            response = requests.post(
                url, headers=self._headers(), json=data, timeout=30
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

    def set_sensor_state(
        self,
        entity_id: str,
        state: Any,
        attributes: Optional[dict] = None,
    ) -> bool:
        """
        Set a sensor entity state directly via REST API.

        Args:
            entity_id: The sensor entity ID
            state: The state value
            attributes: Optional attributes dict

        Returns:
            True on success, False on error
        """
        if not self.token:
            logger.warning("No token available for set_sensor_state")
            return False

        try:
            url = self._api_url(f"/states/{entity_id}")
            data = {
                "state": str(state),
                "attributes": attributes or {},
            }
            logger.debug(f"POST {url} with state={state}")
            response = requests.post(
                url, headers=self._headers(), json=data, timeout=30
            )
            response.raise_for_status()
            logger.debug(f"Set {entity_id} to {state}")
            return True
        except Exception as e:
            logger.error(f"Failed to set {entity_id}: {e}")
            return False
