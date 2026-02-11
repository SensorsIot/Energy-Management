#!/usr/bin/env python3
"""
Wallbox OCPP 1.6J simulator for testing the OCPP Server add-on.

Uses the ocpp library as a client to match exact message format.

Usage:
    python3 wallbox_simulator.py [ws://HOST:PORT/CHARGEPOINT_ID]

Default: ws://192.168.0.202:8887/AcTec001
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    Action,
    ChargePointStatus,
    RegistrationStatus,
    AuthorizationStatus,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wallbox-sim")


class WallboxSimulator(CP):
    """Simulated wallbox using the ocpp library (acts as ChargePoint client)."""

    def __init__(self, id, connection):
        super().__init__(id, connection)
        self.charging = False
        self.transaction_id = None
        self.power_w = 0
        self.energy_wh = 0
        self.num_phases = 3

    # === Incoming commands from server ===

    @on(Action.set_charging_profile)
    async def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        schedule = cs_charging_profiles.get("charging_schedule", {})
        periods = schedule.get("charging_schedule_period", [])
        if periods:
            limit = periods[0].get("limit", 0)
            phases = periods[0].get("number_phases", 3)
            self.power_w = limit * 230 * phases
            self.num_phases = phases
            logger.info(f"  Profile set: {limit:.1f}A x {phases}ph = {self.power_w:.0f}W")
        return call_result.SetChargingProfile(status="Accepted")

    @on(Action.remote_start_transaction)
    async def on_remote_start(self, id_tag, **kwargs):
        logger.info(f"  Remote start requested (tag={id_tag})")
        # Accept, then send StartTransaction
        asyncio.create_task(self._do_start_transaction(id_tag))
        return call_result.RemoteStartTransaction(status="Accepted")

    @on(Action.remote_stop_transaction)
    async def on_remote_stop(self, transaction_id, **kwargs):
        logger.info(f"  Remote stop requested (txn={transaction_id})")
        asyncio.create_task(self._do_stop_transaction())
        return call_result.RemoteStopTransaction(status="Accepted")

    @on(Action.trigger_message)
    async def on_trigger_message(self, requested_message, **kwargs):
        logger.info(f"  Trigger: {requested_message}")
        if requested_message == "MeterValues":
            asyncio.create_task(self._send_meter_values())
        return call_result.TriggerMessage(status="Accepted")

    # === Outgoing messages to server ===

    async def send_boot(self):
        req = call.BootNotification(
            charge_point_vendor="AcTec-Sim",
            charge_point_model="Simulator",
            charge_point_serial_number="SIM001",
        )
        resp = await self.call(req)
        logger.info(f"  Boot: {resp.status}")
        return resp.status == RegistrationStatus.accepted.value

    async def send_status(self, status: str):
        req = call.StatusNotification(
            connector_id=1,
            error_code="NoError",
            status=status,
        )
        await self.call(req)
        logger.info(f"  Status: {status}")

    async def _do_start_transaction(self, id_tag: str):
        await asyncio.sleep(0.5)
        req = call.StartTransaction(
            connector_id=1,
            id_tag=id_tag,
            meter_start=int(self.energy_wh),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        resp = await self.call(req)
        self.transaction_id = resp.transaction_id
        self.charging = True
        logger.info(f"  Transaction started: id={self.transaction_id}")
        await self.send_status("Charging")

    async def _do_stop_transaction(self):
        await asyncio.sleep(0.5)
        if self.transaction_id is None:
            return
        req = call.StopTransaction(
            meter_stop=int(self.energy_wh),
            timestamp=datetime.now(timezone.utc).isoformat(),
            transaction_id=self.transaction_id,
        )
        await self.call(req)
        self.charging = False
        self.transaction_id = None
        self.power_w = 0
        logger.info("  Transaction stopped")
        await self.send_status("Available")

    async def _send_meter_values(self):
        req = call.MeterValues(
            connector_id=1,
            meter_value=[{
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sampled_value": [
                    {"measurand": "Power.Active.Import", "value": str(self.power_w), "unit": "W"},
                    {"measurand": "Energy.Active.Import.Register", "value": str(int(self.energy_wh)), "unit": "Wh"},
                ],
            }],
        )
        await self.call(req)
        logger.info(f"  MeterValues: {self.power_w:.0f}W, {self.energy_wh:.0f}Wh")

    async def _meter_loop(self):
        """Send MeterValues every 10s while charging."""
        while True:
            await asyncio.sleep(10)
            if self.charging and self.power_w > 0:
                self.energy_wh += self.power_w * (10 / 3600)
                await self._send_meter_values()


async def main():
    uri = sys.argv[1] if len(sys.argv) > 1 else "ws://192.168.0.202:8887/AcTec001"
    logger.info(f"Connecting to {uri}")

    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        sim = WallboxSimulator("AcTec001", ws)
        logger.info("Connected!")

        # Start message handler in background (must be running to receive responses)
        handler_task = asyncio.create_task(sim.start())

        # Boot and set initial status
        await sim.send_boot()
        await sim.send_status("Available")

        logger.info("Ready — set number.wallbox_power_limit in HA to test")

        # Run meter loop alongside handler
        meter_task = asyncio.create_task(sim._meter_loop())
        try:
            await handler_task
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed")
        finally:
            meter_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
