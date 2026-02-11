#!/usr/bin/env python3
"""
OCPP Server Add-on for Home Assistant.

Provides OCPP 1.6j WebSocket server for wallbox communication.
Communicates with EnergyManager via HA entities (REST API).
"""

__version__ = "0.8.3"

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import aiohttp
import websockets

from src.ha_entities import ALL_ENTITIES, BINARY_SENSORS, CONTROLS, SENSORS
from src.ocpp_handler import ChargePointHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ocpp-server")

# Status change callback key → HA entity mapping
STATUS_ENTITY_MAP = {
    "power_w": "sensor.wallbox_power",
    "energy_wh": "sensor.wallbox_energy",
    "status": "sensor.wallbox_status",
    "connected": "binary_sensor.wallbox_connected",
    "transaction": "sensor.wallbox_transaction",
    "phases": "sensor.wallbox_phases",
}


class HAEntityManager:
    """Async Home Assistant REST API client for entity management."""

    def __init__(self, url: str = "http://supervisor/core/api"):
        self.url = url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def _token(self) -> Optional[str]:
        """Get supervisor token from environment."""
        return os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def start(self):
        """Create aiohttp session."""
        self._session = aiohttp.ClientSession()

    async def stop(self):
        """Close aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def set_state(self, entity_id: str, state, attributes: Optional[dict] = None):
        """Set entity state via POST /api/states/{entity_id}."""
        if not self._session:
            logger.warning("HAEntityManager not started, cannot set state")
            return
        url = f"{self.url}/states/{entity_id}"
        data = {
            "state": str(state),
            "attributes": attributes or {},
        }
        try:
            async with self._session.post(url, headers=self._headers, json=data) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error(f"Failed to set {entity_id}: {resp.status} {text}")
                else:
                    logger.debug(f"Set {entity_id} = {state}")
        except Exception as e:
            logger.error(f"Error setting {entity_id}: {e}")

    async def get_state(self, entity_id: str) -> Optional[str]:
        """Get entity state via GET /api/states/{entity_id}. Returns state string."""
        if not self._session:
            return None
        url = f"{self.url}/states/{entity_id}"
        try:
            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("state")
                else:
                    logger.error(f"Failed to get {entity_id}: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Error getting {entity_id}: {e}")
            return None

    async def call_service(self, domain: str, service: str, data: dict) -> bool:
        """Call HA service via POST /api/services/{domain}/{service}."""
        if not self._session:
            logger.warning("HAEntityManager not started, cannot call service")
            return False
        url = f"{self.url}/services/{domain}/{service}"
        try:
            async with self._session.post(url, headers=self._headers, json=data) as resp:
                if resp.status == 200:
                    logger.debug(f"Called {domain}.{service}: {data}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Failed to call {domain}.{service}: {resp.status} {text}")
                    return False
        except Exception as e:
            logger.error(f"Error calling {domain}.{service}: {e}")
            return False

    async def register_entities(self):
        """Register HA entity attributes; only set defaults for connectivity and controls.

        Sensor states (status, power, energy, transaction, phases) are NOT
        reset — the wallbox is the source of truth and will populate them
        via StatusNotification / MeterValues after it connects.
        """
        # Sensors: register attributes only, preserve existing state
        for entity_id, defn in SENSORS.items():
            attrs = {k: v for k, v in defn.items() if k not in ("initial_state",)}
            existing = await self.get_state(entity_id)
            state = existing if existing is not None else defn["initial_state"]
            await self.set_state(entity_id, state, attrs)

        # Binary sensors: wallbox_connected = off (server just started, no WS yet)
        for entity_id, defn in BINARY_SENSORS.items():
            attrs = {k: v for k, v in defn.items() if k not in ("initial_state",)}
            await self.set_state(entity_id, "off", attrs)

        # Controls: preserve existing value (EnergyManager may have set it)
        for entity_id, defn in CONTROLS.items():
            attrs = {k: v for k, v in defn.items() if k not in ("initial_state",)}
            existing = await self.get_state(entity_id)
            state = existing if existing is not None else defn.get("initial_state", 0)
            await self.set_state(entity_id, state, attrs)

        logger.info("Registered HA entities (preserved existing sensor states)")


class OCPPServer:
    """OCPP 1.6j WebSocket server with HA entity integration."""

    def __init__(self, options: dict):
        self.options = options
        self.wallbox_id = options.get("wallbox_id", "wallbox1")
        self.min_current_a = options.get("min_current_a", 6)
        self.max_current_a = options.get("max_current_a", 16)
        self.phase_switch_entity = options.get("phase_switch_entity", "")

        self.charge_point: Optional[ChargePointHandler] = None
        self.ha = HAEntityManager()
        self.ws_server = None
        self.running = False

        # Phase switching state
        self._current_phases = 3
        self._phase_threshold_w = self.min_current_a * 230 * 3

        # Track last-seen control states for change detection
        self._last_power_limit: Optional[str] = None

    def _on_status_change(self, key: str, value):
        """Callback when wallbox status changes — update HA entity."""
        entity_id = STATUS_ENTITY_MAP.get(key)
        if not entity_id:
            logger.debug(f"No entity mapping for status key: {key}")
            return

        # Convert boolean connected flag to on/off
        if key == "connected":
            state = "on" if value else "off"
        # Map transaction started/stopped to charging/idle
        elif key == "transaction":
            state = "charging" if value == "started" else "idle"
        else:
            state = value

        asyncio.ensure_future(self.ha.set_state(entity_id, state))

    async def _switch_phases(self, target_phases: int):
        """Switch between 1-phase and 3-phase charging via EARU relay.

        Safety sequence:
        1. Pause charging (set limit to 0 A)
        2. Wait 2 s for current to drop
        3. Toggle relay (ON = 3-phase, OFF = 1-phase)
        4. Wait 3 s for relay to settle
        5. (Caller resumes with new phase count)
        """
        if target_phases == self._current_phases:
            return
        if not self.phase_switch_entity:
            return

        logger.info(f"Phase switch: {self._current_phases} → {target_phases}")

        # Step 1: Pause charging
        if self.charge_point:
            await self.charge_point.set_charging_power(0, num_phases=self._current_phases)
        await asyncio.sleep(2)

        # Step 2: Toggle relay
        domain = self.phase_switch_entity.split(".")[0]
        service = "turn_on" if target_phases == 3 else "turn_off"
        entity_data = {"entity_id": self.phase_switch_entity}
        ok = await self.ha.call_service(domain, service, entity_data)
        if not ok:
            logger.error(f"Failed to switch relay to {target_phases}-phase")
            return
        await asyncio.sleep(3)

        # Step 3: Update state
        self._current_phases = target_phases
        self._on_status_change("phases", target_phases)
        logger.info(f"Phase switch complete: now {target_phases}-phase")

    async def _watch_controls(self):
        """Poll HA control entities for changes from EnergyManager."""
        logger.info("Control watcher started")
        while self.running:
            await asyncio.sleep(1)
            try:
                # Power limit (number entity)
                power_state = await self.ha.get_state("number.wallbox_power_limit")
                if power_state is not None and power_state != self._last_power_limit:
                    prev = self._last_power_limit
                    self._last_power_limit = power_state
                    if prev is not None:
                        # Value changed — send to wallbox
                        try:
                            power_w = float(power_state)
                            logger.info(f"Power limit changed to {power_w}W")
                            if self.charge_point:
                                # Auto-transaction: start when power requested, never auto-stop
                                # (0W just pauses via SetChargingProfile 0A, transaction stays alive)
                                if power_w > 0 and self.charge_point.transaction_id is None:
                                    logger.info("No active transaction, starting one first")
                                    self.charge_point.transaction_started_event.clear()
                                    ok = await self.charge_point.remote_start()
                                    if ok:
                                        try:
                                            await asyncio.wait_for(
                                                self.charge_point.transaction_started_event.wait(),
                                                timeout=15,
                                            )
                                        except asyncio.TimeoutError:
                                            logger.warning("StartTransaction not received after 15s")
                                            continue
                                    else:
                                        logger.warning("RemoteStartTransaction not accepted")

                                # Phase switching
                                if power_w > 0 and self.phase_switch_entity:
                                    if power_w < self._phase_threshold_w:
                                        target_phases = 1
                                    else:
                                        target_phases = 3
                                    await self._switch_phases(target_phases)

                                # Set charging profile
                                await self.charge_point.set_charging_power(
                                    power_w, num_phases=self._current_phases
                                )
                            else:
                                logger.warning("No wallbox connected, ignoring power limit")
                        except ValueError:
                            logger.warning(f"Invalid power limit value: {power_state}")

            except Exception as e:
                logger.error(f"Control watcher error: {e}")

    async def _post_connect_setup(self):
        """Sync state after wallbox connects.

        Only syncs — does NOT start a transaction.  Transaction start is
        handled by _watch_controls when the EnergyManager requests power
        (exactly like v0.5.0 which worked reliably).
        """
        ALREADY_ACTIVE = {"Charging", "SuspendedEV", "SuspendedEVSE"}

        if not self.charge_point:
            return

        # Wait for first message from wallbox (Boot or Status)
        boot = asyncio.create_task(self.charge_point.boot_event.wait())
        status = asyncio.create_task(self.charge_point.status_event.wait())
        done, pending = await asyncio.wait(
            {boot, status}, timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if done:
            logger.info("Post-connect: wallbox ready (boot or status received)")
        else:
            logger.warning("Post-connect: no message from wallbox after 30s, giving up")
            return

        if not self.charge_point:
            return

        ws = self.charge_point.current_status
        logger.info(f"Post-connect: status={ws}")

        # Request MeterValues to sync power/energy/transaction_id
        await self.charge_point.trigger_meter_values()

        if ws in ALREADY_ACTIVE:
            logger.info(f"Wallbox already active ({ws}), recovering transaction state")
        elif not self.charge_point.boot_event.is_set():
            # No BootNotification → this is a WebSocket reconnect, not a fresh boot.
            # Reset the wallbox to get a clean boot with proper pilot signal init.
            logger.info(f"No BootNotification received, sending Reset to reinitialize")
            await self.charge_point.reset()
            # Wallbox will disconnect and reconnect with BootNotification
        else:
            logger.info(f"Post-connect: idle ({ws}), waiting for EnergyManager power request")

    async def handle_websocket(self, websocket):
        """Handle incoming WebSocket connection from wallbox."""
        # Extract charge point ID from path (e.g., /AcTec001)
        # websockets v11+: path is on the request object
        path = websocket.request.path if hasattr(websocket, 'request') else "/"
        cp_id = path.strip("/").split("/")[-1] if path.strip("/") else self.wallbox_id
        logger.info(f"Wallbox connecting: id={cp_id}, path={path}")

        # Create charge point handler
        cp = ChargePointHandler(
            cp_id,
            websocket,
            on_status_change=self._on_status_change,
        )
        self.charge_point = cp

        # Reset power limit tracking so _watch_controls re-evaluates
        self._last_power_limit = None

        # Publish connected status
        self._on_status_change("connected", True)
        self._on_status_change("wallbox_id", cp_id)

        setup_task = None
        try:
            setup_task = asyncio.create_task(self._post_connect_setup())
            await cp.start()
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Wallbox disconnected: {cp_id}")
        finally:
            if setup_task and not setup_task.done():
                setup_task.cancel()
            # Only clear state if this is still the active connection
            if self.charge_point is cp:
                self._on_status_change("connected", False)
                self._on_status_change("power_w", 0)
                self._on_status_change("transaction", "stopped")
                self.charge_point = None

    async def start_server(self):
        """Start WebSocket server and HA integration."""
        # Initialize HA entity manager
        await self.ha.start()
        await self.ha.register_entities()

        # Read initial relay state to sync phase count
        if self.phase_switch_entity:
            relay_state = await self.ha.get_state(self.phase_switch_entity)
            if relay_state == "on":
                self._current_phases = 3
            elif relay_state == "off":
                self._current_phases = 1
            else:
                self._current_phases = 3  # default if unavailable
            await self.ha.set_state("sensor.wallbox_phases", self._current_phases)
            logger.info(
                f"Phase switch entity: {self.phase_switch_entity}, "
                f"relay={relay_state}, phases={self._current_phases}"
            )

        host = "0.0.0.0"
        port = self.options.get("ws_port", 8887)

        logger.info(f"Starting OCPP WebSocket server on ws://{host}:{port}")

        self.ws_server = await websockets.serve(
            self.handle_websocket,
            host,
            port,
            subprotocols=["ocpp1.6"],
        )

        self.running = True
        logger.info("OCPP server ready, waiting for wallbox connection...")

        # Run control watcher alongside the server
        watcher = asyncio.create_task(self._watch_controls())
        try:
            while self.running:
                await asyncio.sleep(1)
        finally:
            watcher.cancel()

    async def stop(self):
        """Stop the server and close HA session."""
        self.running = False
        if self.ws_server:
            self.ws_server.close()
        await self.ha.stop()
        logger.info("OCPP server stopped")


def load_options() -> dict:
    """Load add-on options from /data/options.json."""
    options_path = Path("/data/options.json")
    if options_path.exists():
        with open(options_path) as f:
            return json.load(f)
    return {}


async def async_main():
    """Async main entry point."""
    logger.info("=" * 60)
    logger.info(f"OCPP Server Add-on v{__version__}")
    logger.info("=" * 60)

    options = load_options()
    logger.info(f"Config: wallbox_id={options.get('wallbox_id', 'wallbox1')}")

    server = OCPPServer(options)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def shutdown(signum):
        logger.info(f"Received signal {signum}, shutting down...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown, sig)

    # Run server until stop signal
    server_task = asyncio.create_task(server.start_server())
    await stop_event.wait()
    await server.stop()
    server_task.cancel()


def main():
    """Main entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
