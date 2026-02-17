#!/usr/bin/env python3
"""
EnergyManager Add-on for Home Assistant.

Optimizes battery usage based on PV and load forecasts.
"""

__version__ = "1.6.33"

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from src.forecast_reader import ForecastReader
from src.ha_client import HAClient
from src.battery_optimizer import BatteryOptimizer
from src.appliance_signal import calculate_appliance_signal
from src.ev_state_machine import EVStateMachine, EVInputs, EVState
from src.influxdb_writer import SimulationWriter
from src.integration_observer import CycleSnapshot, IntegrationObserver
from src.notifications import init_telegram, notify_error
from src.sanity import validate_power_readings
from src.smart_car import CHARGER_STATE_LABELS, HelloSmartClient, get_ev_status

# Swiss timezone for display
SWISS_TZ = ZoneInfo("Europe/Zurich")

# Adaptive SOC polling: 1 minute during charging, hourly otherwise
CAR_SOC_CHARGING_INTERVAL_S = 60


def swiss_time(dt: datetime) -> str:
    """Format datetime in Swiss timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SWISS_TZ).strftime("%H:%M")


def swiss_datetime(dt: datetime) -> str:
    """Format datetime in Swiss timezone with date."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SWISS_TZ).strftime("%Y-%m-%d %H:%M")


# Configure logging with Swiss timezone
class SwissFormatter(logging.Formatter):
    """Formatter that uses Swiss timezone."""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=SWISS_TZ)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Apply Swiss formatter to root logger
for handler in logging.root.handlers:
    handler.setFormatter(SwissFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))

logger = logging.getLogger("energymanager")


class EnergyManager:
    """Main EnergyManager application."""

    def __init__(self, options: dict):
        self.options = options

        # Initialize InfluxDB components
        influx_opts = options.get("influxdb", {})
        influx_token = influx_opts.get("token", "")

        self.forecast_reader = ForecastReader(
            host=influx_opts.get("host", "192.168.0.203"),
            port=influx_opts.get("port", 8087),
            token=influx_token,
            org=influx_opts.get("org", "energymanagement"),
            pv_bucket=influx_opts.get("pv_bucket", "pv_forecast"),
            load_bucket=influx_opts.get("load_bucket", "load_forecast"),
        )

        # InfluxDB client for writing results
        self.influx_url = f"http://{influx_opts.get('host', '192.168.0.203')}:{influx_opts.get('port', 8087)}"
        self.influx_token = influx_token
        self.influx_org = influx_opts.get("org", "energymanagement")
        self.output_bucket = influx_opts.get("output_bucket", "energy_manager")
        self.influx_client = None
        self.write_api = None

        # Home Assistant client
        ha_opts = options.get("home_assistant", {})
        self.ha_client = HAClient(
            url=ha_opts.get("url", "http://supervisor/core"),
            token=ha_opts.get("token"),
        )

        # Battery optimizer
        battery_opts = options.get("battery", {})
        tariff_opts = options.get("tariff", {})

        self.optimizer = BatteryOptimizer(
            capacity_wh=battery_opts.get("capacity_kwh", 10.0) * 1000,
            min_soc_percent=battery_opts.get("reserve_percent", 10),
            charge_efficiency=battery_opts.get("charge_efficiency", 0.95),
            discharge_efficiency=battery_opts.get("discharge_efficiency", 0.95),
            max_charge_w=battery_opts.get("max_charge_w", 5000),
            max_discharge_w=battery_opts.get("max_discharge_w", 5000),
            weekday_cheap_start=tariff_opts.get("weekday_cheap_start", "21:00"),
            weekday_cheap_end=tariff_opts.get("weekday_cheap_end", "06:00"),
            weekend_all_day_cheap=tariff_opts.get("weekend_all_day_cheap", True),
            holidays=tariff_opts.get("holidays", []),
        )

        self.soc_entity = battery_opts.get(
            "soc_entity", "sensor.battery_state_of_capacity"
        )
        self.discharge_control_entity = battery_opts.get(
            "discharge_control_entity", "number.battery_maximum_discharging_power"
        )

        # Appliance signal config
        appliance_opts = options.get("appliances", {})
        self.appliance_power_w = appliance_opts.get("power_w", 2500)
        self.appliance_energy_wh = appliance_opts.get("energy_wh", 1500)

        # Battery parameters for appliance signal
        self.capacity_wh = battery_opts.get("capacity_kwh", 10.0) * 1000
        self.reserve_percent = battery_opts.get("reserve_percent", 10)

        # Sensor entities for appliance signal calculation
        sensors_opts = options.get("sensors", {})
        self.pv_power_entity = sensors_opts.get("pv_power", "sensor.solar_pv_total_ac_power")
        self.load_power_entity = sensors_opts.get("load_power", "sensor.load_power")
        self.appliance_signal_entity = sensors_opts.get("appliance_signal", "sensor.appliance_signal")

        # Scheduler
        schedule_opts = options.get("schedule", {})
        self.update_interval = schedule_opts.get("update_interval_minutes", 15)
        self.scheduler = BackgroundScheduler(timezone="UTC")

        # Track last discharge state to only send signal on change
        self.last_discharge_allowed = None
        # Two independent discharge-block reasons (OR logic)
        self._discharge_blocked_by_protection = False
        self._discharge_blocked_by_ev = False

        # SimulationWriter for FSD 4.2.3 output
        self.simulation_writer = SimulationWriter(
            host=influx_opts.get("host", "192.168.0.203"),
            port=influx_opts.get("port", 8087),
            token=influx_token,
            org=influx_opts.get("org", "energymanagement"),
            bucket=self.output_bucket,
        )

        # Initialize Telegram notifications
        telegram_opts = options.get("telegram", {})
        init_telegram(
            bot_token=telegram_opts.get("bot_token", ""),
            chat_id=telegram_opts.get("chat_id", ""),
        )

        # EV charging config (FSD 4.5)
        ev_opts = options.get("ev_charging", {})
        self.ev_charging_enabled = ev_opts.get("enabled", False)
        self.ev_min_power_w = ev_opts.get("min_power_w", 1400)
        self.ev_max_power_w = ev_opts.get("max_power_w", 11000)
        self.mbus_grid_power_entity = sensors_opts.get("mbus_grid_power", "sensor.grid_power")
        self.dtsu_grid_power_entity = sensors_opts.get("dtsu_grid_power", "sensor.power_meter_active_power")
        self.wallbox_power_entity = ev_opts.get("wallbox_power_entity", "sensor.wallbox_power")
        self.wallbox_connected_entity = ev_opts.get("wallbox_connected_entity", "binary_sensor.wallbox_connected")
        self.wallbox_power_limit_entity = ev_opts.get("wallbox_power_limit_entity", "number.wallbox_power_limit")
        self.ev_target_power_entity = ev_opts.get("ev_target_power_entity", "sensor.ev_target_power")
        self._last_ev_power_limit = None
        self._ev_sm = EVStateMachine()

        # Charging mode config (FSD 4.5.4)
        self.ev_power_limit_entity = ev_opts.get(
            "power_limit_entity", "input_number.ev_power_limit"
        )
        self.ev_charging_mode_entity = ev_opts.get(
            "mode_entity", "input_select.ev_charging_mode"
        )
        self.ev_charge_status_entity = ev_opts.get(
            "charge_status_entity", "sensor.ev_charge_status"
        )
        self.ev_wallbox_status_entity = ev_opts.get(
            "wallbox_status_entity", "sensor.wallbox_status"
        )
        self.ev_auto_reset_timeout_min = ev_opts.get("auto_reset_timeout_min", 5)
        self.ev_battery_protection_soc = ev_opts.get("battery_protection_soc", 80)
        self._ev_idle_since: datetime | None = None
        self._observer = IntegrationObserver() if self.ev_charging_enabled else None
        self._last_mode_error_notified: str | None = None
        self._battery_reaches_target: bool = False
        self._battery_min_soc_forecast: float = 0.0

        # Smart car config (FSD 4.5 Step 2 — hourly SOC readback)
        smart_opts = options.get("smart_car", {})
        self.smart_car_enabled = smart_opts.get("enabled", False)
        self.smart_car_user = smart_opts.get("user", "")
        self.smart_car_password = smart_opts.get("password", "")
        self.smart_car_vin = smart_opts.get("vin", "")
        self.smart_car_soc_entity = smart_opts.get(
            "soc_entity", "sensor.smart_battery"
        )
        self._smart_car_client: HelloSmartClient | None = None
        self._last_wallbox_status: str | None = None
        self._last_ev_charging_mode: str | None = None
        self._last_car_soc_poll: float = 0.0

    def connect(self):
        """Connect to services."""
        logger.info("Connecting to services...")
        self.forecast_reader.connect()
        self.influx_client = InfluxDBClient(
            url=self.influx_url,
            token=self.influx_token,
            org=self.influx_org
        )
        self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
        self.simulation_writer.connect()
        logger.info("Connected successfully")

    def close(self):
        """Close connections."""
        self.forecast_reader.close()
        if self.influx_client:
            self.influx_client.close()
        self.simulation_writer.close()

    def get_current_soc(self) -> float:
        """Get current battery SOC from HA or InfluxDB."""
        # Try Home Assistant first
        current_soc = self.ha_client.get_battery_soc(self.soc_entity)

        if current_soc is None:
            # Fallback: try to get SOC from InfluxDB
            logger.info("HA SOC not available, trying InfluxDB...")
            influx_opts = self.options.get("influxdb", {})
            current_soc = self.forecast_reader.get_current_soc(
                bucket=influx_opts.get("soc_bucket", "HuaweiNew"),
                measurement=influx_opts.get("soc_measurement", "Energy"),
                field=influx_opts.get("soc_field", "BATT_Level"),
            )

        if current_soc is None:
            logger.warning("Could not get current SOC, using 50%")
            current_soc = 50.0

        return current_soc

    def write_energy_balance(self, forecast):
        """Write energy balance to InfluxDB for visualization.

        Args:
            forecast: DataFrame with pv_energy_wh, load_energy_wh, net_energy_wh columns
        """
        if forecast.empty:
            return

        # Write energy balance data from forecast DataFrame
        # Calculate cumulative as running sum of net_energy_wh
        points = []
        cumulative_wh = 0.0
        for t in forecast.index:
            ts = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            row = forecast.loc[t]

            net_wh = float(row.get("net_energy_wh", 0))
            cumulative_wh += net_wh

            points.append(
                Point("energy_balance")
                .field("cumulative_wh", cumulative_wh)
                .time(ts, WritePrecision.S)
            )

        self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=points)
        logger.info(f"Written {len(points)} energy balance points")

    def write_decision(self, decision, current_soc: float):
        """Write discharge decision to InfluxDB."""
        now = datetime.now(timezone.utc)

        point = (
            Point("discharge_decision")
            .field("allowed", decision.discharge_allowed)
            .field("reason", decision.reason)
            .field("min_soc_percent", float(decision.min_soc_percent))
            .field("current_soc", float(current_soc))
            .time(now, WritePrecision.S)
        )

        self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=point)

    def control_battery(self, discharge_allowed: bool):
        """Control battery discharge via Home Assistant - reads actual state and adjusts."""
        if not self.ha_client.token:
            logger.warning("No HA token, cannot control battery")
            return

        # Determine target value: max_discharge_w if allowed, 0W if blocked
        battery_opts = self.options.get("battery", {})
        max_discharge_w = battery_opts.get("max_discharge_w", 5000)
        target_value = max_discharge_w if discharge_allowed else 0
        action = "enable" if discharge_allowed else "block"

        # Read current actual value from Home Assistant
        current_value = self.ha_client.get_battery_discharge_power(
            self.discharge_control_entity
        )

        if current_value is None:
            logger.warning(f"Could not read current discharge power from {self.discharge_control_entity}")
            # Continue anyway - we should try to set the value
        else:
            logger.info(f"Current discharge power: {current_value}W, target: {target_value}W")

            # Check if already at target value (with small tolerance for float comparison)
            if abs(current_value - target_value) < 1:
                logger.debug(f"Discharge power already at target ({target_value}W), no change needed")
                self.last_discharge_allowed = discharge_allowed
                return

        # Set the new value
        success, error_msg = self.ha_client.set_battery_discharge_power(
            self.discharge_control_entity,
            target_value,
            max_retries=5,
        )

        if not success:
            # All retries failed - send Telegram notification
            logger.error(f"Failed to {action} battery discharge after 5 attempts")
            notify_error(
                title="Battery Control Failed",
                message=(
                    f"Failed to {action} battery discharge after 5 attempts.\n\n"
                    f"Entity: {self.discharge_control_entity}\n"
                    f"Target value: {target_value}W\n"
                    f"Error: {error_msg}\n\n"
                    f"The battery may not be in the expected state!"
                ),
            )
            return

        # Verify the change took effect
        time.sleep(1)  # Give HA time to process
        verified_value = self.ha_client.get_battery_discharge_power(
            self.discharge_control_entity
        )

        if verified_value is not None and abs(verified_value - target_value) < 1:
            self.last_discharge_allowed = discharge_allowed
            logger.info(f"Battery control verified: {self.discharge_control_entity} = {verified_value}W")
        elif verified_value is not None:
            logger.warning(
                f"Battery control verification mismatch: set {target_value}W but read {verified_value}W"
            )
            notify_error(
                title="Battery Control Verification Failed",
                message=(
                    f"Set discharge power to {target_value}W but verification read {verified_value}W.\n\n"
                    f"Entity: {self.discharge_control_entity}\n"
                    f"The battery may not be in the expected state!"
                ),
            )
        else:
            # Could not verify but set succeeded
            self.last_discharge_allowed = discharge_allowed
            logger.info(f"Battery control set: {self.discharge_control_entity} = {target_value}W (unverified)")

    def _update_discharge_control(self):
        """Combine both discharge-block flags and apply if changed."""
        discharge_allowed = not (
            self._discharge_blocked_by_protection or self._discharge_blocked_by_ev
        )
        if discharge_allowed != self.last_discharge_allowed:
            reason = []
            if self._discharge_blocked_by_protection:
                reason.append("battery protection")
            if self._discharge_blocked_by_ev:
                reason.append("EV charging")
            logger.info(
                f"Discharge {'allowed' if discharge_allowed else 'blocked'}"
                f"{' by ' + ' + '.join(reason) if reason else ''}"
            )
            self.control_battery(discharge_allowed)

    def run_optimization(self):
        """Run battery optimization cycle."""
        logger.info("=" * 50)
        logger.info("Running battery optimization...")

        try:
            # Get current SOC
            current_soc = self.get_current_soc()
            logger.info(f"DEBUG: Current battery SOC from get_current_soc(): {current_soc:.1f}%")

            # Get forecast
            now = datetime.now(timezone.utc)
            start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)

            # Get tariff periods to determine forecast end
            tariff = self.optimizer.get_tariff_periods(now)
            # Always fetch at least until tomorrow 21:00 for visualization
            tomorrow_target = (now + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
            end = max(tariff.target + timedelta(hours=1), tomorrow_target)

            logger.info(f"Fetching forecasts from {swiss_datetime(start)} to {swiss_datetime(end)}")
            logger.info(f"Tariff: cheap={'Yes' if tariff.is_cheap_now else 'No'}, "
                       f"cheap_end={swiss_datetime(tariff.cheap_end)}, "
                       f"target={swiss_datetime(tariff.target)}")

            forecast = self.forecast_reader.get_combined_forecast(
                start=start,
                end=end,
                percentile="p50",
            )

            if forecast.empty:
                logger.error("No forecast data available")
                return

            logger.info(f"Got {len(forecast)} forecast periods")
            logger.info(f"DEBUG: Forecast first timestamp: {forecast.index[0]}")
            logger.info(f"DEBUG: Forecast last timestamp: {forecast.index[-1]}")

            # Calculate discharge decision
            decision, sim_no_strategy, sim_with_strategy = self.optimizer.calculate_decision(
                soc_percent=current_soc,
                forecast=forecast,
                now=now,
            )

            # Debug: log first few simulation points
            if not sim_no_strategy.empty:
                logger.info(f"DEBUG: Simulation first timestamp: {sim_no_strategy.index[0]}")
                logger.info(f"DEBUG: Simulation first SOC: {sim_no_strategy['soc_percent'].iloc[0]:.1f}%")

            # Log decision
            logger.info(f"Decision: discharge_allowed={decision.discharge_allowed}, "
                       f"min_soc={decision.min_soc_percent:.0f}%")
            logger.info(f"Reason: {decision.reason}")

            # Write results to InfluxDB
            # Write both scenarios for visualization:
            # - with_strategy: what will happen (discharge blocked during cheap hours)
            # - without_strategy: what would happen without blocking (shows why we block)
            self.simulation_writer.write_soc_forecast(sim_with_strategy, scenario="with_strategy")
            self.simulation_writer.write_soc_forecast(sim_no_strategy, scenario="without_strategy")
            # Write forecast snapshot for accuracy tracking
            # Only overwrites from NOW onwards - earlier points preserved for comparison with actual SOC
            self.simulation_writer.write_forecast_snapshot(sim_with_strategy)
            # Write energy balance for cumulative visualization
            self.write_energy_balance(forecast)
            self.write_decision(decision, current_soc)

            # Control battery (protection flag — combined with EV flag)
            self._discharge_blocked_by_protection = not decision.discharge_allowed
            self._update_discharge_control()

            # Calculate appliance signal using full simulation
            # (checks if battery has enough energy to run appliance without grid import)
            self.calculate_appliance_signal(current_soc, sim_no_strategy)

        except Exception as e:
            logger.error(f"Optimization failed: {e}", exc_info=True)

    def calculate_appliance_signal(self, current_soc: float, simulation: pd.DataFrame):
        """Calculate and output appliance signal to Home Assistant."""
        try:
            # Get current PV and load from HA
            current_pv = self.ha_client.get_sensor_value(self.pv_power_entity)
            current_load = self.ha_client.get_sensor_value(self.load_power_entity)

            # Log sensor values for debugging
            logger.debug(f"Appliance signal sensors: PV={current_pv}W, Load={current_load}W")

            if current_pv is None or current_load is None:
                logger.warning(f"Sensor read failed: PV={self.pv_power_entity}={current_pv}, "
                              f"Load={self.load_power_entity}={current_load}")
                current_pv = current_pv or 0
                current_load = current_load or 0

            # Calculate signal using simulation (which has efficiency applied)
            signal = calculate_appliance_signal(
                current_pv_w=current_pv,
                current_load_w=current_load,
                simulation=simulation,
                appliance_power_w=self.appliance_power_w,
                appliance_energy_wh=self.appliance_energy_wh,
                capacity_wh=self.capacity_wh,
                reserve_percent=self.reserve_percent,
            )

            logger.info(f"Appliance signal: {signal.signal} - {signal.reason}")

            # Output to Home Assistant (using configurable entity)
            self.ha_client.set_sensor_state(
                self.appliance_signal_entity,
                signal.signal,
                attributes={
                    "friendly_name": "Appliance Signal",
                    "reason": signal.reason,
                    "excess_power_w": signal.excess_power_w,
                    "final_soc_percent": signal.final_soc_percent,
                    "icon": "mdi:washing-machine",
                },
            )

            # Write to InfluxDB
            point = (
                Point("appliance_signal")
                .field("signal", signal.signal)
                .field("reason", signal.reason)
                .field("excess_power_w", float(signal.excess_power_w))
                .field("final_soc_percent", float(signal.final_soc_percent))
                .time(datetime.now(timezone.utc), WritePrecision.S)
            )
            self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=point)

        except Exception as e:
            logger.error(f"Failed to calculate appliance signal: {e}")


    def _read_grid_power(self) -> float:
        """Read grid power, preferring M-Bus smart meter if fresh (<20s)."""
        state = self.ha_client.get_state(self.mbus_grid_power_entity)
        if state:
            try:
                updated = datetime.fromisoformat(state["last_updated"])
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if age < 20:
                    return float(state["state"])
                logger.debug(f"M-Bus stale ({age:.0f}s), falling back to DTSU")
            except (ValueError, KeyError):
                pass
        return self.ha_client.get_sensor_value(self.dtsu_grid_power_entity) or 0

    def check_battery_protection(self) -> tuple[bool, float]:
        """Check if battery SOC at cheap tariff start meets protection target (FSD 4.5.6).

        Queries the SOC forecast from InfluxDB (written by run_optimization)
        and checks the predicted SOC at the start of the next cheap tariff
        window (21:00 on weekdays). EV charging is only allowed if the
        battery will have >= 80% SOC at that time.

        Returns:
            (reaches_target, soc_at_cheap_start) tuple
        """
        try:
            now = datetime.now(timezone.utc)
            tariff = self.optimizer.get_tariff_periods(now)
            query_api = self.influx_client.query_api()

            # Query SOC at cheap tariff start (narrow window around 21:00)
            window_start = (tariff.cheap_start - timedelta(minutes=15)).isoformat()
            window_stop = (tariff.cheap_start + timedelta(minutes=15)).isoformat()

            query = f'''
            from(bucket: "{self.output_bucket}")
              |> range(start: {window_start}, stop: {window_stop})
              |> filter(fn: (r) => r._measurement == "soc_forecast")
              |> filter(fn: (r) => r.scenario == "with_strategy")
              |> filter(fn: (r) => r._field == "soc_percent")
              |> last()
            '''

            result = query_api.query(query)
            if result and result[0].records:
                soc_at_target = result[0].records[0].get_value()
                reaches_target = soc_at_target >= self.ev_battery_protection_soc
                logger.info(
                    f"Battery protection: forecast SOC at {swiss_time(tariff.cheap_start)}="
                    f"{soc_at_target:.0f}% "
                    f"(target={self.ev_battery_protection_soc}%) → "
                    f"{'EV allowed' if reaches_target else 'EV blocked'}"
                )
                return reaches_target, soc_at_target
            else:
                logger.warning("No SOC forecast data — blocking EV as precaution")
                return False, 0.0

        except Exception as e:
            logger.error(f"Battery protection check failed: {e}")
            return False, 0.0

    def control_ev_charging(self):
        """Control EV charging via state machine (FSD 4.5)."""
        if not self.ev_charging_enabled:
            return

        try:
            # Check wallbox connectivity — skip everything if entity doesn't exist
            wb_state = self.ha_client.get_state(self.wallbox_connected_entity)
            if wb_state is None:
                # Entity doesn't exist (OCPP server not running)
                return
            wb_connected = wb_state.get("state") == "on"

            if not wb_connected:
                logger.debug("Wallbox not connected, skipping EV control")
                return

            # Adaptive SOC polling: on connect, on mode change, every 1 min while charging
            wb_status_state = self.ha_client.get_state(self.ev_wallbox_status_entity)
            wb_status = wb_status_state.get("state", "Unknown") if wb_status_state else "Unknown"

            if self.smart_car_enabled:
                ev_mode_poll = self.ha_client.get_input_select(self.ev_charging_mode_entity)
                now_mono = time.monotonic()

                # Charging mode changed — get fresh SOC before deciding
                if ev_mode_poll != self._last_ev_charging_mode and self._last_ev_charging_mode is not None:
                    logger.info(f"Smart car: mode changed ({self._last_ev_charging_mode} → {ev_mode_poll}), polling SOC")
                    self.update_car_soc()
                    self._last_car_soc_poll = now_mono

                # Car just connected (transition to Preparing from disconnected state)
                elif (
                    wb_status == "Preparing"
                    and self._last_wallbox_status
                    not in ("Preparing", "Charging", "SuspendedEV", "SuspendedEVSE", "Finishing")
                ):
                    logger.info("Smart car: car connected, polling SOC")
                    self.update_car_soc()
                    self._last_car_soc_poll = now_mono

                # Every 1 min while charging
                elif (
                    wb_status == "Charging"
                    and (now_mono - self._last_car_soc_poll) >= CAR_SOC_CHARGING_INTERVAL_S
                ):
                    logger.info("Smart car: charging poll (1-min interval)")
                    self.update_car_soc()
                    self._last_car_soc_poll = now_mono

                self._last_wallbox_status = wb_status
                self._last_ev_charging_mode = ev_mode_poll

            # Read wallbox power
            wallbox_power = self.ha_client.get_sensor_value(self.wallbox_power_entity) or 0.0

            # Dynamic min/max from OCPP server (falls back to config)
            dyn_min = self.ha_client.get_sensor_value("sensor.wallbox_min_power_w")
            dyn_max = self.ha_client.get_sensor_value("sensor.wallbox_max_power_w")
            ev_min_power = int(dyn_min) if dyn_min and dyn_min > 0 else self.ev_min_power_w
            ev_max_power = int(dyn_max) if dyn_max and dyn_max > 0 else self.ev_max_power_w

            # Wallbox available = connected AND status not idle/faulted
            wallbox_available = wb_connected and wb_status not in (
                "Available", "Faulted", "Unknown",
            )

            # Read inputs for state machine
            ev_mode = self.ha_client.get_input_select(self.ev_charging_mode_entity)
            now = datetime.now(timezone.utc)
            tariff = self.optimizer.get_tariff_periods(now)
            grid_power = self._read_grid_power()
            battery_soc = self.ha_client.get_sensor_value(self.soc_entity) or 0

            # User power slider
            user_limit = self.ha_client.get_sensor_value(self.ev_power_limit_entity)
            max_power = (
                user_limit
                if user_limit and user_limit > 0
                else ev_max_power
            )

            # Battery protection — informational (for dashboard)
            if battery_soc >= 100:
                reaches_target, soc_at_target = True, 100.0
            else:
                reaches_target, soc_at_target = self.check_battery_protection()
            self._battery_reaches_target = reaches_target
            self._battery_min_soc_forecast = soc_at_target

            validate_power_readings(grid_w=grid_power, wallbox_w=wallbox_power)

            # Compute wallbox idle state (all modes)
            if wallbox_power == 0 and wb_status in ("Finishing", "SuspendedEV"):
                if self._ev_idle_since is None:
                    self._ev_idle_since = now
                idle_minutes = (now - self._ev_idle_since).total_seconds() / 60
                wallbox_idle = idle_minutes >= self.ev_auto_reset_timeout_min
            else:
                self._ev_idle_since = None
                idle_minutes = 0.0
                wallbox_idle = False

            # Build inputs and run state machine
            inputs = EVInputs(
                wallbox_available=wallbox_available,
                wallbox_power_w=wallbox_power,
                wallbox_status=wb_status,
                wallbox_idle=wallbox_idle,
                battery_protection_passed=reaches_target,
                battery_soc=battery_soc,
                charging_mode=ev_mode,
                is_cheap_tariff=tariff.is_cheap_now,
                grid_power_w=grid_power,
                min_power_w=ev_min_power,
                max_power_w=max_power,
            )
            prev_ev_state = self._ev_sm.state
            output = self._ev_sm.step(inputs)

            logger.info(f"EV [{output.state.value}] {output.target_power_w:.0f}W — {output.reason}")

            # Auto-revert: immediate/cheap idle timeout → switch mode back to solar
            if wallbox_idle and ev_mode in ("immediate", "cheap"):
                logger.info("Charging complete — reverting mode to solar")
                self.ha_client.set_input_select(self.ev_charging_mode_entity, "solar")
                self._ev_idle_since = None

            # Send power limit to OCPP (on change, or re-send if wallbox stuck)
            resend = (
                output.target_power_w > 0
                and wallbox_power == 0
                and wb_status == "SuspendedEVSE"
            )
            if output.target_power_w != self._last_ev_power_limit or resend:
                success = self.ha_client.set_sensor_state(
                    self.wallbox_power_limit_entity,
                    int(output.target_power_w),
                    attributes={
                        "friendly_name": "Wallbox Power Limit",
                        "unit_of_measurement": "W",
                        "icon": "mdi:speedometer",
                    },
                )
                if success:
                    self._last_ev_power_limit = output.target_power_w
                else:
                    logger.error("Failed to set wallbox power limit")

            # Discharge blocking: block when IMMEDIATE/CHEAP and power > 0
            should_block = (
                output.state in (EVState.IMMEDIATE, EVState.CHEAP)
                and output.target_power_w > 0
            )
            if should_block != self._discharge_blocked_by_ev:
                self._discharge_blocked_by_ev = should_block
                self._update_discharge_control()

            # Integration test observer
            if self._observer is not None:
                self._observer.observe(CycleSnapshot(
                    inputs=inputs,
                    output=output,
                    prev_state=prev_ev_state,
                    discharge_blocked_by_ev=self._discharge_blocked_by_ev,
                    last_power_limit_sent=self._last_ev_power_limit,
                    wb_connected=wb_connected,
                    idle_since=self._ev_idle_since,
                    excess_w=-grid_power + wallbox_power,
                    ts=datetime.now(timezone.utc),
                ))

            # Publish dashboard sensors
            self.ha_client.set_sensor_state(
                self.ev_charge_status_entity,
                output.state.value,
                attributes={
                    "friendly_name": "EV Charge Status",
                    "target_power_w": output.target_power_w,
                    "reason": output.reason,
                    "idle_minutes": round(idle_minutes, 1),
                    "wallbox_idle": wallbox_idle,
                    "icon": "mdi:ev-station",
                },
            )
            self.ha_client.set_sensor_state(
                self.ev_target_power_entity,
                int(output.target_power_w),
                attributes={
                    "friendly_name": "EV Target Power",
                    "unit_of_measurement": "W",
                    "reason": output.reason,
                    "battery_protection": not self._battery_reaches_target,
                    "battery_forecast_max_soc": self._battery_min_soc_forecast,
                    "icon": "mdi:ev-station",
                },
            )

        except Exception as e:
            logger.error(f"EV charging control failed: {e}", exc_info=True)

    def update_car_soc(self):
        """Read SOC from Smart car API and update HA sensor."""
        if not self.smart_car_enabled:
            return

        try:
            # Reuse cached client, re-auth only when needed
            if self._smart_car_client is None:
                self._smart_car_client = HelloSmartClient(
                    self.smart_car_user, self.smart_car_password
                )
                self._smart_car_client.authenticate()

            vin = self.smart_car_vin
            if not vin:
                vehicles = self._smart_car_client.list_vehicles()
                if not vehicles:
                    logger.error("Smart car: no vehicles found")
                    return
                vin = vehicles[0]

            ev = get_ev_status(self._smart_car_client, vin)
            logger.info(
                f"Smart car SOC: {ev.soc}% "
                f"charger={CHARGER_STATE_LABELS.get(ev.charger_state, ev.charger_state)} "
                f"current={ev.charge_current_a}A"
            )

            self.ha_client.set_sensor_state(
                self.smart_car_soc_entity,
                ev.soc,
                attributes={
                    "state_class": "measurement",
                    "unit_of_measurement": "%",
                    "device_class": "battery",
                    "icon": "mdi:car-battery",
                    "friendly_name": "Smart Battery",
                    "attribution": "Data provided by Hello Smart API",
                    "charger_state": CHARGER_STATE_LABELS.get(ev.charger_state, str(ev.charger_state)),
                    "charge_current_a": ev.charge_current_a,
                    "time_to_full_min": ev.time_to_full_min if ev.time_to_full_min < 2047 else None,
                    "range_km": ev.range_km,
                },
            )

        except Exception as e:
            logger.error(f"Smart car SOC update failed: {e}")
            self._smart_car_client = None  # Force re-auth on next attempt

    def _query_last_value(self, entity_id: str) -> str | None:
        """Query InfluxDB for the last known value of an HA entity."""
        try:
            # HA InfluxDB integration stores entity_id without domain prefix
            # e.g. "sensor.smart_battery" → entity_id tag = "smart_battery"
            short_id = entity_id.split(".", 1)[-1] if "." in entity_id else entity_id
            query_api = self.influx_client.query_api()
            query = f'''
            from(bucket: "HomeAssistant")
              |> range(start: -7d)
              |> filter(fn: (r) => r.entity_id == "{short_id}")
              |> filter(fn: (r) => r._field == "value")
              |> last()
            '''
            result = query_api.query(query, org="spiessa")
            if result and result[0].records:
                value = result[0].records[0].get_value()
                logger.info(f"Restored {entity_id} from InfluxDB: {value}")
                return value
        except Exception as e:
            logger.debug(f"Could not query InfluxDB for {entity_id}: {e}")
        return None

    def _ensure_sensor_exists(self, entity_id: str, default_state, attributes: dict):
        """Ensure a sensor exists in HA. Restores last value from InfluxDB if missing."""
        existing = self.ha_client.get_state(entity_id)
        if existing is not None:
            logger.debug(f"Sensor {entity_id} exists (state={existing.get('state')})")
            return
        # Try to restore from InfluxDB history
        restored = self._query_last_value(entity_id)
        state = restored if restored is not None else default_state
        logger.info(f"Creating sensor {entity_id} with state={state}"
                     f"{' (from InfluxDB)' if restored is not None else ' (default)'}")
        self.ha_client.set_sensor_state(entity_id, state, attributes=attributes)

    def _publish_initial_sensors(self):
        """Ensure all managed sensors exist in HA at startup."""
        try:
            self._ensure_sensor_exists(
                self.ev_target_power_entity,
                0,
                {"friendly_name": "EV Target Power", "unit_of_measurement": "W",
                 "reason": "Waiting for first update", "icon": "mdi:ev-station"},
            )
            self._ensure_sensor_exists(
                self.ev_charge_status_entity,
                "unknown",
                {"friendly_name": "EV Charge Status",
                 "status_text": "Waiting for first update", "icon": "mdi:ev-station"},
            )
            if self.smart_car_enabled:
                self._ensure_sensor_exists(
                    self.smart_car_soc_entity,
                    "unknown",
                    {"state_class": "measurement", "unit_of_measurement": "%",
                     "device_class": "battery", "icon": "mdi:car-battery",
                     "friendly_name": "Smart Battery"},
                )
            logger.info("Initial sensor check complete")
        except Exception as e:
            logger.warning(f"Failed to check initial sensors: {e}")

    def start(self):
        """Start the scheduler."""
        logger.info(f"Starting scheduler (every {self.update_interval} minutes)")

        # Ensure all sensors exist in HA before anything else
        self._publish_initial_sensors()

        # Run immediately
        self.run_optimization()

        # Schedule regular updates
        self.scheduler.add_job(
            self.run_optimization,
            "interval",
            minutes=self.update_interval,
            id="optimization",
            name="Battery Optimization",
            max_instances=1,
            coalesce=True,
        )

        # Schedule EV charging control (10-second interval)
        if self.ev_charging_enabled:
            self.control_ev_charging()  # Run immediately
            self.scheduler.add_job(
                self.control_ev_charging,
                "interval",
                seconds=10,
                id="ev_charging",
                name="EV Charging Control",
                max_instances=1,
                coalesce=True,
            )
            logger.info("EV charging control enabled (10-second interval)")

        # Schedule Smart car SOC update (hourly)
        if self.smart_car_enabled:
            self.update_car_soc()  # Run immediately
            self.scheduler.add_job(
                self.update_car_soc,
                "interval",
                hours=1,
                id="smart_car_soc",
                name="Smart Car SOC Update",
                max_instances=1,
                coalesce=True,
            )
            logger.info("Smart car SOC update enabled (1-hour interval)")

        self.scheduler.start()

        # Log next run time
        job = self.scheduler.get_job("optimization")
        if job:
            logger.info(f"Next optimization at {job.next_run_time}")

    def stop(self):
        """Stop the scheduler."""
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        self.close()
        logger.info("Stopped")


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict. Override values win."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str = None) -> dict:
    """Load configuration with secrets from environment.

    Strategy:
    1. Load defaults from /usr/share/energymanager/energymanager.yaml.example
    2. Load user config from /config/energymanager.yaml (via --config)
    3. Deep-merge: defaults first, user values win
    4. Overlay secrets from environment variables (set by startup script from HA UI)
    5. User file is never overwritten (source of truth for non-secrets)

    Args:
        config_path: Path to user config file (passed via --config argument)

    Returns:
        Merged configuration dictionary with secrets
    """
    import yaml
    import os

    defaults = {}
    user_config = {}

    # Load defaults from template (shipped in image)
    defaults_path = Path("/usr/share/energymanager/energymanager.yaml.example")
    if defaults_path.exists():
        logger.debug(f"Loading defaults from {defaults_path}")
        with open(defaults_path) as f:
            defaults = yaml.safe_load(f) or {}

    # Load user config (non-secrets from /config/energymanager.yaml)
    if config_path:
        path = Path(config_path)
        if path.exists():
            logger.info(f"Loading user config from {path}")
            with open(path) as f:
                user_config = yaml.safe_load(f) or {}
        else:
            logger.warning(f"User config not found: {path}, using defaults only")
    else:
        # Fallback for local development/testing
        test_config = Path(__file__).parent / "testdata" / "options.json"
        if test_config.exists():
            logger.info(f"Using test config from {test_config}")
            with open(test_config) as f:
                return json.load(f)

    # Merge: defaults first, user wins
    merged = deep_merge(defaults, user_config)

    # Overlay secrets from environment variables (set by HA Configuration UI)
    influxdb_token = os.environ.get("INFLUXDB_TOKEN")
    if influxdb_token:
        if "influxdb" not in merged:
            merged["influxdb"] = {}
        merged["influxdb"]["token"] = influxdb_token
        logger.info("InfluxDB token loaded from environment")
    else:
        logger.warning("InfluxDB token not set - configure it in the add-on Configuration tab")

    smart_user = os.environ.get("SMART_USER")
    smart_password = os.environ.get("SMART_PASSWORD")
    if smart_user or smart_password:
        if "smart_car" not in merged:
            merged["smart_car"] = {}
        if smart_user:
            merged["smart_car"]["user"] = smart_user
        if smart_password:
            merged["smart_car"]["password"] = smart_password
        logger.info("Smart car credentials loaded from environment")

    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if telegram_bot_token or telegram_chat_id:
        if "telegram" not in merged:
            merged["telegram"] = {}
        if telegram_bot_token:
            merged["telegram"]["bot_token"] = telegram_bot_token
        if telegram_chat_id:
            merged["telegram"]["chat_id"] = telegram_chat_id
        logger.info("Telegram credentials loaded from environment")

    return merged


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="EnergyManager Add-on")
    parser.add_argument("--config", help="Path to config file")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"EnergyManager Add-on v{__version__}")
    logger.info("=" * 60)

    # Load config
    options = load_config(args.config)

    # Set log level
    log_level = options.get("log_level", "info").upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))
    logger.info(f"Log level: {log_level}")

    # Create and start manager
    manager = EnergyManager(options)

    # Handle shutdown signals
    def shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        manager.connect()
        manager.start()

        # Keep running
        while True:
            time.sleep(60)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        manager.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
