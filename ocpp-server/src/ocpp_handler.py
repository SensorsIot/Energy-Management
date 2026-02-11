"""
OCPP 1.6j message handler for wallbox communication.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Callable

from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    ChargePointStatus,
    RegistrationStatus,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingRateUnitType,
    MessageTrigger,
)

logger = logging.getLogger(__name__)


class ChargePointHandler(CP):
    """
    OCPP 1.6j ChargePoint handler.

    Handles incoming messages from wallbox and sends commands.
    """

    # 3-phase calibration: (requested_amps, metered_watts_at_grid)
    # Measured 2026-02-11 on AcTec EV-AC22K, grid meter = EBL M-Bus via gPlug.
    # 7A excluded (unreliable DTSU transient during measurement).
    CALIBRATION_3P = [
        (6, 4094), (8, 5137), (9, 5835), (10, 6445),
        (11, 7245), (12, 7852), (13, 8545), (14, 9321), (15, 10007), (16, 10623),
    ]

    def __init__(self, id: str, connection, on_status_change: Optional[Callable] = None):
        super().__init__(id, connection)
        self.on_status_change = on_status_change
        self.current_status = ChargePointStatus.available
        self.current_power_w = 0
        self.session_energy_wh = 0
        self.connector_id = 1
        self.transaction_id: Optional[int] = None
        self._transaction_counter = 0

    # ========== Incoming messages from wallbox ==========

    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor: str, charge_point_model: str, **kwargs):
        """Wallbox connected and sent boot notification."""
        logger.info(f"Wallbox connected: {charge_point_vendor} {charge_point_model}")
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=60,  # Heartbeat interval in seconds
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self):
        """Wallbox heartbeat - keep connection alive."""
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).isoformat()
        )

    @on(Action.status_notification)
    async def on_status_notification(
        self, connector_id: int, error_code: str, status: str, **kwargs
    ):
        """Wallbox status changed."""
        logger.info(f"Status: connector={connector_id}, status={status}, error={error_code}")
        # Only track status from our connector (connector 0 = charge point level, ignore)
        if connector_id == self.connector_id:
            self.current_status = status
            if self.on_status_change:
                self.on_status_change("status", status)
        return call_result.StatusNotification()

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id: int, meter_value: list, **kwargs):
        """Wallbox sent meter values (power, energy, etc.)."""
        for mv in meter_value:
            for sampled in mv.get("sampled_value", []):
                measurand = sampled.get("measurand", "Energy.Active.Import.Register")
                value = float(sampled.get("value", 0))

                if "Power" in measurand:
                    self.current_power_w = value
                    if self.on_status_change:
                        self.on_status_change("power_w", value)
                elif "Energy" in measurand:
                    self.session_energy_wh = value
                    if self.on_status_change:
                        self.on_status_change("energy_wh", value)

        logger.debug(f"MeterValues: power={self.current_power_w}W, energy={self.session_energy_wh}Wh")
        return call_result.MeterValues()

    @on(Action.start_transaction)
    async def on_start_transaction(
        self, connector_id: int, id_tag: str, meter_start: int, timestamp: str, **kwargs
    ):
        """Wallbox started a charging transaction."""
        self._transaction_counter += 1
        self.transaction_id = self._transaction_counter
        logger.info(f"Transaction started: id={self.transaction_id}, connector={connector_id}")
        if self.on_status_change:
            self.on_status_change("transaction", "started")
        return call_result.StartTransaction(
            transaction_id=self.transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on(Action.stop_transaction)
    async def on_stop_transaction(
        self, meter_stop: int, timestamp: str, transaction_id: int, **kwargs
    ):
        """Wallbox stopped a charging transaction."""
        logger.info(f"Transaction stopped: id={transaction_id}, energy={meter_stop}Wh")
        self.transaction_id = None
        if self.on_status_change:
            self.on_status_change("transaction", "stopped")
        return call_result.StopTransaction(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    @on(Action.authorize)
    async def on_authorize(self, id_tag: str):
        """Wallbox requests authorization for a tag."""
        logger.info(f"Authorize request: id_tag={id_tag}")
        # Accept all tags for now
        return call_result.Authorize(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    # ========== Outgoing commands to wallbox ==========

    def _calibrated_current(self, power_w: float, num_phases: int) -> float:
        """Convert target power to request current using calibration data.

        For 3-phase: linear interpolation on measured calibration table.
        For 1-phase or uncalibrated: naive formula power / (230 * phases).
        """
        if power_w <= 0:
            return 0.0
        if num_phases == 3 and self.CALIBRATION_3P:
            table = self.CALIBRATION_3P
            if power_w <= table[0][1]:
                return float(table[0][0])
            if power_w >= table[-1][1]:
                return float(table[-1][0])
            for i in range(len(table) - 1):
                a1, w1 = table[i]
                a2, w2 = table[i + 1]
                if w1 <= power_w <= w2:
                    ratio = (power_w - w1) / (w2 - w1)
                    return a1 + ratio * (a2 - a1)
        return power_w / (230 * num_phases)

    async def set_charging_power(self, power_w: float, num_phases: int = 3):
        """
        Set charging power limit via SetChargingProfile.

        Args:
            power_w: Target power in watts
            num_phases: Number of phases (1 or 3)
        """
        current_a = self._calibrated_current(power_w, num_phases)
        current_a = round(current_a, 1)  # OCPP requires multiple of 0.1
        current_a = max(0, min(current_a, 32))  # Clamp to valid range

        logger.info(f"Setting charging power: {power_w}W ({current_a:.1f}A, {num_phases}-phase)")

        request = call.SetChargingProfile(
            connector_id=self.connector_id,
            cs_charging_profiles={
                "charging_profile_id": 1,
                "stack_level": 0,
                "charging_profile_purpose": ChargingProfilePurposeType.tx_default_profile,
                "charging_profile_kind": ChargingProfileKindType.absolute,
                "charging_schedule": {
                    "charging_rate_unit": ChargingRateUnitType.amps,
                    "charging_schedule_period": [
                        {
                            "start_period": 0,
                            "limit": current_a,
                            "number_phases": num_phases,
                        }
                    ],
                },
            },
        )

        response = await self.call(request)
        logger.info(f"SetChargingProfile response: {response.status}")
        return response.status == "Accepted"

    async def remote_start(self, id_tag: str = "EnergyManager"):
        """Start charging remotely."""
        logger.info("Sending RemoteStartTransaction")
        request = call.RemoteStartTransaction(
            id_tag=id_tag,
            connector_id=self.connector_id,
        )
        response = await self.call(request)
        logger.info(f"RemoteStartTransaction response: {response.status}")
        return response.status == "Accepted"

    async def remote_stop(self):
        """Stop charging remotely."""
        if self.transaction_id is None:
            logger.warning("No active transaction to stop")
            return False

        logger.info(f"Sending RemoteStopTransaction: transaction_id={self.transaction_id}")
        request = call.RemoteStopTransaction(transaction_id=self.transaction_id)
        response = await self.call(request)
        logger.info(f"RemoteStopTransaction response: {response.status}")
        return response.status == "Accepted"

    async def trigger_meter_values(self):
        """Request immediate MeterValues from wallbox."""
        logger.info("Sending TriggerMessage for MeterValues")
        request = call.TriggerMessage(
            requested_message=MessageTrigger.meter_values,
            connector_id=self.connector_id,
        )
        response = await self.call(request)
        logger.info(f"TriggerMessage response: {response.status}")
        return response.status == "Accepted"
