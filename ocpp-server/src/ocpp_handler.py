"""OCPP 1.6j message handler for wallbox communication."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from collections.abc import Callable

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
    """OCPP 1.6j ChargePoint handler.

    Handles incoming messages from wallbox and sends commands.
    """

    # Linear meter correction: corrected = METER_SCALE * raw + METER_OFFSET
    # Regression on 2026-03-04 sweep (6–14A): OCPP MeterValues vs M-Bus.
    # Max residual ≈ 33W (<0.5%).
    METER_SCALE = 0.962115
    METER_OFFSET = 105.6

    # Demand calibration: W→A divisor so round(mbus_w / DEMAND_DIVISOR) = correct amps.
    # Midpoint of safe range [612, 662] from 2026-03-04 M-Bus calibration sweep.
    DEMAND_DIVISOR = 637

    def __init__(
        self, id: str, connection, on_status_change: Callable | None = None
    ) -> None:
        super().__init__(id, connection)
        self.on_status_change = on_status_change
        self.current_status = ChargePointStatus.available
        self.current_power_w = 0
        self.session_energy_wh = 0
        self.connector_id = 1
        self.transaction_id: int | None = None
        self._transaction_counter = 0
        self.boot_event = asyncio.Event()
        self.status_event = asyncio.Event()
        self.heartbeat_event = asyncio.Event()
        self.transaction_started_event = asyncio.Event()
        self.last_meter_values_time: float = (
            0  # monotonic timestamp of last MeterValues
        )
        self.meter_values_event = asyncio.Event()

    # ========== Incoming messages from wallbox ==========

    @on(Action.boot_notification)
    async def on_boot_notification(
        self, charge_point_vendor: str, charge_point_model: str, **kwargs
    ):
        """Wallbox connected and sent boot notification."""
        logger.info(f"Wallbox connected: {charge_point_vendor} {charge_point_model}")
        self.boot_event.set()
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=60,  # Heartbeat interval in seconds
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self):
        """Wallbox heartbeat - keep connection alive."""
        self.heartbeat_event.set()
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).isoformat()
        )

    @on(Action.status_notification)
    async def on_status_notification(
        self, connector_id: int, error_code: str, status: str, **kwargs
    ):
        """Wallbox status changed."""
        logger.info(
            f"Status: connector={connector_id}, status={status}, error={error_code}"
        )
        # Only track status from our connector (connector 0 = charge point level, ignore)
        if connector_id == self.connector_id:
            self.current_status = status
            self.status_event.set()
            if self.on_status_change:
                self.on_status_change("status", status)
            # Zero power when wallbox is not actively charging
            if status != "Charging" and self.current_power_w > 0:
                logger.info(
                    f"Status {status}: resetting power from {self.current_power_w}W to 0W"
                )
                self.current_power_w = 0
                if self.on_status_change:
                    self.on_status_change("power_w", 0)
        return call_result.StatusNotification()

    # Maximum age (seconds) for MeterValues timestamps.  The Actec wallbox
    # replays its internal meter-log queue after server restarts or long idle
    # periods, delivering readings with timestamps from hours or days ago.
    # Accepting those stale readings creates impossible energy-counter jumps
    # (e.g. 70 → 6980 Wh in one second) that corrupt daily statistics.
    METER_VALUES_MAX_AGE_S = 300  # 5 minutes

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id: int, meter_value: list, **kwargs):
        """Wallbox sent meter values (power, energy, etc.)."""
        # Recover transaction_id from MeterValues (e.g. after server restart)
        # Recover if wallbox status indicates a car is connected — SuspendedEVSE
        # and SuspendedEV also have active transactions
        ACTIVE_STATUSES = {
            "Charging", "SuspendedEV", "SuspendedEVSE",
            ChargePointStatus.charging,
            ChargePointStatus.suspended_ev,
            ChargePointStatus.suspended_evse,
        }
        txn_id = kwargs.get("transaction_id")
        if txn_id is not None and self.transaction_id is None:
            if self.current_status in ACTIVE_STATUSES:
                self.transaction_id = txn_id
                self.transaction_started_event.set()
                logger.info(f"Recovered transaction_id={txn_id} from MeterValues")
            else:
                logger.info(
                    f"Ignoring stale transaction_id={txn_id} from MeterValues (status={self.current_status})"
                )

        total_power = 0.0
        has_power_measurand = False
        for mv in meter_value:
            # Reject stale MeterValues based on wallbox timestamp
            mv_timestamp = mv.get("timestamp")
            if mv_timestamp:
                try:
                    mv_time = datetime.fromisoformat(
                        mv_timestamp.replace("Z", "+00:00")
                    )
                    age_s = (
                        datetime.now(timezone.utc) - mv_time
                    ).total_seconds()
                    if age_s > self.METER_VALUES_MAX_AGE_S:
                        logger.warning(
                            f"STALE MeterValues dropped: "
                            f"age={age_s:.0f}s, timestamp={mv_timestamp}, "
                            f"status={self.current_status}, "
                            f"txn={self.transaction_id} "
                            f"— triggering fresh reading"
                        )
                        asyncio.ensure_future(self.trigger_meter_values())
                        continue
                except (ValueError, TypeError):
                    pass  # unparseable timestamp — process normally

            for sampled in mv.get("sampled_value", []):
                measurand = sampled.get("measurand", "Energy.Active.Import.Register")
                value = float(sampled.get("value", 0))

                if "Power" in measurand:
                    total_power += value
                    has_power_measurand = True
                elif "Energy" in measurand:
                    self.session_energy_wh = value
                    if self.on_status_change:
                        self.on_status_change("energy_wh", value)

        total_power = self._correct_meter_power(total_power)

        self.last_meter_values_time = time.monotonic()
        self.meter_values_event.set()

        # Skip power update for energy-only messages (e.g. Sample.Clock at
        # 15-minute boundaries) — they contain no Power measurand so
        # total_power=0 would incorrectly zero the reported power.
        if not has_power_measurand:
            logger.debug(
                f"MeterValues: energy-only (no Power measurand), "
                f"keeping power={self.current_power_w}W"
            )
            return call_result.MeterValues()

        # Only accept power readings during an active transaction
        # Wallbox may return stale cached MeterValues when triggered outside a transaction
        if self.transaction_id is not None:
            if total_power > 0 or self.current_power_w > 0:
                self.current_power_w = total_power
                if self.on_status_change:
                    self.on_status_change("power_w", total_power)
        elif total_power > 0:
            logger.info(
                f"Ignoring MeterValues power {total_power}W — no active transaction"
            )

        logger.debug(
            f"MeterValues: power={self.current_power_w}W, energy={self.session_energy_wh}Wh"
        )
        return call_result.MeterValues()

    @on(Action.start_transaction)
    async def on_start_transaction(
        self, connector_id: int, id_tag: str, meter_start: int, timestamp: str, **kwargs
    ):
        """Wallbox started a charging transaction."""
        self._transaction_counter += 1
        self.transaction_id = self._transaction_counter
        self.transaction_started_event.set()
        logger.info(
            f"Transaction started: id={self.transaction_id}, connector={connector_id}"
        )
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
        # Reset power to 0 — no more MeterValues will arrive after transaction ends
        if self.current_power_w > 0:
            logger.info(
                f"Transaction ended: resetting power from {self.current_power_w}W to 0W"
            )
            self.current_power_w = 0
            if self.on_status_change:
                self.on_status_change("power_w", 0)
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

    def _correct_meter_power(self, raw_w: float) -> float:
        """Correct OCPP MeterValues power using linear regression.

        corrected = METER_SCALE * raw + METER_OFFSET
        Returns raw value unchanged when not charging (raw <= 0).
        """
        if raw_w <= 0:
            return raw_w
        return self.METER_SCALE * raw_w + self.METER_OFFSET

    async def set_charging_power(self, power_w: float, num_phases: int = 3):
        """Set charging power limit via SetChargingProfile.

        Converts M-Bus watts to integer amps using calibrated divisor,
        then sends via OCPP 1.6 chargingRateUnit=A.

        Args:
            power_w: Target power in watts (M-Bus scale)
            num_phases: Number of phases (1 or 3)

        """
        limit_w = max(0, power_w)
        limit_a = round(limit_w / self.DEMAND_DIVISOR) if limit_w > 0 else 0

        logger.info(
            f"Setting charging power: {limit_w:.0f}W → {limit_a}A "
            f"({num_phases}-phase)"
        )

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
                            "limit": limit_a,
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
