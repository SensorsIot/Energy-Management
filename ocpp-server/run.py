#!/usr/bin/env python3
"""
OCPP Server Add-on for Home Assistant.

Provides OCPP 1.6j WebSocket server for wallbox communication.
Communicates with EnergyManager via HA entities (REST API).
"""

__version__ = "0.9.16"

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

import aiohttp
import aiomqtt
import websockets

from src.ha_entities import BINARY_SENSORS, CONTROLS, SENSORS
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
                elif resp.status == 404:
                    logger.debug(f"Entity {entity_id} not found (404)")
                    return None
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
        self.single_phase_supported = options.get("single_phase_supported", False)
        self.power_update_interval_s = options.get("power_update_interval_s", 60)

        self.charge_point: Optional[ChargePointHandler] = None
        self.ha = HAEntityManager()
        self.ws_server = None
        self.running = False

        # MQTT config for ESP32 Modbus Proxy power correction
        self._mqtt_host = options.get("mqtt_host", "192.168.0.203")
        self._mqtt_port = options.get("mqtt_port", 1883)
        self._mqtt_topic = options.get("mqtt_topic", "wallbox")
        self._mqtt_client: Optional[aiomqtt.Client] = None
        self._last_mqtt_power: float = 0.0  # re-publish periodically to prevent staleness

        # Phase switching state
        self._current_phases = 3
        self._phase_threshold_w = self.min_current_a * 230 * 3

        # Track last-seen control states for change detection
        self._last_power_limit: Optional[str] = None

        # Throttle state for SetChargingProfile rate-limiting
        self._last_change_at: float = 0.0  # When value last changed
        self._pending_power_w: float | None = None

        # Synchronization: _watch_controls waits until _post_connect_setup finishes
        self._setup_complete = asyncio.Event()

    async def _mqtt_loop(self):
        """Maintain MQTT connection and reconnect on failure."""
        if not self._mqtt_host:
            logger.info("MQTT disabled (no mqtt_host configured)")
            return
        while self.running:
            try:
                async with aiomqtt.Client(
                    hostname=self._mqtt_host,
                    port=self._mqtt_port,
                ) as client:
                    self._mqtt_client = client
                    logger.info(
                        f"MQTT connected to {self._mqtt_host}:{self._mqtt_port}, "
                        f"topic={self._mqtt_topic}"
                    )
                    # Re-publish last known power every 10s to prevent
                    # Modbus Proxy staleness (30s timeout on ESP32)
                    while self.running:
                        await asyncio.sleep(10)
                        await self._publish_mqtt_power(self._last_mqtt_power)
            except aiomqtt.MqttError as e:
                self._mqtt_client = None
                if self.running:
                    logger.warning(f"MQTT connection lost: {e}, reconnecting in 5s")
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._mqtt_client = None
                if self.running:
                    logger.error(f"MQTT error: {e}, reconnecting in 10s")
                    await asyncio.sleep(10)
        self._mqtt_client = None

    async def _publish_mqtt_power(self, power_w: float):
        """Publish wallbox power to MQTT for ESP32 Modbus Proxy correction."""
        self._last_mqtt_power = power_w
        if self._mqtt_client is None:
            return
        try:
            await self._mqtt_client.publish(self._mqtt_topic, str(power_w))
            logger.debug(f"MQTT publish {self._mqtt_topic}: {power_w}W")
        except Exception as e:
            logger.warning(f"MQTT publish failed: {e}")

    def _on_status_change(self, key: str, value):
        """Callback when wallbox status changes — update HA entity and MQTT."""
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

        # Publish wallbox power to MQTT for ESP32 Modbus Proxy correction
        if key == "power_w":
            asyncio.ensure_future(self._publish_mqtt_power(float(value)))

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

    async def _meter_values_watchdog(self):
        """Reset power to 0 if no MeterValues received for 2 minutes."""
        METER_VALUES_TIMEOUT = 120  # seconds
        while self.running:
            await asyncio.sleep(10)
            cp = self.charge_point
            if cp is None or cp.current_power_w == 0:
                continue
            if cp.last_meter_values_time == 0:
                continue
            age = time.monotonic() - cp.last_meter_values_time
            if age > METER_VALUES_TIMEOUT:
                logger.warning(
                    f"No MeterValues for {age:.0f}s — resetting power from "
                    f"{cp.current_power_w}W to 0W"
                )
                cp.current_power_w = 0
                self._on_status_change("power_w", 0)

    async def _send_power_to_wallbox(self, power_w: float):
        """Send power limit to wallbox (phase switching, auto-start, SetChargingProfile).

        This method contains the actual wallbox communication logic,
        extracted from _watch_controls so it can be gated by the throttle.
        """
        if not self.charge_point:
            logger.warning("No wallbox connected, ignoring power limit")
            return

        # Phase switching (before setting profile)
        if power_w > 0 and self.phase_switch_entity:
            if power_w < self._phase_threshold_w:
                target_phases = 1
            else:
                target_phases = 3
            await self._switch_phases(target_phases)

        if power_w > 0 and self.charge_point.transaction_id is None:
            # No transaction yet — send profile first, then start
            logger.info("No active transaction, setting profile then starting")
            await self.charge_point.set_charging_power(
                power_w, num_phases=self._current_phases
            )
            await asyncio.sleep(3)
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
                    return
            else:
                logger.warning("RemoteStartTransaction not accepted")
        else:
            # Transaction active (or pausing) — just update profile
            await self.charge_point.set_charging_power(
                power_w, num_phases=self._current_phases
            )

        # When pausing (0W), reset reported power immediately
        # Wallbox may not send MeterValues with 0W
        if power_w == 0 and self.charge_point and self.charge_point.current_power_w > 0:
            logger.info("Power limit set to 0W — resetting reported power")
            self.charge_point.current_power_w = 0
            self._on_status_change("power_w", 0)

        self._pending_power_w = None
        logger.info(f"Sent power profile: {power_w}W")

    async def _sync_ha_state(self):
        """Re-publish current wallbox state to HA after entity recovery."""
        cp = self.charge_point
        connected = cp is not None
        await self.ha.set_state(
            "binary_sensor.wallbox_connected",
            "on" if connected else "off",
        )
        await self.ha.set_state(
            "binary_sensor.wallbox_single_phase_supported",
            "on" if self.single_phase_supported else "off",
        )
        await self.ha.set_state("sensor.wallbox_phases", self._current_phases)
        if cp:
            await self.ha.set_state("sensor.wallbox_status", cp.current_status)
            await self.ha.set_state("sensor.wallbox_power", cp.current_power_w)
            await self.ha.set_state("sensor.wallbox_energy", cp.session_energy_wh)
            txn = "charging" if cp.transaction_id is not None else "idle"
            await self.ha.set_state("sensor.wallbox_transaction", txn)
        logger.info(f"Synced HA state (connected={connected})")

    async def _watch_controls(self):
        """Poll HA control entities for changes from EnergyManager.

        Detected changes are queued in _pending_power_w and only sent
        to the wallbox when power_update_interval_s has elapsed since the
        last SetChargingProfile, preventing wallbox oscillation.
        """
        logger.info("Control watcher started")
        while self.running:
            await asyncio.sleep(1)
            # Wait for post-connect setup to finish before processing controls
            if not self._setup_complete.is_set():
                await self._setup_complete.wait()
            try:
                # Power limit (number entity)
                power_state = await self.ha.get_state("number.wallbox_power_limit")
                if power_state is None:
                    # Entity lost (e.g. HA core restarted) — re-register all entities
                    logger.warning("Control entity missing, re-registering HA entities")
                    await self.ha.register_entities()
                    await self._sync_ha_state()
                    await asyncio.sleep(5)
                    continue

                # Detect change
                if power_state != self._last_power_limit:
                    prev = self._last_power_limit
                    self._last_power_limit = power_state
                    if prev is not None:
                        try:
                            power_w = float(power_state)
                            since_last_change = time.monotonic() - self._last_change_at
                            self._last_change_at = time.monotonic()
                            if since_last_change >= self.power_update_interval_s:
                                # No recent change — send immediately
                                logger.info(f"Power limit changed to {power_w}W (sending immediately, {since_last_change:.0f}s since last change)")
                                await self._send_power_to_wallbox(power_w)
                            else:
                                # Rapid change — queue, send when interval expires
                                logger.info(f"Power limit changed to {power_w}W (throttled, {since_last_change:.0f}s since last change)")
                                self._pending_power_w = power_w
                        except ValueError:
                            logger.warning(f"Invalid power limit value: {power_state}")

                # Send throttled value when interval expires
                if self._pending_power_w is not None:
                    since_last_change = time.monotonic() - self._last_change_at
                    if since_last_change >= self.power_update_interval_s:
                        power_w = self._pending_power_w
                        await self._send_power_to_wallbox(power_w)

            except Exception as e:
                logger.error(f"Control watcher error: {e}")

    async def _post_connect_setup(self):
        """Sync state after wallbox connects (FSD 4.5).

        Accepts any message (Boot, Status, or Heartbeat) as proof the
        wallbox is alive.  Triggers MeterValues to recover power/energy/
        transaction state.  Does NOT start a transaction — that happens
        in _watch_controls when EnergyManager requests power.
        """
        ALREADY_ACTIVE = {"Charging", "SuspendedEV", "SuspendedEVSE"}
        CAR_PRESENT = {"Preparing"} | ALREADY_ACTIVE

        if not self.charge_point:
            return

        # Wait for first message from wallbox (Boot, Status, or Heartbeat)
        boot = asyncio.create_task(self.charge_point.boot_event.wait())
        status = asyncio.create_task(self.charge_point.status_event.wait())
        heartbeat = asyncio.create_task(self.charge_point.heartbeat_event.wait())
        done, pending = await asyncio.wait(
            {boot, status, heartbeat}, timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if done:
            which = []
            if self.charge_point.boot_event.is_set():
                which.append("boot")
            if self.charge_point.status_event.is_set():
                which.append("status")
            if self.charge_point.heartbeat_event.is_set():
                which.append("heartbeat")
            logger.info(f"Post-connect: wallbox ready ({', '.join(which)} received)")
        else:
            logger.warning("Post-connect: no message from wallbox after 30s, giving up")
            return

        if not self.charge_point:
            return

        # Request MeterValues to sync power/energy/transaction_id
        await self.charge_point.trigger_meter_values()

        ws = self.charge_point.current_status
        logger.info(f"Post-connect: status={ws}")

        if ws in ALREADY_ACTIVE:
            logger.info(f"Wallbox already active ({ws}), recovering transaction state")
        elif ws in CAR_PRESENT:
            logger.info(f"Car present ({ws}), waiting for EnergyManager to request power")
        else:
            logger.info(f"Post-connect: no car ({ws}), waiting for plug-in")

        self._setup_complete.set()
        logger.info("Post-connect setup complete")

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

        # Reset power limit tracking so _watch_controls re-applies current value
        self._last_power_limit = "0"

        # Block _watch_controls until post-connect setup finishes
        self._setup_complete.clear()

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

        # Set single_phase_supported from config
        await self.ha.set_state(
            "binary_sensor.wallbox_single_phase_supported",
            "on" if self.single_phase_supported else "off",
        )
        logger.info(f"Single phase supported: {self.single_phase_supported}")

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

        # Run control watcher, MQTT loop, and MeterValues watchdog alongside the server
        watcher = asyncio.create_task(self._watch_controls())
        mqtt_task = asyncio.create_task(self._mqtt_loop())
        mv_watchdog = asyncio.create_task(self._meter_values_watchdog())
        try:
            while self.running:
                await asyncio.sleep(1)
        finally:
            watcher.cancel()
            mqtt_task.cancel()
            mv_watchdog.cancel()

    async def stop(self):
        """Stop the server, MQTT, and close HA session."""
        self.running = False
        self._mqtt_client = None
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
    logger.info(
        f"MQTT: host={options.get('mqtt_host', '')}, "
        f"port={options.get('mqtt_port', 1883)}, "
        f"topic={options.get('mqtt_topic', 'wallbox')}"
    )

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
