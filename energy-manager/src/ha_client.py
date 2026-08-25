"""Home Assistant REST API client."""

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


class HAClient:
    """Home Assistant API client."""

    # Largest difference still counted as "the register already holds this value".
    # The number entities carry whole W / 0.1 % steps, so anything below this is
    # float noise, not a real change.
    WRITE_TOLERANCE = 0.01

    def __init__(
        self,
        url: str = "http://supervisor/core",
        token: str | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._provided_token = token
        self._token = None

    @property
    def token(self) -> str | None:
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
            with open("/run/secrets/supervisor_token") as f:
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

    def get_state(self, entity_id: str) -> dict | None:
        """Get entity state.

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
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.debug(f"Entity {entity_id} not found (404)")
            else:
                logger.error(f"Failed to get state for {entity_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get state for {entity_id}: {e}")
            return None

    def get_sensor_value(self, entity_id: str) -> float | None:
        """Get numeric sensor value.

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

    def _already_at(self, entity_id: str, value: float) -> bool:
        """Whether the entity already holds `value` (within WRITE_TOLERANCE).

        False when the value cannot be read, so an unreadable entity is written
        rather than silently skipped. Parses the state here instead of through
        `get_number_value` so an `unavailable` register — routine while the
        Huawei integration reconnects — logs at debug, not error.
        """
        state = self.get_state(entity_id)
        if not state:
            return False
        try:
            current = float(state["state"])
        except (ValueError, KeyError, TypeError):
            logger.debug(f"{entity_id} not readable as a number — writing anyway")
            return False
        return abs(current - value) <= self.WRITE_TOLERANCE

    def set_number(
        self,
        entity_id: str,
        value: float,
        max_retries: int = 5,
        retry_delay: float = 2.0,
    ) -> tuple[bool, str]:
        """Set a number entity value, skipping no-op writes, with retry logic.

        Every one of these entities is a Huawei inverter holding register, and
        each write costs a flash-erase cycle. So an unchanged value is never
        sent: the live entity state is read first and the call short-circuits
        when it already matches (FSD 4.7.1). Reading is free — it is served
        from the HA state machine, not a Modbus round trip.

        The same read guards the retry loop: a lost response after HA already
        accepted the call would otherwise turn one logical change into up to
        `max_retries` register writes, so every retry re-reads first and treats
        an already-correct value as success.

        A value that cannot be read (entity missing, `unknown`, `unavailable`)
        fails open — the write proceeds, because refusing to write on a read
        failure would strand the register at whatever it holds.

        Args:
            entity_id: The entity to set
            value: The value to set
            max_retries: Maximum number of attempts (default: 5)
            retry_delay: Delay between retries in seconds (default: 2.0)

        Returns:
            Tuple of (success: bool, error_message: str)
            error_message is empty on success

        """
        import time

        if not self.token:
            return False, "No HA token available"

        if self._already_at(entity_id, value):
            logger.debug(f"{entity_id} already at {value} — no write sent")
            return True, ""

        url = self._api_url("/services/number/set_value")
        data = {
            "entity_id": entity_id,
            "value": value,
        }

        last_error = ""
        for attempt in range(1, max_retries + 1):
            # Before every re-post, check whether the previous attempt actually
            # landed and only its response was lost.
            if attempt > 1 and self._already_at(entity_id, value):
                logger.info(
                    f"{entity_id} reached {value} despite '{last_error}' — "
                    f"not re-sending"
                )
                return True, ""
            try:
                logger.debug(f"POST {url} with {data} (attempt {attempt}/{max_retries})")
                response = requests.post(
                    url, headers=self._headers(), json=data, timeout=30
                )
                response.raise_for_status()
                logger.info(f"Set {entity_id} to {value}")
                return True, ""
            except requests.Timeout:
                last_error = f"Timeout after 30s (attempt {attempt})"
                logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
            except requests.ConnectionError as e:
                last_error = f"Connection error: {e} (attempt {attempt})"
                logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
            except requests.HTTPError as e:
                last_error = f"HTTP error {e.response.status_code}: {e} (attempt {attempt})"
                logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")
            except Exception as e:
                last_error = f"Unexpected error: {e} (attempt {attempt})"
                logger.warning(f"Attempt {attempt}/{max_retries}: {last_error}")

            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay}s...")
                time.sleep(retry_delay)

        logger.error(f"Failed to set {entity_id} after {max_retries} attempts: {last_error}")
        return False, last_error

    def get_battery_soc(self, entity_id: str = "sensor.battery_state_of_capacity") -> float | None:
        """Get current battery SOC.

        Returns:
            SOC as percentage (0-100) or None on error

        """
        soc = self.get_sensor_value(entity_id)
        if soc is not None:
            logger.debug(f"Battery SOC: {soc}%")
        return soc

    def get_number_value(self, entity_id: str) -> float | None:
        """Get numeric value from a number entity.

        Args:
            entity_id: The number entity to read

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
            logger.error(f"Failed to parse number entity {entity_id}: {e}")
            return None

    def get_battery_discharge_power(self, entity_id: str) -> float | None:
        """Get current maximum battery discharge power setting.

        Args:
            entity_id: The number entity to read

        Returns:
            Current power setting in watts, or None on error

        """
        value = self.get_number_value(entity_id)
        if value is not None:
            logger.debug(f"Current discharge power setting: {value}W")
        return value

    def set_battery_discharge_power(
        self,
        entity_id: str,
        power_w: float,
        max_retries: int = 5,
    ) -> tuple[bool, str]:
        """Set maximum battery discharge power with retry logic.

        Args:
            entity_id: The number entity to control
            power_w: Maximum discharge power in watts (0 = block discharge)
            max_retries: Maximum number of attempts

        Returns:
            Tuple of (success: bool, error_message: str)

        """
        return self.set_number(entity_id, power_w, max_retries=max_retries)

    def get_input_boolean(self, entity_id: str) -> bool:
        """Get input_boolean state.

        Args:
            entity_id: The input_boolean entity ID

        Returns:
            True if "on", False otherwise (including errors)

        """
        state = self.get_state(entity_id)
        return state is not None and state.get("state") == "on"

    def get_optional_bool(self, entity_id: str) -> bool | None:
        """Read a toggle, distinguishing "off" from "not there".

        `get_input_boolean` collapses a missing entity into False, which is
        unusable for a setting that falls back to a configured default — an
        absent helper would silently read as "off" and disable the feature.
        This returns None for missing, `unknown` or `unavailable`, so the
        caller can apply its default instead.

        Args:
            entity_id: The input_boolean entity ID

        Returns:
            True / False when the entity holds a definite state, else None.

        """
        state = self.get_state(entity_id)
        if not state:
            return None
        value = state.get("state")
        if value == "on":
            return True
        if value == "off":
            return False
        return None

    def set_input_boolean(self, entity_id: str, state: bool) -> bool:
        """Set input_boolean on or off.

        Args:
            entity_id: The input_boolean entity ID
            state: True for on, False for off

        Returns:
            True on success, False on error

        """
        if not self.token:
            logger.warning("No token available for set_input_boolean")
            return False

        service = "turn_on" if state else "turn_off"
        try:
            url = self._api_url(f"/services/input_boolean/{service}")
            data = {"entity_id": entity_id}
            logger.debug(f"POST {url} with {data}")
            response = requests.post(
                url, headers=self._headers(), json=data, timeout=30
            )
            response.raise_for_status()
            logger.info(f"Set {entity_id} to {'on' if state else 'off'}")
            return True
        except Exception as e:
            logger.error(f"Failed to set {entity_id}: {e}")
            return False

    def get_input_select(self, entity_id: str) -> str:
        """Get input_select state."""
        state = self.get_state(entity_id)
        return state.get("state", "") if state else ""

    def set_input_select(self, entity_id: str, option: str) -> bool:
        """Set input_select to a specific option."""
        if not self.token:
            logger.warning("No token available for set_input_select")
            return False

        try:
            url = self._api_url("/services/input_select/select_option")
            data = {"entity_id": entity_id, "option": option}
            logger.debug(f"POST {url} with {data}")
            response = requests.post(
                url, headers=self._headers(), json=data, timeout=30
            )
            response.raise_for_status()
            logger.info(f"Set {entity_id} to '{option}'")
            return True
        except Exception as e:
            logger.error(f"Failed to set {entity_id}: {e}")
            return False

    def create_notification(
        self, message: str, title: str, notification_id: str
    ) -> bool:
        """Create/replace a Home Assistant persistent notification (UI feedback).

        A fixed ``notification_id`` means repeated calls replace the same
        notification instead of stacking.
        """
        if not self.token:
            logger.warning("No token available for create_notification")
            return False
        try:
            url = self._api_url("/services/persistent_notification/create")
            data = {
                "message": message,
                "title": title,
                "notification_id": notification_id,
            }
            response = requests.post(
                url, headers=self._headers(), json=data, timeout=30
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to create notification: {e}")
            return False

    def set_sensor_state(
        self,
        entity_id: str,
        state: Any,
        attributes: dict | None = None,
    ) -> bool:
        """Set a sensor entity state directly via REST API.

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
