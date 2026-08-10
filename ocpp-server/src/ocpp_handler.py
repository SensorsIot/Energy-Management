"""OCPP 1.6j message handler for wallbox communication."""

import asyncio
import logging
import math
import time
from datetime import datetime, UTC
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
    # Recalibrated 2026-08-02 against the night house baseline. At night the house
    # is stable (280 W median, IQR 245–327, n=7492 five-minute samples over 6
    # months), so with the wallbox the only variable load,
    #   (site load charging) − (site load idle) = true wallbox power,
    # where site load = PV − grid_power − battery_charge_discharge_power. This uses
    # only the grid and battery meters, so it is independent of both the wallbox's
    # own meter and the derived house_load_power (which would be circular).
    # Anchors — two 11 kW charging→idle transitions after 23:00 local, agreeing to
    # 3 W, plus one 4 kW night:
    #   2026-03-20 00:50   raw  4027 W → true  4019 W
    #   2026-04-12 04:25 } raw 11315 W → true 11475 W
    #   2026-05-17 23:30 }
    # The 11 kW point is solid (±0.3 %); the slope/offset split rests on the single
    # 4 kW night, so the low-power end is the weak part of this fit.
    # Prior fits: 1.048 * raw − 286 (2026-07-02, daytime grid match — 0.8 % high at
    # 11 kW and ~2 % low at 4 kW against the night measurement); 0.962115 * raw +
    # 105.6 (2026-03-04 sweep 6–14 A).
    # The correction is 3-phase-calibrated (its anchors are multi-kW 3φ points), so it
    # is only applied when 3 phases are drawing; a single-phase draw would be biased
    # low by the −101 W offset, so 1φ/2φ reports the wallbox's own measured power.
    METER_SCALE = 1.023
    METER_OFFSET = -101.0

    # Demand calibration: W→A divisor so round(mbus_w / DEMAND_DIVISOR) = correct amps.
    # The wallbox applies the amp limit PER PHASE, so the divisor is phase-specific
    # (see _demand_divisor). Both are measured, not derived from each other:
    #   3φ = 637 — midpoint of safe range [612, 662], 2026-03-04 M-Bus sweep.
    #   1φ = 230 — from live single-phase OCPP MeterValues (2026-07-09, ~230 W/A,
    #              linear through origin). NOT 637/3=212: single-phase draws more
    #              per amp than one leg of a 3φ load.
    DEMAND_DIVISOR = 637
    DEMAND_DIVISOR_1P = 230

    # Phase detection from MeterValues: a phase counts as "drawing" when its current
    # is at/above PHASE_ACTIVE_MIN_A, evaluated only once the total draw is meaningful
    # (PHASE_DETECT_MIN_TOTAL_W) so ramp/idle noise cannot misdetect. The 0.5 A floor
    # matches the BL0942 phase-switch safety threshold and still separates a 3φ cable
    # (≥0.6 A/phase at ~400 W total) from a 1φ cable (L1 only).
    PHASE_ACTIVE_MIN_A = 0.5
    PHASE_DETECT_MIN_TOTAL_W = 400

    def __init__(
        self,
        id: str,
        connection,
        on_status_change: Callable | None = None,
        max_current_a: int = 16,
    ) -> None:
        super().__init__(id, connection)
        self.on_status_change = on_status_change
        # Hard ceiling on the per-phase amp limit sent to the wallbox. The
        # wallbox does not enforce the configured maximum itself (a single-phase
        # cable was observed drawing ~19 A from a 21 A profile), so the server
        # caps it — otherwise an over-command over-draws the phase (SEC-07).
        self.max_current_a = max_current_a
        self.current_status = ChargePointStatus.available
        self.current_power_w = 0
        self.session_energy_wh = 0
        # Corrected session energy is accumulated from raw-register increments
        # (see _accumulate_energy): METER_OFFSET is a power, so it only becomes
        # an energy once integrated over the interval it applied to.
        self._raw_energy_wh: float | None = None
        self._raw_energy_time: float | None = None
        # Last (limit_a, num_phases) the wallbox accepted, so an identical
        # profile is never re-written (FSD 3.5.1). Per-connection: a reconnect
        # re-asserts, since the server cannot know what the wallbox retained.
        self._last_profile: tuple[int, int] | None = None
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
        # Phases actually drawing current (detected from MeterValues). Defaults to
        # 3 until the first meaningful draw reveals the connected cable's phases.
        self.active_phases = 3

    # ========== Incoming messages from wallbox ==========

    @on(Action.boot_notification)
    async def on_boot_notification(
        self, charge_point_vendor: str, charge_point_model: str, **kwargs
    ):
        """Wallbox connected and sent boot notification."""
        logger.info(f"Wallbox connected: {charge_point_vendor} {charge_point_model}")
        self.boot_event.set()
        return call_result.BootNotification(
            current_time=datetime.now(UTC).isoformat(),
            interval=60,  # Heartbeat interval in seconds
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self):
        """Wallbox heartbeat - keep connection alive."""
        self.heartbeat_event.set()
        return call_result.Heartbeat(
            current_time=datetime.now(UTC).isoformat()
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
                    f"Ignoring stale transaction_id={txn_id} from MeterValues "
                    f"(status={self.current_status})"
                )

        total_power = 0.0
        has_power_measurand = False
        raw_energy_wh: float | None = None
        phase_current: dict[str, float] = {}  # per-phase current for phase detection
        for mv in meter_value:
            # Reject stale MeterValues based on wallbox timestamp
            mv_timestamp = mv.get("timestamp")
            if mv_timestamp:
                try:
                    mv_time = datetime.fromisoformat(
                        mv_timestamp.replace("Z", "+00:00")
                    )
                    age_s = (
                        datetime.now(UTC) - mv_time
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
                raw_value = sampled.get("value", 0)
                # Validate untrusted wallbox input: drop non-numeric, non-finite,
                # or negative values rather than crashing or corrupting state.
                # (Security SEC-01/SEC-03, CWE-20 input validation.)
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    logger.warning(
                        f"MeterValues: dropped non-numeric {measurand} value {raw_value!r}"
                    )
                    continue
                if not math.isfinite(value):
                    logger.warning(
                        f"MeterValues: dropped non-finite {measurand} value {value}"
                    )
                    continue

                if "Power" in measurand:
                    if value < 0:
                        logger.warning(f"MeterValues: dropped negative power {value}W")
                        continue
                    total_power += value
                    has_power_measurand = True
                elif "Energy" in measurand:
                    if value < 0:
                        logger.warning(f"MeterValues: dropped negative energy {value}Wh")
                        continue
                    raw_energy_wh = value
                elif "Current" in measurand:
                    phase = sampled.get("phase")
                    if phase and value >= 0:
                        phase_current[phase] = value

        # Detect how many phases the connected cable is drawing on. Only trust the
        # count once the total draw is meaningful, so idle/ramp noise never flaps it.
        if has_power_measurand and total_power >= self.PHASE_DETECT_MIN_TOTAL_W and phase_current:
            active = sum(1 for a in phase_current.values() if a >= self.PHASE_ACTIVE_MIN_A)
            if active in (1, 2, 3) and active != self.active_phases:
                logger.info(
                    f"Phase detection: {self.active_phases} → {active} active "
                    f"(per-phase A: {phase_current})"
                )
                self.active_phases = active
                if self.on_status_change:
                    self.on_status_change("phases_active", active)

        total_power = self._correct_meter_power(total_power, self.active_phases)

        # Energy must be corrected on the same scale as power, or the two sensors
        # disagree (they did, for months: power corrected, energy raw).
        if raw_energy_wh is not None:
            self._accumulate_energy(raw_energy_wh, self.active_phases)
            if self.on_status_change:
                self.on_status_change("energy_wh", self.session_energy_wh)

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
        # New session — restart the corrected-energy accumulator. The register
        # reset is also caught in _accumulate_energy, but a wallbox that carries
        # its register across sessions would otherwise leak the previous total.
        self.session_energy_wh = 0.0
        self._raw_energy_wh = None
        self._raw_energy_time = None
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

    def _correct_meter_power(self, raw_w: float, active_phases: int = 3) -> float:
        """Correct OCPP MeterValues power using linear regression.

        corrected = METER_SCALE * raw + METER_OFFSET when 3 phases are drawing.
        The regression is anchored on multi-kW 3φ points, so for a single- (or
        two-) phase draw the −286 W offset would bias the reading low; there the
        wallbox's own measured power is returned unchanged. Returns raw value
        unchanged when not charging (raw <= 0).
        """
        if raw_w <= 0:
            return raw_w
        if active_phases < 3:
            return raw_w
        return self.METER_SCALE * raw_w + self.METER_OFFSET

    def _accumulate_energy(self, raw_wh: float, active_phases: int = 3) -> None:
        """Accumulate corrected session energy from raw-register increments.

        The register is cumulative, so the correction cannot be applied to it
        directly: METER_SCALE is a ratio (safe on any quantity) but METER_OFFSET
        is a *power*, and only becomes an energy once integrated over the interval
        it applied to. So each increment is corrected as

            dE_true = METER_SCALE * dE_raw + METER_OFFSET * dt_hours

        and summed. Correction is applied on the same terms as the power path
        (3 phases only); a 1φ/2φ draw accumulates the raw increment unchanged.

        A register that goes backwards means the wallbox restarted the transaction,
        so the session restarts from zero.
        """
        now = time.monotonic()
        prev_wh, prev_t = self._raw_energy_wh, self._raw_energy_time
        self._raw_energy_wh, self._raw_energy_time = raw_wh, now

        if prev_wh is None or prev_t is None or raw_wh < prev_wh:
            # First reading of a session, or the register reset — restart.
            self.session_energy_wh = 0.0
            return

        d_raw = raw_wh - prev_wh
        if d_raw <= 0:
            return
        if active_phases < 3:
            self.session_energy_wh += d_raw
            return
        dt_h = max(0.0, now - prev_t) / 3600.0
        self.session_energy_wh = max(
            0.0, self.session_energy_wh + self.METER_SCALE * d_raw + self.METER_OFFSET * dt_h
        )

    def _demand_divisor(self, num_phases: int) -> int:
        """Watts→amps divisor for the requested phase count.

        Each phase count has its own measured divisor (3φ → 637, 1φ → 230); a
        2φ draw interpolates between them (rare / transitional).
        """
        if num_phases >= 3:
            return self.DEMAND_DIVISOR
        if num_phases <= 1:
            return self.DEMAND_DIVISOR_1P
        return round((self.DEMAND_DIVISOR_1P + self.DEMAND_DIVISOR) / 2)

    async def set_charging_power(
        self, power_w: float, num_phases: int = 3, force: bool = False
    ):
        """Set charging power limit via SetChargingProfile.

        Converts M-Bus watts to integer amps using the phase-aware calibrated
        divisor, then sends via OCPP 1.6 chargingRateUnit=A.

        Every profile is a write to the wallbox's non-volatile store, so an
        unchanged command is not sent (FSD 3.5.1). The comparison is on the
        integer amps and phase count actually commanded, not the requested
        watts: the wallbox floors watts to whole amps, so 4354 W and 4400 W are
        both 7 A and the second would be a duplicate write.

        Args:
            power_w: Target power in watts (M-Bus scale)
            num_phases: Number of phases (1 or 3)
            force: Re-send even if the wallbox already holds this profile. The
                SuspendedEVSE recovery (FSD 5.4) needs this — nudging a stuck
                wallbox means re-sending the same profile deliberately.

        """
        limit_w = max(0, power_w)
        divisor = self._demand_divisor(num_phases)
        limit_a = round(limit_w / divisor) if limit_w > 0 else 0
        # Hard cap at the configured maximum — the wallbox does not enforce it.
        capped_a = min(limit_a, self.max_current_a)
        if capped_a != limit_a:
            logger.info(
                f"Clamping {limit_a}A → {capped_a}A (max_current_a={self.max_current_a})"
            )
        limit_a = capped_a

        profile = (limit_a, num_phases)
        if not force and profile == self._last_profile:
            logger.debug(
                f"Wallbox already at {limit_a}A ({num_phases}-phase) — no profile sent"
            )
            return True

        logger.info(
            f"Setting charging power: {limit_w:.0f}W → {limit_a}A "
            f"({num_phases}-phase, ÷{divisor})"
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
        accepted = response.status == "Accepted"
        if accepted:
            self._last_profile = profile
        return accepted

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

    async def change_configuration(self, key: str, value: str) -> str:
        """Set an OCPP configuration key on the wallbox.

        Returns the raw status string (`Accepted`, `Rejected`, `RebootRequired`,
        or `NotSupported`). The caller decides how to react — `Accepted` and
        `RebootRequired` both mean the value was taken.
        """
        logger.info(f"Sending ChangeConfiguration: {key}={value}")
        response = await self.call(call.ChangeConfiguration(key=key, value=value))
        logger.info(f"ChangeConfiguration {key}={value} response: {response.status}")
        return response.status

    async def get_configuration(self, keys: list[str] | None = None) -> dict:
        """Read OCPP configuration keys from the wallbox.

        Returns a ``{key: value}`` dict for the keys the wallbox knows about
        (keys it does not support are reported in ``unknown_key`` and omitted
        from the result). Used to confirm feature support and to sync HA state
        to the wallbox's actual configuration — the wallbox is the source of
        truth.
        """
        request = call.GetConfiguration(key=keys) if keys else call.GetConfiguration()
        response = await self.call(request)
        result = {
            item.get("key"): item.get("value")
            for item in (getattr(response, "configuration_key", None) or [])
        }
        unknown = getattr(response, "unknown_key", None) or []
        logger.info(
            f"GetConfiguration: {result}"
            + (f", unknown={unknown}" if unknown else "")
        )
        return result

    async def unlock_connector(self) -> str:
        """Release the socket lock now via UnlockConnector.

        Returns the raw status string (`Unlocked`, `UnlockFailed`, or
        `NotSupported`). Momentary action — frees a stuck cable — distinct from
        the persistent `UnlockConnectorOnEVSideDisconnect` policy.
        """
        logger.info("Sending UnlockConnector")
        response = await self.call(
            call.UnlockConnector(connector_id=self.connector_id)
        )
        logger.info(f"UnlockConnector response: {response.status}")
        return response.status
