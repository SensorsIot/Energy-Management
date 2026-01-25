#!/usr/bin/env python3
"""
OCPP Server Add-on for Home Assistant.

Provides OCPP 1.6j WebSocket server for wallbox communication.
Receives commands from EnergyManager via MQTT.
"""

__version__ = "0.1.0"

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import websockets

from src.ocpp_handler import ChargePointHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ocpp-server")


class OCPPServer:
    """OCPP 1.6j WebSocket server with MQTT integration."""

    def __init__(self, options: dict):
        self.options = options
        self.mqtt_topic_prefix = options.get("mqtt_topic_prefix", "ocpp")
        self.wallbox_id = options.get("wallbox_id", "wallbox1")
        self.min_current_a = options.get("min_current_a", 6)
        self.max_current_a = options.get("max_current_a", 16)

        self.charge_point: Optional[ChargePointHandler] = None
        self.mqtt_client: Optional[mqtt.Client] = None
        self.ws_server = None
        self.running = False

    def setup_mqtt(self):
        """Set up MQTT connection for EnergyManager communication."""
        mqtt_host = os.environ.get("MQTT_HOST", "core-mosquitto")
        mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        mqtt_user = os.environ.get("MQTT_USER", "")
        mqtt_pass = os.environ.get("MQTT_PASS", "")

        self.mqtt_client = mqtt.Client(client_id="ocpp-server")

        if mqtt_user:
            self.mqtt_client.username_pw_set(mqtt_user, mqtt_pass)

        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_message = self._on_mqtt_message

        try:
            self.mqtt_client.connect(mqtt_host, mqtt_port)
            self.mqtt_client.loop_start()
            logger.info(f"MQTT connected to {mqtt_host}:{mqtt_port}")
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connected - subscribe to command topics."""
        if rc == 0:
            # Subscribe to commands from EnergyManager
            topics = [
                f"{self.mqtt_topic_prefix}/command/set_power",
                f"{self.mqtt_topic_prefix}/command/start",
                f"{self.mqtt_topic_prefix}/command/stop",
                f"{self.mqtt_topic_prefix}/command/trigger_meter",
            ]
            for topic in topics:
                client.subscribe(topic)
                logger.info(f"Subscribed to {topic}")
        else:
            logger.error(f"MQTT connect failed with code {rc}")

    def _on_mqtt_message(self, client, userdata, msg):
        """Handle MQTT commands from EnergyManager."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        logger.info(f"MQTT command: {topic} = {payload}")

        # Process command asynchronously
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.create_task(self._handle_command(topic, payload))
        )

    async def _handle_command(self, topic: str, payload: str):
        """Process command from EnergyManager."""
        if self.charge_point is None:
            logger.warning("No wallbox connected, ignoring command")
            return

        try:
            if topic.endswith("/set_power"):
                # Payload: {"power_w": 7000, "phases": 3}
                data = json.loads(payload)
                power_w = data.get("power_w", 0)
                phases = data.get("phases", 3)
                await self.charge_point.set_charging_power(power_w, phases)

            elif topic.endswith("/start"):
                await self.charge_point.remote_start()

            elif topic.endswith("/stop"):
                await self.charge_point.remote_stop()

            elif topic.endswith("/trigger_meter"):
                await self.charge_point.trigger_meter_values()

        except Exception as e:
            logger.error(f"Command handling failed: {e}")

    def _on_status_change(self, key: str, value):
        """Callback when wallbox status changes - publish to MQTT."""
        if self.mqtt_client:
            topic = f"{self.mqtt_topic_prefix}/status/{key}"
            payload = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            self.mqtt_client.publish(topic, payload, retain=True)
            logger.debug(f"Published: {topic} = {payload}")

    async def handle_websocket(self, websocket, path):
        """Handle incoming WebSocket connection from wallbox."""
        # Extract charge point ID from path (e.g., /AcTec001)
        cp_id = path.strip("/").split("/")[-1] if "/" in path else self.wallbox_id
        logger.info(f"Wallbox connecting: id={cp_id}, path={path}")

        # Create charge point handler
        self.charge_point = ChargePointHandler(
            cp_id,
            websocket,
            on_status_change=self._on_status_change,
        )

        # Publish connected status
        self._on_status_change("connected", True)
        self._on_status_change("wallbox_id", cp_id)

        try:
            # Start message handler
            await self.charge_point.start()
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Wallbox disconnected: {cp_id}")
        finally:
            self._on_status_change("connected", False)
            self.charge_point = None

    async def start_server(self):
        """Start WebSocket server."""
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

        # Keep running until stopped
        while self.running:
            await asyncio.sleep(1)

    def stop(self):
        """Stop the server."""
        self.running = False
        if self.ws_server:
            self.ws_server.close()
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        logger.info("OCPP server stopped")


def load_options() -> dict:
    """Load add-on options from /data/options.json."""
    options_path = Path("/data/options.json")
    if options_path.exists():
        with open(options_path) as f:
            return json.load(f)
    return {}


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info(f"OCPP Server Add-on v{__version__}")
    logger.info("=" * 60)

    options = load_options()
    logger.info(f"Config: wallbox_id={options.get('wallbox_id', 'wallbox1')}")

    server = OCPPServer(options)
    server.setup_mqtt()

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        server.stop()
        loop.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Run server
    try:
        loop.run_until_complete(server.start_server())
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        loop.close()


if __name__ == "__main__":
    main()
