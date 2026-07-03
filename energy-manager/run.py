#!/usr/bin/env python3
"""EnergyManager Add-on for Home Assistant.

Optimizes battery usage based on PV and load forecasts.
"""

__version__ = "1.9.2"

import json
import logging
import signal
import sys
import time
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from src.forecast_reader import ForecastReader
from src.ha_client import HAClient
from src.battery_optimizer import BatteryOptimizer, should_charge_now
from src.appliance_signal import ApplianceSignal
from src.ev_battery import EVBatteryOptimizer
from src.ev_state_machine import EVStateMachine, EVInputs, EVState
from src.ev_charging import (
    build_solar_candidates,
    snap_to_power_step,
    POWER_STEPS_3P,
)
from src.influxdb_writer import SimulationWriter
from src.integration_observer import CycleSnapshot, IntegrationObserver
from src.flows_daily import FlowsDaily
from src.notifications import init_telegram, notify_error
from src.sanity import validate_power_readings

# Swiss timezone for display
SWISS_TZ = ZoneInfo("Europe/Zurich")

# Adaptive SOC polling: 1 minute during charging, hourly otherwise
CAR_SOC_CHARGING_INTERVAL_S = 60


def swiss_time(dt: datetime) -> str:
    """Format datetime in Swiss timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SWISS_TZ).strftime("%H:%M")


def swiss_datetime(dt: datetime) -> str:
    """Format datetime in Swiss timezone with date."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
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
    handler.setFormatter(
        SwissFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )

# Silence apscheduler's per-cycle "Running job…" / "executed successfully"
# chatter (and its UTC timestamps). Warnings still surface.
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

logger = logging.getLogger("energy-manager")


class EnergyManager:
    """Main EnergyManager application."""

    def __init__(self, options: dict) -> None:
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
        self.influx_url = (
            f"http://{influx_opts.get('host', '192.168.0.203')}:{influx_opts.get('port', 8087)}"
        )
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

        self.soc_entity = battery_opts.get("soc_entity", "sensor.battery_state_of_capacity")
        self.discharge_control_entity = battery_opts.get(
            "discharge_control_entity", "number.battery_maximum_discharging_power"
        )
        # Export-peak-shaving charge control: defer battery charging so its
        # headroom absorbs the midday export peak. Runs only on a "shaving
        # day" — a once-daily mode decided at shaving_decision_hour (car
        # connected & full). Otherwise it is a "car day" (EV owns the surplus,
        # battery charges greedily). See FSD 4.2.3.
        self.charge_control_entity = battery_opts.get(
            "charge_control_entity", "number.battery_maximum_charging_power"
        )
        self.charge_max_w = battery_opts.get("max_charge_w", 5000)
        # Charge power used while shaving the export peak (use case B). Lower
        # than max_charge_w on purpose: a gentler C-rate is easier on the
        # battery and spreads absorption across more intervals → flatter
        # feed-in. Use case A (car present) still releases to max_charge_w.
        self.charge_shaving_power_w = battery_opts.get("charge_shaving_power_w", 2500)
        # B0 fill-margin: only shave when the day's (conservative p10) surplus
        # comfortably exceeds the headroom, not merely fills it. A day that only
        # just fills (or fills near sunset) has no real export peak to clip, so
        # deferring just risks under-filling → route it to greedy charging.
        self.charge_shaving_fill_margin = battery_opts.get("charge_shaving_fill_margin", 1.2)
        # Day-mode latch (FSD 4.2.3): the shave-vs-car-day choice is made ONCE
        # per day, at this local hour, then frozen until the next midnight. A
        # car that needs energy (or is absent) at the decision hour makes it a
        # car day → shaving off all day; only a connected-and-full car makes it
        # a shaving day. Before the decision hour the safe default is car day.
        self.shaving_decision_hour = int(battery_opts.get("shaving_decision_hour", 8))
        self._shaving_day_mode: str = "car_day"
        self._shaving_decision_date: date | None = None
        # Fail-safe: if the PV forecast heartbeat is older than this, the
        # forecast is stale/untrusted (upstream guard keeping last-good on bad
        # weather) → don't shave, charge greedily. Set on each tick by the loop.
        self.forecast_max_age_minutes = battery_opts.get("forecast_max_age_minutes", 120)
        self._forecast_fresh: bool = True
        # Latest combined net-energy forecast (p50), cached each 15-min cycle so
        # the 10-s EV loop can re-anchor the SOC sim to the live SOC for the
        # target gate (FSD 4.3.6) without re-fetching. None until the first cycle.
        self._latest_forecast = None
        # Tracks last applied charge power (W) to only write/log on change.
        # Tracks power, not a bool, so a use-case A→B transition (max→shaving
        # power) is still detected even though "charging" stays true.
        self._last_charge_power_w: int | None = None
        # Battery decision reasoning, published to sensor.battery_decision
        # for the dashboard (FSD 4.6.4). Recorded by control_battery_charge.
        self.battery_decision_entity = battery_opts.get(
            "battery_decision_entity", "sensor.battery_decision"
        )
        self._charge_use_case: str = "disabled"
        self._charge_action: str = "released"
        self._charge_reason: str = "charge shaving disabled"

        # Appliance signal config
        appliance_opts = options.get("appliances", {})
        self.appliance_power_w = appliance_opts.get("power_w", 2500)
        self.appliance_energy_wh = appliance_opts.get("energy_wh", 1500)

        # Battery parameters for appliance signal
        self.capacity_wh = battery_opts.get("capacity_kwh", 10.0) * 1000
        self.reserve_percent = battery_opts.get("reserve_percent", 10)

        # Topic 3 longevity (FSD 4.2.4): dynamic charge target — charge the LFP
        # battery only to the lowest SOC that stays protected from buying over
        # 48 h (worst-case p10 PV / p50 load) + margin, then hold; reduces
        # high-SOC dwell. OFF by default pending live validation (Topics 1/2/4
        # ship first). The target is NOT threaded into the EV/discharge forecast.
        self.charge_target_enabled = battery_opts.get("charge_target_enabled", False)
        self.charge_target_margin = float(battery_opts.get("charge_target_margin", 10.0))
        self.charge_target_horizon_h = int(battery_opts.get("charge_target_horizon_h", 48))
        # Floor on the dynamic target: even when the 48 h survival need is lower,
        # always charge to at least this SOC. 80% is well within the LFP-friendly
        # band (no longevity cost) and keeps headroom available for shaving. The
        # survival math still uses no_buy_floor_percent; this only raises the
        # final target.
        self.charge_target_min = float(battery_opts.get("charge_target_min", 90.0))
        self.charge_target_full_interval_days = float(
            battery_opts.get("charge_target_full_interval_days", 7)
        )
        # Current dynamic ceiling (%); 100 until first optimization (safe default).
        # Enforced in software (charge limit 0 at/above target) because the
        # inverter's native max-SOC entity only accepts 90-100%.
        self._battery_target_soc: float = 100.0
        # Human-readable explanation of the current target (published to HA).
        self._charge_target_reason: str = ""

        # Sensor entities for appliance signal calculation
        sensors_opts = options.get("sensors", {})
        self.pv_power_entity = sensors_opts.get("pv_power", "sensor.solar_pv_total_ac_power")
        self.load_power_entity = sensors_opts.get("load_power", "sensor.house_load_power")
        self.surplus_power_entity = sensors_opts.get("surplus_power", "sensor.surplus_power")
        self.appliance_signal_entity = sensors_opts.get(
            "appliance_signal", "sensor.appliance_signal"
        )

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

        # Daily household flows summary (long-term reporting)
        reporting_opts = options.get("reporting", {})
        self.flows_daily = FlowsDaily(
            influx_host=influx_opts.get("host", "192.168.0.203"),
            influx_port=influx_opts.get("port", 8087),
            influx_token=influx_token,
            influx_org=influx_opts.get("org", "energymanagement"),
            tariff=self.optimizer,
            ht_chf_kwh=reporting_opts.get("import_ht_chf_kwh", 0.3202),
            nt_chf_kwh=reporting_opts.get("import_nt_chf_kwh", 0.2434),
            feed_in_chf_kwh=reporting_opts.get("feed_in_chf_kwh", 0.09),
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
        self.dtsu_grid_power_entity = sensors_opts.get(
            "dtsu_grid_power", "sensor.power_meter_active_power"
        )
        self.wallbox_power_entity = ev_opts.get("wallbox_power_entity", "sensor.wallbox_power")
        self.wallbox_connected_entity = ev_opts.get(
            "wallbox_connected_entity", "binary_sensor.wallbox_connected"
        )
        self.wallbox_power_limit_entity = ev_opts.get(
            "wallbox_power_limit_entity", "number.wallbox_power_limit"
        )
        self.ev_target_power_entity = ev_opts.get(
            "ev_target_power_entity", "sensor.ev_target_power"
        )
        self._last_ev_power_limit = None
        self._last_ev_power_limit_at: float = 0.0  # monotonic timestamp
        self._ev_sm = EVStateMachine()
        self._surplus_samples: list[float] = []  # rolling 30s avg (3 × 10 s)
        self.ev_min_solar_power_entity = ev_opts.get(
            "min_solar_power_entity", "input_number.ev_min_solar_power"
        )
        self.ev_phases = ev_opts.get("phases", 3)
        self.ev_min_amps = ev_opts.get("min_current_a", 6)
        self.ev_max_amps = ev_opts.get("max_current_a", 16)

        # Charging mode config (FSD 4.5.4)
        self.manual_power_entity = ev_opts.get(
            "manual_power_entity", "input_number.ev_manual_power"
        )
        self.ev_charging_mode_entity = ev_opts.get("mode_entity", "input_select.ev_charging_mode")
        self.ev_charge_status_entity = ev_opts.get(
            "charge_status_entity", "sensor.ev_charge_status"
        )
        self.ev_wallbox_status_entity = ev_opts.get(
            "wallbox_status_entity", "sensor.wallbox_status"
        )
        self.car_ready_entity = ev_opts.get("car_ready_entity", "binary_sensor.car_ready")
        self.ev_auto_reset_timeout_min = ev_opts.get("auto_reset_timeout_min", 5)
        # EV safety rule floor — independent from battery.reserve_percent.
        # The nightly battery-discharge block uses battery.reserve_percent
        # to gate discharge during expensive hours; the EV safety rule uses
        # this higher floor to keep a buffer for house consumption before
        # it diverts surplus to the car. See FSD 4.3.6.
        # Buy-protection floor (FSD 4.2.2 / 4.3.6): the home-battery SOC the
        # system keeps to avoid grid purchases. Shared by Topics 1, 2 and 3.
        # Renamed from ev_charging.reserve_percent (kept as fallback).
        self.no_buy_floor_percent = battery_opts.get(
            "no_buy_floor_percent", ev_opts.get("reserve_percent", 20)
        )
        self.ev_reserve_percent = self.no_buy_floor_percent  # legacy alias
        self._ev_idle_since: datetime | None = None
        self._observer = IntegrationObserver() if self.ev_charging_enabled else None
        self._last_mode_error_notified: str | None = None
        self._ev_safe: bool = False
        self._battery_min_soc_forecast: float = 0.0
        # End-of-day (today, Europe/Zurich) car SOC forecast — refreshed
        # every 15 min by write_energy_balance(). Used by the EV control
        # loop to decide whether the snap-up step (which drains the home
        # battery) is needed to reach the EV target. None = no forecast
        # yet (startup) or smart_car disabled → keep current snap-up.
        self.ev_soc_forecast_eod_today: float | None = None
        # Forecast-based "car reaches target SOC at" prediction (solar-aware,
        # from the car SOC forecast in write_energy_balance). ISO-UTC string of
        # the first forecast timestamp where car SOC ≥ target, plus the target
        # used (sensor.smart_charging_max_last_known). None = not forecast to
        # reach target within the horizon, or no car forecast this run.
        self.ev_soc_forecast_full_time: str | None = None
        self.ev_soc_forecast_target_soc: float | None = None
        # Snap-up gate (Section 4.3.6): does the HOME battery still reach full
        # today with the EV load accounted for? Recomputed every 15 min in
        # run_optimization, read by the 10-s EV loop. False until first
        # optimization (safe default: no battery drain).
        self._battery_full_with_ev: bool = False
        # Log dedup: one INFO line per state/power change; DEBUG between,
        # with an INFO heartbeat every 60 s so the log stays alive while idle.
        self._ev_log_signature: tuple | None = None
        self._ev_log_last_info_monotonic: float = 0.0
        # Layer-2 EV decision helper — bound in connect() once influx_client exists
        self.ev_battery_optimizer: EVBatteryOptimizer | None = None

        # Smart car config (FSD 4.5 Step 2 — hourly SOC readback)
        smart_opts = options.get("smart_car", {})
        self.smart_car_enabled = smart_opts.get("enabled", False)
        self.smart_car_soc_entity = smart_opts.get("soc_entity", "sensor.smart_battery")
        self.smart_car_capacity_kwh = float(smart_opts.get("capacity_kwh", 100.0))
        self.smart_car_charge_efficiency = float(smart_opts.get("charge_efficiency", 0.88))
        # Phase 3 — manual-charge kWh budget entities
        self.ev_target_soc_entity = ev_opts.get("target_soc_entity", "input_number.ev_target_soc")
        self.car_soc_last_known_entity = smart_opts.get(
            "soc_last_known_entity", "sensor.smart_battery_last_known"
        )
        # EV target SOC (car-side limit) — used by the solar-mode snap-up
        # gate: only drain the home battery if the EOD forecast won't
        # reach this target.
        self.car_charging_max_entity = smart_opts.get(
            "charging_max_entity", "sensor.smart_charging_max_last_known"
        )
        self.wallbox_session_energy_entity = ev_opts.get(
            "wallbox_session_energy_entity", "sensor.wallbox_energy"
        )
        self._last_wallbox_status: str | None = None
        self._last_ev_charging_mode: str | None = None
        self._last_car_soc_poll: float = 0.0

    def connect(self) -> None:
        """Connect to services."""
        logger.info("Connecting to services...")
        self.forecast_reader.connect()
        self.influx_client = InfluxDBClient(
            url=self.influx_url, token=self.influx_token, org=self.influx_org
        )
        self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
        self.simulation_writer.connect()
        self.ev_battery_optimizer = EVBatteryOptimizer(
            influx_client=self.influx_client,
            bucket=self.output_bucket,
            capacity_wh=self.capacity_wh,
            min_soc_percent=self.ev_reserve_percent,
        )
        logger.info("Connected successfully")

    def close(self) -> None:
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

    def _read_car_soc_with_fallback(self) -> float | None:
        """Read EV SOC with fallback to last-known value from InfluxDB.

        The smarthashtag integration goes unavailable when the car is asleep.
        We then use the last numeric reading from the last 7 days so the
        car-SOC forecast remains usable.
        """
        raw = self.ha_client.get_sensor_value(self.smart_car_soc_entity)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass

        cached = self._query_last_value(self.smart_car_soc_entity)
        if cached is not None:
            try:
                return float(cached)
            except (TypeError, ValueError):
                pass

        logger.warning(
            f"No EV SOC available from {self.smart_car_soc_entity} "
            "(neither live nor cached) — skipping car SOC forecast"
        )
        return None

    def write_energy_balance(self, forecast, house_soc: float | None = None) -> None:
        """Write energy balance + car SOC forecast to InfluxDB.

        Car SOC model: the house battery is a buffer. Surplus first refills
        the house battery; the overflow past 100% goes to the car at
        charge_efficiency. Deficits drain the house battery (the car is
        unaffected). Starts from the current car SOC.

        Args:
            forecast: DataFrame with pv_energy_wh, load_energy_wh, net_energy_wh
            house_soc: current house battery SOC (%); if None, car curve is skipped

        """
        if forecast.empty:
            return

        car_soc = self._read_car_soc_with_fallback() if self.smart_car_enabled else None
        sim_car = car_soc is not None and house_soc is not None and self.smart_car_capacity_kwh > 0
        # Target SOC the car charges toward (car-side limit,
        # sensor.smart_charging_max_last_known). Used to predict the first
        # forecast timestamp the car reaches the target — published as the
        # car_target_time attribute on sensor.ev_target_power.
        target_soc: float | None = None
        car_full_time: str | None = None
        if sim_car:
            house_cap_kwh = self.capacity_wh / 1000
            house_kwh = max(0.0, min(house_cap_kwh, house_soc / 100 * house_cap_kwh))
            # The house battery fills only to its dynamic charge target (FSD
            # 4.2.4); surplus past the target overflows to the car (or exports).
            house_ceil_kwh = self._battery_target_soc / 100.0 * house_cap_kwh
            car_kwh_added = 0.0
            raw_target = self.ha_client.get_sensor_value(self.car_charging_max_entity)
            try:
                target_soc = float(raw_target) if raw_target is not None else None
            except (TypeError, ValueError):
                target_soc = None

        # End-of-day cutoff (today 23:59:59 Europe/Zurich) as UTC for
        # comparison against forecast point timestamps. Car SOC is
        # monotonic non-decreasing, so the latest point ≤ cutoff gives
        # the EOD value.
        eod_today_utc = (
            datetime.now(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        eod_car_soc_pct: float | None = None

        points = []
        cumulative_wh = 0.0
        for t in forecast.index:
            ts = t if t.tzinfo else t.replace(tzinfo=UTC)
            row = forecast.loc[t]

            pv_wh = float(row.get("pv_energy_wh", 0))
            load_wh = float(row.get("load_energy_wh", 0))
            net_wh = float(row.get("net_energy_wh", 0))
            cumulative_wh += net_wh

            point = (
                Point("energy_balance")
                .field("cumulative_wh", cumulative_wh)
                .field("pv_power_w", pv_wh * 4)
                .field("load_power_w", load_wh * 4)
                .time(ts, WritePrecision.S)
            )

            if sim_car:
                net_kwh = net_wh / 1000
                if net_kwh >= 0:
                    headroom = max(0.0, house_ceil_kwh - house_kwh)
                    to_house = min(net_kwh, headroom)
                    house_kwh += to_house
                    overflow = net_kwh - to_house
                    car_kwh_added += overflow * self.smart_car_charge_efficiency
                else:
                    house_kwh = max(0.0, house_kwh + net_kwh)
                car_soc_pct = min(
                    100.0,
                    car_soc + car_kwh_added / self.smart_car_capacity_kwh * 100,
                )
                point = point.field("car_soc_percent", float(car_soc_pct))
                if ts <= eod_today_utc:
                    eod_car_soc_pct = float(car_soc_pct)
                if (
                    car_full_time is None
                    and target_soc is not None
                    and car_soc_pct >= target_soc
                ):
                    car_full_time = ts.isoformat()

            points.append(point)

        # Publish EOD forecast for the EV control loop's snap-up gate.
        # None if no car forecast was computed this run (e.g. car_soc
        # unavailable) — gate then falls back to current snap-up behavior.
        self.ev_soc_forecast_eod_today = eod_car_soc_pct
        self.ev_soc_forecast_full_time = car_full_time
        self.ev_soc_forecast_target_soc = target_soc

        self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=points)
        logger.info(
            f"Written {len(points)} energy balance points"
            + (f" (car SOC forecast from {car_soc:.0f}%)" if sim_car else "")
        )

    def write_decision(self, decision, current_soc: float) -> None:
        """Write discharge decision to InfluxDB."""
        now = datetime.now(UTC)

        point = (
            Point("discharge_decision")
            .field("allowed", decision.discharge_allowed)
            .field("reason", decision.reason)
            .field("min_soc_percent", float(decision.min_soc_percent))
            .field("current_soc", float(current_soc))
            .time(now, WritePrecision.S)
        )

        self.write_api.write(bucket=self.output_bucket, org=self.influx_org, record=point)

    def control_battery(self, discharge_allowed: bool) -> None:
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
        current_value = self.ha_client.get_battery_discharge_power(self.discharge_control_entity)

        if current_value is None:
            logger.warning(
                f"Could not read current discharge power from {self.discharge_control_entity}"
            )
            # Continue anyway - we should try to set the value
        else:
            logger.info(f"Current discharge power: {current_value}W, target: {target_value}W")

            # Check if already at target value (with small tolerance for float comparison)
            if abs(current_value - target_value) < 1:
                logger.debug(
                    f"Discharge power already at target ({target_value}W), no change needed"
                )
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
        verified_value = self.ha_client.get_battery_discharge_power(self.discharge_control_entity)

        if verified_value is not None and abs(verified_value - target_value) < 1:
            self.last_discharge_allowed = discharge_allowed
            logger.info(
                f"Battery control verified: {self.discharge_control_entity} = {verified_value}W"
            )
        elif verified_value is not None:
            logger.warning(
                f"Battery control verification mismatch: "
                f"set {target_value}W but read {verified_value}W"
            )
            notify_error(
                title="Battery Control Verification Failed",
                message=(
                    f"Set discharge power to {target_value}W but verification read "
                    f"{verified_value}W.\n\n"
                    f"Entity: {self.discharge_control_entity}\n"
                    f"The battery may not be in the expected state!"
                ),
            )
        else:
            # Could not verify but set succeeded
            self.last_discharge_allowed = discharge_allowed
            logger.info(
                f"Battery control set: {self.discharge_control_entity} = "
                f"{target_value}W (unverified)"
            )

    def _update_discharge_control(self) -> None:
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

    def _car_full_inputs(self):
        """Read (car_present, car_soc, target) live from HA for the shaving premise."""
        car_state = self.ha_client.get_state(self.car_ready_entity)
        car_present = car_state is not None and car_state.get("state") == "on"
        car_soc = self.ha_client.get_sensor_value(self.smart_car_soc_entity)
        target = self.ha_client.get_sensor_value(self.car_charging_max_entity)
        return car_present, car_soc, target

    def _car_departed(self) -> bool:
        """Return True when a shaving day's premise no longer holds (departure trigger).

        Fires when the car has **disconnected**, or is connected but its SOC
        has dropped **below target** ("no longer full"). A connected car with
        unknown SOC/target is NOT treated as departed — the cached last-known
        SOC is held (§7.7), so a stale read keeps the shaving day rather than
        cancelling it on missing data.
        """
        car_present, car_soc, target = self._car_full_inputs()
        if not car_present:
            return True
        if car_soc is not None and target is not None and car_soc < float(target):
            return True
        return False

    def _update_shaving_day_mode(self, now) -> None:
        """Decide the day's shaving mode at the configured hour; downgrade on departure.

        The shave-vs-car-day choice is a daily snapshot taken once at
        shaving_decision_hour (Europe/Zurich): car connected AND at/above
        target → shaving day; below target, absent, or unknown → car day
        (FSD 4.2.3). Latched until the next local midnight, with one one-way
        downgrade: a shaving day reverts to a car day the instant its premise
        breaks — the **departure trigger**, `_car_departed()` (car disconnects
        or drops below target) — and stays a car day for the rest of the day
        (never re-arms shaving). Before the decision hour the default is car day.
        """
        local_now = now.astimezone(SWISS_TZ)
        today = local_now.date()
        if self._shaving_decision_date == today:
            # Latch committed today. Departure trigger: a shaving day drops to a
            # car day as soon as the car leaves or is no longer full, so the
            # battery stops holding headroom for an export peak whose surplus the
            # returning car will need. One-way — a later full reconnect does not
            # restore shaving (stability; matches the once-per-day philosophy).
            if self._shaving_day_mode == "shaving_day" and self._car_departed():
                self._shaving_day_mode = "car_day"
                logger.info(
                    "Shaving day → car day: car departed or no longer full "
                    "(departure trigger) — battery charges greedily for the rest of the day"
                )
            return
        if local_now.hour < self.shaving_decision_hour:
            self._shaving_day_mode = "car_day"  # before the decision → safe default
            return
        # First tick at/after the decision hour today → take the one-shot snapshot.
        self._shaving_decision_date = today
        car_present, car_soc, target = self._car_full_inputs()
        car_full = (
            car_present
            and car_soc is not None
            and target is not None
            and car_soc >= float(target)
        )
        self._shaving_day_mode = "shaving_day" if car_full else "car_day"
        logger.info(
            "Shaving day-mode decided at %02d:00 → %s "
            "(car_present=%s soc=%s target=%s)",
            self.shaving_decision_hour,
            self._shaving_day_mode,
            car_present,
            car_soc,
            target,
        )

    def _charge_gate_active(self) -> bool:
        """Return True when export-peak-shaving may manage charging today.

        True only on a shaving day — the once-daily mode decided at
        shaving_decision_hour (car connected & full). On a car day the EV
        owns the surplus and the battery charges greedily. See FSD 4.2.3.
        """
        return self._shaving_day_mode == "shaving_day"

    def control_battery_charge(self, current_soc: float, forecast, gate_forecast, now) -> None:
        """Defer battery charging to shave the export peak (FSD 4.2.3).

        Stateless per-tick decision (re-evaluated every 15 min): hold the
        battery's headroom for the highest-surplus intervals of the rest of
        the day so it absorbs the export peak instead of charging greedily
        at sunrise. Sets number.battery_maximum_charging_power to 0 (defer)
        or a charge power. Always releases to max when the gate is inactive
        (use case A) or the day is marginal (B0), so it never leaves charging
        stuck off.

        Records the decision (use case / action / reason) on self for
        publication to sensor.battery_decision (FSD 4.6.4).
        """
        # Topic 3 longevity (FSD 4.2.4): hold once the battery has charged to the
        # dynamic target (lowest SOC that stays protected over 48 h). Enforced in
        # software (limit 0) because the inverter's native max-SOC accepts only
        # 90-100%. When disabled or at the 100% calibration/fail-safe target this
        # never fires. Re-evaluated every 15 min.
        if self.charge_target_enabled and current_soc >= self._battery_target_soc:
            self._charge_use_case = "B"
            self._charge_action = "deferred"
            self._charge_reason = (
                f"at charge target {self._battery_target_soc:.0f}% — holding "
                f"(LFP longevity, surplus exported)"
            )
            self._apply_charge_control(False, self._charge_reason)
            return

        # Decide the day's shaving mode once, at the configured local hour
        # (FSD 4.2.3). A car day → the EV owns the surplus and the battery
        # charges greedily; a shaving day → the battery holds headroom for the
        # peak. Latched until the next local midnight.
        self._update_shaving_day_mode(now)

        if not self._charge_gate_active():
            # Use case A — car day: the EV owns the surplus (now or later);
            # the battery charges greedily so a late top-up never starves it.
            self._charge_use_case = "A"
            self._charge_action = "released"
            self._charge_reason = (
                "car day — EV owns the surplus, battery charges greedily"
            )
            self._apply_charge_control(True, "car day — charge released")
            return

        # Use case B — shaving day: no EV load expected today → defer to shave
        # the export peak.
        # Headroom is to the dynamic charge target (FSD 4.2.4), not 100% — the
        # battery is only filled to what the next days need.
        self._charge_use_case = "B"
        headroom_wh = max(
            0.0, (self._battery_target_soc - current_soc) / 100.0 * self.capacity_wh
        )

        # Fail-safe: a stale forecast (upstream guard keeping last-good because
        # the weather input went bad) must not drive shaving. Charge greedily —
        # the safe default that never leaves the battery under-filled.
        if not self._forecast_fresh:
            self._charge_action = "charging"
            self._charge_reason = (
                "PV forecast stale → charge greedily, no shaving (fail-safe)"
            )
            self._apply_charge_control(True, self._charge_reason)
            return

        # B0 marginal-day gate: only shave on an abundant day, i.e. one where
        # the battery would greedily reach full today even under a conservative
        # (p10 PV) forecast, with a comfortable surplus margin. If it would not
        # fill (or only barely fills) under that pessimistic estimate, the day
        # is too marginal to gamble headroom on — charge greedily at full power
        # to capture the scarce surplus.
        if not self._will_fill_today(current_soc, gate_forecast, now):
            self._charge_action = "charging"
            self._charge_reason = (
                "marginal day — battery not forecast to fill today "
                "(conservative p10 PV) → charge greedily, no shaving"
            )
            self._apply_charge_control(True, self._charge_reason)
            return

        # Build the rest-of-today (Europe/Zurich) per-interval surplus curve
        # from the forecast. net_energy_wh = PV − load per 15 min = export.
        eod = (
            datetime.now(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        start_aligned = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        remaining: list[float] = []
        current_surplus = 0.0
        for t in forecast.index:
            ts = t if t.tzinfo else t.replace(tzinfo=UTC)
            if ts < start_aligned or ts > eod:
                continue
            net = float(forecast.loc[t].get("net_energy_wh", 0.0))
            if not remaining:
                current_surplus = net  # first qualifying interval = now
            remaining.append(net)

        # Per-interval absorption cap (Wh) at the shaving power. The water-fill
        # must account for it: at a reduced power each 15-min interval absorbs
        # at most this much, so more intervals are needed to fill the headroom.
        max_charge_per_interval_wh = self.charge_shaving_power_w * 0.25
        charge = should_charge_now(
            remaining, headroom_wh, current_surplus, max_charge_per_interval_wh
        )
        reason = (
            f"surplus now {current_surplus:.0f}Wh, headroom {headroom_wh:.0f}Wh → "
            f"{'charge' if charge else 'defer (shaving export peak)'}"
        )
        self._charge_action = "charging" if charge else "deferred"
        self._charge_reason = reason
        self._apply_charge_control(charge, reason, self.charge_shaving_power_w)

    def _will_fill_today(self, current_soc: float, gate_forecast, now) -> bool:
        """Check whether the battery is forecast to reach its target today.

        Runs a greedy SOC simulation (charge at max_charge_w, no deferral) over
        today's *conservative* forecast (p10 PV, p50 load) and asks whether any
        interval reaches the dynamic charge target (`_battery_target_soc`, FSD
        4.2.4 — not a fixed 100%). If the battery fills even under that
        pessimistic production estimate, the day is abundant enough to shave the
        export peak; otherwise it is marginal and we charge greedily.

        The greedy sim never defers, so the result is independent of the shaving
        decision — no feedback loop. The p10 PV margin means a marginal day
        cannot trip shaving and then fail to fill the battery. A second margin
        (charge_shaving_fill_margin) requires the day's surplus to *comfortably*
        exceed the headroom, not merely reach it — a day that only just fills,
        or fills near sunset, has no real export peak worth deferring for.

        Returns:
            True if the battery reaches its target today under the conservative
            forecast AND the day's surplus exceeds headroom × fill_margin (a
            battery already at target now counts), else False. An empty forecast
            is treated as marginal (charge greedily — never defer blindly).

        """
        if gate_forecast is None or gate_forecast.empty:
            return False
        eod = (
            datetime.now(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        start_aligned = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        mask = [
            start_aligned <= (t if t.tzinfo else t.replace(tzinfo=UTC)) <= eod
            for t in gate_forecast.index
        ]
        today = gate_forecast[mask]
        if today.empty:
            return False

        # "Fill" means reach the dynamic charge target (FSD 4.2.4), not 100%.
        target = self._battery_target_soc
        sim = self.optimizer.simulate_soc(current_soc, today)
        if not bool((sim["soc_percent"] >= target).any()):
            return False  # would not reach target today → marginal

        # Fill-margin: the rest-of-today surplus must comfortably exceed the
        # headroom (to target), else it's a marginal day with no real peak.
        headroom_wh = max(0.0, (target - current_soc) / 100.0 * self.capacity_wh)
        total_surplus_wh = float(today["net_energy_wh"].clip(lower=0).sum())
        return total_surplus_wh >= headroom_wh * self.charge_shaving_fill_margin

    def _will_battery_fill_today_with_ev(self, current_soc: float, forecast, now) -> bool:
        """Return whether the home battery still reaches full today with the EV.

        Gate for the EV solar snap-up step (Section 4.3.6). The home-battery
        load forecast excludes the wallbox (house load ≈ 240 W even while the
        car draws 7 kW), so the plain SOC forecast is blind to the EV. Here we
        subtract the *actual current* wallbox draw from each remaining 15-min
        interval's net energy and re-simulate, so the answer reflects reality.

        - True  → battery reaches ≥99% by tonight despite the EV → snap-up OK.
        - False → battery would not fill → don't drain it further (snap down).

        Uses the live wallbox power as the EV estimate and re-runs every 15 min;
        that cadence is itself the hysteresis (the decision is latched between
        cycles, so the 10-s EV loop cannot oscillate within a window).
        """
        if forecast is None or forecast.empty:
            return False
        ev_w = self.ha_client.get_sensor_value(self.wallbox_power_entity) or 0.0
        eod = (
            datetime.now(SWISS_TZ)
            .replace(hour=23, minute=59, second=59, microsecond=0)
            .astimezone(UTC)
        )
        start_aligned = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        mask = [
            start_aligned <= (t if t.tzinfo else t.replace(tzinfo=UTC)) <= eod
            for t in forecast.index
        ]
        today = forecast[mask].copy()
        if today.empty:
            return False
        # Subtract the EV load the home-battery forecast doesn't see.
        today["net_energy_wh"] = today["net_energy_wh"] - float(ev_w) * 0.25
        sim = self.optimizer.simulate_soc(current_soc, today)
        # "Full" = reaches the dynamic charge target (FSD 4.2.4), not 100%.
        return bool((sim["soc_percent"] >= self._battery_target_soc).any())

    def _calibration_charge_due(self, now: datetime) -> bool:
        """Whether an LFP calibration full charge is due (FSD 4.2.4).

        Due when the battery has not reached ≥99% SOC within the last
        `charge_target_full_interval_days` (rolling — the clock restarts each
        time SOC reaches ≥99%, by sun or by a prior calibration). Reads the
        most recent ≥99% point
        from SOC history (no separate persisted state). On query error or no
        client, returns False (don't force a full charge on a transient error;
        the worst-case survival logic still protects against import).
        """
        if self.influx_client is None:
            return False
        short_id = self.soc_entity.split(".", 1)[-1]
        interval = self.charge_target_full_interval_days
        try:
            query = f'''
            from(bucket: "HomeAssistant")
              |> range(start: -{int(interval) + 1}d)
              |> filter(fn: (r) => r.entity_id == "{short_id}" and r._field == "value")
              |> filter(fn: (r) => r._value >= 99.0)
              |> last()
            '''
            result = self.influx_client.query_api().query(query, org=self.influx_org)
            if result and result[0].records:
                last_full = result[0].records[0].get_time()
                age_days = (now - last_full).total_seconds() / 86400.0
                return age_days >= interval
            # No ≥99% reading within the window → calibration overdue.
            return True
        except Exception as e:
            logger.debug(f"Calibration-due query failed: {e}")
            return False

    def _apply_charge_control(
        self, charge_allowed: bool, reason: str, power_w: int | None = None
    ) -> None:
        """Set max charging power and write/log only when the value changes.

        power_w is the charge limit to apply when charging is allowed; defaults
        to charge_max_w (use case A — greedy release). Use case B passes the
        lower charge_shaving_power_w. When deferring, the limit is always 0.
        """
        target = (power_w if power_w is not None else self.charge_max_w) if charge_allowed else 0
        if target == self._last_charge_power_w:
            return
        logger.info(
            f"Battery charge {'allowed' if charge_allowed else 'deferred'} "
            f"→ {self.charge_control_entity}={target}W ({reason})"
        )
        success, error_msg = self.ha_client.set_number(
            self.charge_control_entity, target, max_retries=5
        )
        if not success:
            logger.error(f"Failed to set charge power: {error_msg}")
            notify_error(
                title="Battery Charge Control Failed",
                message=(
                    f"Failed to set {self.charge_control_entity} to {target}W "
                    f"after 5 attempts.\nError: {error_msg}"
                ),
            )
            return
        self._last_charge_power_w = target

    def publish_battery_decision(self, decision, current_soc: float) -> None:
        """Publish combined battery reasoning to sensor.battery_decision.

        Surfaces both home-battery decisions for the dashboard (FSD 4.6.4):
        the discharge decision (4.2.2, two block flags) and the charge-
        shaving decision (4.2.3, use case A/B). Advisory display only.
        """
        discharge_allowed = not (
            self._discharge_blocked_by_protection or self._discharge_blocked_by_ev
        )
        charge_limit_w = None
        if self._last_charge_power_w is not None:
            charge_limit_w = self._last_charge_power_w
        # When is the home battery forecast to reach 100% today (SOC forecast,
        # planned scenario)? Independent of EV state so the battery card
        # can show "Full by HH:MM" even when no car is plugged in.
        battery_will_be_full = None
        battery_full_time = None
        battery_peak_soc = None
        if self.ev_battery_optimizer is not None:
            try:
                battery_will_be_full, peak_soc, battery_full_time, _ = (
                    self.ev_battery_optimizer.will_battery_hit_full(
                        full_threshold=self._battery_target_soc
                    )
                )
                if peak_soc is not None:
                    battery_peak_soc = round(peak_soc)
            except Exception as e:
                logger.debug(f"will_battery_hit_full failed: {e}")
        state = f"discharge={'on' if discharge_allowed else 'off'} charge={self._charge_action}"
        self.ha_client.set_sensor_state(
            self.battery_decision_entity,
            state,
            attributes={
                "friendly_name": "Battery Decision",
                "battery_soc": round(current_soc, 1),
                # Discharge decision (FSD 4.2.2)
                "discharge_allowed": discharge_allowed,
                "discharge_reason": decision.reason,
                "discharge_blocked_by_protection": self._discharge_blocked_by_protection,
                "discharge_blocked_by_ev": self._discharge_blocked_by_ev,
                "discharge_min_soc_percent": round(decision.min_soc_percent, 1),
                "expensive_import_wh": round(decision.expensive_import_wh, 0),
                # Charge-shaving decision (FSD 4.2.3)
                "charge_use_case": self._charge_use_case,
                "charge_action": self._charge_action,
                "charge_reason": self._charge_reason,
                "charge_limit_w": charge_limit_w,
                # Charge ceiling (Topic 3, FSD 4.2.4) + day-mode latch (Topic 5, FSD 4.2.3)
                "battery_target_soc": round(self._battery_target_soc),
                "charge_target_enabled": self.charge_target_enabled,
                "battery_target_reason": self._charge_target_reason,
                "shaving_day_mode": self._shaving_day_mode,
                "shaving_decision_hour": self.shaving_decision_hour,
                # Forecast — when does the home battery reach 100% today
                "battery_will_be_full": battery_will_be_full,
                "battery_full_time": battery_full_time,
                "battery_peak_soc": battery_peak_soc,
                "icon": "mdi:home-battery",
            },
        )

    def run_optimization(self) -> None:
        """Run battery optimization cycle."""
        logger.info("=" * 50)
        logger.info("Running battery optimization...")

        try:
            # Get current SOC
            current_soc = self.get_current_soc()
            logger.debug(f"Current battery SOC: {current_soc:.1f}%")

            # Get forecast
            now = datetime.now(UTC)
            start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)

            # Get tariff periods to determine forecast end
            tariff = self.optimizer.get_tariff_periods(now)
            # Extend to full PV forecast horizon (up to 5 days) for visualization
            # SOC simulation will naturally stop where load forecast ends
            pv_horizon = now + timedelta(days=5)
            end = max(tariff.target + timedelta(hours=1), pv_horizon)

            logger.info(f"Fetching forecasts from {swiss_datetime(start)} to {swiss_datetime(end)}")
            logger.info(
                f"Tariff: cheap={'Yes' if tariff.is_cheap_now else 'No'}, "
                f"cheap_end={swiss_datetime(tariff.cheap_end)}, "
                f"target={swiss_datetime(tariff.target)}"
            )

            forecast = self.forecast_reader.get_combined_forecast(
                start=start,
                end=end,
                percentile="p50",
            )

            if forecast.empty:
                logger.error("No forecast data available")
                return

            # Cache for the 10-s EV target gate's live SOC re-anchoring (FSD 4.3.6).
            self._latest_forecast = forecast

            # Conservative forecast for the marginal-day fill check (B0): low
            # PV (p10) against median load (p50). Shaving only runs when the
            # battery fills today even under this pessimistic estimate, so a
            # marginal day cannot trip shaving and then fail to fill. Everything
            # else (discharge, water-fill, dashboards) stays on p50 above.
            gate_forecast = self.forecast_reader.get_combined_forecast(
                start=start,
                end=end,
                pv_percentile="p10",
                load_percentile="p50",
            )

            # Fail-safe: a stale forecast heartbeat means the upstream guard is
            # keeping the last-good forecast because the weather input is bad.
            # Don't trust it for shaving → charge greedily instead. A missing
            # heartbeat (None) is treated as fresh so shaving still works before
            # the heartbeat feature is deployed / if metadata is absent.
            age_s = self.forecast_reader.get_forecast_age_seconds(now)
            self._forecast_fresh = age_s is None or age_s <= self.forecast_max_age_minutes * 60
            if not self._forecast_fresh:
                logger.warning(
                    f"PV forecast heartbeat is {age_s / 60:.0f} min old "
                    f"(> {self.forecast_max_age_minutes} min) — shaving will fall "
                    f"back to greedy charging"
                )

            logger.info(f"Got {len(forecast)} forecast periods")
            logger.debug(
                f"Forecast range: {swiss_datetime(forecast.index[0])} → "
                f"{swiss_datetime(forecast.index[-1])}"
            )

            # Topic 3 longevity (FSD 4.2.4): charge only to the lowest SOC that
            # keeps the home battery >= no_buy_floor over 48 h (worst-case p10 PV
            # / p50 load) + margin, floored at charge_target_min; hold above it.
            # A due calibration charge (rolling 7 d since the last >= 99%) and a
            # stale/missing forecast fail UP to 100%. The target is enforced ONLY
            # by control_battery_charge — it is deliberately NOT threaded into the
            # discharge/EV SOC forecast (those read the natural charge-to-100
            # trajectory; threading the cap in caused the 2026-06-18/19 incidents).
            if self.charge_target_enabled:
                calibration_due = self._calibration_charge_due(now)
                target_soc, target_reason = self.optimizer.compute_charge_target(
                    current_soc,
                    gate_forecast,
                    now,
                    reserve=self.no_buy_floor_percent,
                    margin_pct=self.charge_target_margin,
                    min_target=self.charge_target_min,
                    horizon_h=self.charge_target_horizon_h,
                    calibration_due=calibration_due,
                    forecast_fresh=self._forecast_fresh,
                )
                self._battery_target_soc = target_soc
                self._charge_target_reason = target_reason
                logger.info(f"Battery charge target: {target_soc:.0f}% ({target_reason})")
            else:
                self._battery_target_soc = 100.0
                self._charge_target_reason = "charge target disabled"

            # Discharge decision (Topic 4). The planned SOC forecast it produces
            # is the NATURAL charge-to-100 trajectory — the Topic 3 charge target
            # is deliberately NOT threaded in, so the EV safety gate (Rule 4) and
            # Topic 4 read the unpolluted forecast.
            decision, sim_battery_on, sim_battery_off, sim_planned = (
                self.optimizer.calculate_decision(
                    soc_percent=current_soc,
                    forecast=forecast,
                    now=now,
                    previously_blocked=self._discharge_blocked_by_protection,
                    max_soc_percent=100.0,
                )
            )

            if not sim_battery_on.empty:
                logger.debug(
                    f"Simulation first: {swiss_datetime(sim_battery_on.index[0])} "
                    f"SOC={sim_battery_on['soc_percent'].iloc[0]:.1f}%"
                )

            # Log decision
            logger.info(
                f"Decision: discharge_allowed={decision.discharge_allowed}, "
                f"min_soc={decision.min_soc_percent:.0f}%, "
                f"expensive_import={decision.expensive_import_wh:.0f}Wh"
            )
            logger.info(f"Reason: {decision.reason}")

            # Write results to InfluxDB
            # Three scenarios for visualization and the EV gate:
            # - battery_on:  free discharge (the discharge-allowed implication)
            # - battery_off: discharge held during cheap hours (the hold implication)
            # - planned:     whichever of the two the decision selects (what runs);
            #                the EV safety gate (Rule 4) reads this realistic path
            self.simulation_writer.write_soc_forecast(sim_battery_on, scenario="battery_on")
            self.simulation_writer.write_soc_forecast(sim_battery_off, scenario="battery_off")
            self.simulation_writer.write_soc_forecast(sim_planned, scenario="planned")
            # Write forecast snapshot for accuracy tracking
            # Only overwrites from NOW onwards — earlier points preserved
            # for comparison with actual SOC
            self.simulation_writer.write_forecast_snapshot(sim_planned)

            # Prime the EV safety cache so the 10-s EV loop can show real
            # values on the dashboard without hitting InfluxDB every cycle.
            # Safe to call even when EV is disabled — method handles missing
            # data with a block-as-precaution default.
            if self.ev_battery_optimizer is not None:
                self._ev_safe, self._battery_min_soc_forecast = (
                    self.ev_battery_optimizer.check_ev_safe()
                )
            # Write energy balance + car SOC forecast for visualization
            self.write_energy_balance(forecast, house_soc=current_soc)
            self.write_decision(decision, current_soc)

            # Control battery (protection flag — combined with EV flag)
            self._discharge_blocked_by_protection = not decision.discharge_allowed
            self._update_discharge_control()

            # Export-peak-shaving charge control (uses self._battery_target_soc,
            # computed early above)
            self.control_battery_charge(current_soc, forecast, gate_forecast, now)

            # Publish combined battery reasoning for the dashboard
            self.publish_battery_decision(decision, current_soc)

            # Calculate appliance signal using full simulation
            # (checks if battery has enough energy to run appliance without grid import)
            self.calculate_appliance_signal(current_soc, sim_battery_on)

        except Exception as e:
            logger.error(f"Optimization failed: {e}", exc_info=True)

    def calculate_appliance_signal(self, current_soc: float, simulation: pd.DataFrame) -> None:
        """Calculate and output appliance signal to Home Assistant.

        Signal logic:
        - GREEN: Immediate PV excess > appliance power (run from solar)
        - ORANGE: Appliance won't cause grid import until 21:00 next evening
        - RED: Appliance would require grid import before 21:00
        """
        try:
            # Get current PV and load from HA
            current_pv = self.ha_client.get_sensor_value(self.pv_power_entity) or 0.0
            current_load = self.ha_client.get_sensor_value(self.load_power_entity) or 0.0
            excess_power = current_pv - current_load

            # Forecast min SOC with appliance load
            load_percent = self._extra_load_percent(self.appliance_energy_wh)
            if not simulation.empty and "soc_percent" in simulation.columns:
                min_soc = simulation["soc_percent"].min() - load_percent
                min_soc = max(0.0, min_soc)
            else:
                min_soc = 0.0

            # GREEN: Current PV excess covers the appliance directly
            if excess_power > self.appliance_power_w:
                signal = ApplianceSignal(
                    signal="green",
                    reason=f"PV excess {int(excess_power)}W > {int(self.appliance_power_w)}W",
                    excess_power_w=excess_power,
                    min_soc_percent=min_soc,
                )
            elif min_soc > 0:
                # ORANGE: appliance won't cause grid import until 21:00
                signal = ApplianceSignal(
                    signal="orange",
                    reason=(f"No grid import needed (min SOC {min_soc:.0f}% with appliance)"),
                    excess_power_w=excess_power,
                    min_soc_percent=min_soc,
                )
            else:
                # RED: appliance would require grid import
                signal = ApplianceSignal(
                    signal="red",
                    reason=(
                        f"Would need grid import "
                        f"(min SOC 0% with −{load_percent:.0f}% appliance load)"
                    ),
                    excess_power_w=excess_power,
                    min_soc_percent=0.0,
                )

            logger.info(f"Appliance signal: {signal.signal} - {signal.reason}")

            # Output to Home Assistant
            self.ha_client.set_sensor_state(
                self.appliance_signal_entity,
                signal.signal,
                attributes={
                    "friendly_name": "Appliance Signal",
                    "reason": signal.reason,
                    "excess_power_w": signal.excess_power_w,
                    "min_soc_percent": signal.min_soc_percent,
                    "icon": "mdi:washing-machine",
                },
            )

            # Write to InfluxDB
            point = (
                Point("appliance_signal")
                .field("signal", signal.signal)
                .field("reason", signal.reason)
                .field("excess_power_w", float(signal.excess_power_w))
                .field("min_soc_percent", float(signal.min_soc_percent))
                .time(datetime.now(UTC), WritePrecision.S)
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
                age = (datetime.now(UTC) - updated).total_seconds()
                if age < 20:
                    return float(state["state"])
                logger.debug(f"M-Bus stale ({age:.0f}s), falling back to DTSU")
            except (ValueError, KeyError):
                pass
        return self.ha_client.get_sensor_value(self.dtsu_grid_power_entity) or 0

    def _extra_load_percent(self, extra_load_wh: float) -> float:
        """Convert extra load in Wh to SOC percentage of battery capacity."""
        if extra_load_wh <= 0 or self.capacity_wh <= 0:
            return 0.0
        return extra_load_wh / self.capacity_wh * 100

    def control_ev_charging(self) -> None:
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
                if (
                    ev_mode_poll != self._last_ev_charging_mode
                    and self._last_ev_charging_mode is not None
                ):
                    logger.info(
                        f"Smart car: mode changed "
                        f"({self._last_ev_charging_mode} → {ev_mode_poll}), polling SOC"
                    )
                    self.update_car_soc()
                    self._last_car_soc_poll = now_mono

                # Car just connected (transition to Preparing from disconnected state)
                elif wb_status == "Preparing" and self._last_wallbox_status not in (
                    "Preparing",
                    "Charging",
                    "SuspendedEV",
                    "SuspendedEVSE",
                    "Finishing",
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

            # Wallbox available = car_ready binary sensor from OCPP server
            car_ready_state = self.ha_client.get_state(self.car_ready_entity)
            wallbox_available = car_ready_state is not None and car_ready_state.get("state") == "on"

            # Read inputs for state machine
            ev_mode = self.ha_client.get_input_select(self.ev_charging_mode_entity)

            # Guard: only off/solar/immediate/cheap are valid. "off" is a real
            # sticky hard-stop (handled in the state machine, FSD 4.3.4); any
            # other value is reset to solar.
            if ev_mode not in ("off", "solar", "immediate", "cheap"):
                logger.warning(f"EV charging mode '{ev_mode}' is invalid, resetting to solar")
                self.ha_client.set_input_select(self.ev_charging_mode_entity, "solar")
                ev_mode = "solar"

            now = datetime.now(UTC)
            tariff = self.optimizer.get_tariff_periods(now)
            grid_power = self._read_grid_power()
            battery_soc = self.ha_client.get_sensor_value(self.soc_entity) or 0

            # User power slider (manual_power for immediate/cheap modes)
            manual_power_raw = self.ha_client.get_sensor_value(self.manual_power_entity)
            manual_power = int(manual_power_raw) if manual_power_raw is not None else ev_max_power

            pv_power = self.ha_client.get_sensor_value(self.pv_power_entity) or 0.0
            load_power = self.ha_client.get_sensor_value(self.load_power_entity) or 0.0
            surplus_power_raw = self.ha_client.get_sensor_value(self.surplus_power_entity) or 0.0

            # Rolling 1-minute average of surplus (6 samples × 10 s)
            self._surplus_samples.append(surplus_power_raw)
            if len(self._surplus_samples) > 3:
                self._surplus_samples.pop(0)
            surplus_power = sum(self._surplus_samples) / len(self._surplus_samples)

            validate_power_readings(grid_w=grid_power, wallbox_w=wallbox_power)

            # Grid export: for dashboard display only (not used in decisions)
            grid_export = max(0.0, grid_power)

            # Step 1: Determine candidate EV power (what we'd charge at)
            # Both rules use surplus_power (PV - house load) as the input.
            # Surplus is independent of wallbox consumption, avoiding the
            # feedback loop where grid_export drops when the wallbox charges.
            ev_charging_power_w = 0.0
            ev_charging_source = "none"
            ev_source_reason = "no solar mode"
            ev_threshold = 0.0
            battery_full_time = None
            # Solar-mode step offset vs the surplus-snapped level (Section 4.3.6):
            # +n = snapped up n steps (home battery bridges the gap), −n = stepped
            # down (preserving the battery), 0 = matched surplus. None when not
            # solar-surplus charging.
            ev_step_offset: int | None = None
            if ev_mode == "solar":
                threshold = (
                    self.ha_client.get_sensor_value(self.ev_min_solar_power_entity) or ev_min_power
                )
                ev_threshold = threshold

                # Rule 2 (Topic 1, FSD 4.3.6): the car must still need charge —
                # its SOC below its own charging limit. Missing SOC → allow (the
                # car BMS stops at its limit anyway).
                car_soc_solar = self.ha_client.get_sensor_value(self.smart_car_soc_entity)
                car_target_solar = self.ha_client.get_sensor_value(self.car_charging_max_entity)
                car_at_target = (
                    car_soc_solar is not None
                    and car_target_solar is not None
                    and car_soc_solar >= car_target_solar
                )

                if car_at_target:
                    ev_charging_source = "none"
                    ev_source_reason = (
                        f"car at target ({car_soc_solar:.0f}% ≥ {car_target_solar:.0f}%)"
                    )
                # Rule 4 full-battery exception: home battery full → capture the
                # otherwise-curtailed surplus, no safety check needed.
                elif battery_soc >= 100 and surplus_power >= threshold and pv_power > 0:
                    ev_charging_power_w = snap_to_power_step(
                        surplus_power,
                        POWER_STEPS_3P[0],
                        POWER_STEPS_3P[-1],
                    )
                    ev_charging_source = "battery_full"
                    ev_source_reason = (
                        f"Surplus {surplus_power:.0f}W → snap {ev_charging_power_w:.0f}W"
                    )
                # Solar-surplus candidate (needs the Rule 4 battery check)
                elif surplus_power >= threshold:
                    ev_charging_source = "solar_surplus"

            # Step 2: Home-battery gate (Rule 5, FSD 4.3.6) + Topic 2 power steps.
            # The car may charge only while the battery is still forecast to reach
            # its target today (will_battery_hit_full); the chosen amp step comes
            # from the Topic 2 candidate list. Re-evaluated each cycle.
            if battery_soc >= 100:
                # Battery already full (Rule 1 set power above). No safety
                # check needed — SOC can only go down from here.
                ev_safe = True
                min_soc_forecast = 100.0
                battery_will_be_full = True
            elif ev_charging_source == "solar_surplus":
                # Target gate (FSD 4.3.6): re-anchor the SOC sim to the LIVE SOC
                # every 10-s cycle (not the 15-min-stale soc_forecast in InfluxDB),
                # so the gate reflects how far the car has actually drained the
                # battery and stops it at the right moment (no ~one-period overshoot).
                battery_will_be_full, _, battery_full_time = (
                    self.optimizer.reaches_target_today(
                        battery_soc,
                        self._latest_forecast,
                        datetime.now(UTC),
                        self._battery_target_soc,
                    )
                )
                ev_min_power = POWER_STEPS_3P[0]
                ev_max_power = POWER_STEPS_3P[-1]
                candidate_power = snap_to_power_step(surplus_power, ev_min_power, ev_max_power)
                # Step-up gate (Topic 2, FSD 4.3.7): step one amp level above
                # surplus (draining the gap from the home battery) only while the
                # battery is still protected from buying over 48 h
                # (battery_min_soc_48h >= floor) AND the *current* SOC is at/above
                # the floor — the 48 h forecast excludes the wallbox load, so it
                # reads optimistically high while the car drains the real battery;
                # the instantaneous-SOC condition stops step-up from draining the
                # home battery below the no-buy floor. Otherwise stay at/below
                # surplus.
                # Topic 1 target gate (FSD 4.3.6): the home battery has priority.
                # When the car-excluded SOC forecast (battery_will_be_full) can
                # no longer reach the charge target today, the car yields all
                # surplus to the battery (no candidates). Re-evaluated each cycle
                # from the car-suppressed current SOC → self-correcting: once the
                # car stops, the battery climbs and reaches (nearly) the target.
                candidates, snap_up_gate_reason = build_solar_candidates(
                    candidate_power=candidate_power,
                    threshold=threshold,
                    step_up_allowed=(
                        self._battery_min_soc_forecast >= self.no_buy_floor_percent
                        and battery_soc >= self.no_buy_floor_percent
                    ),
                    target_reachable=battery_will_be_full,
                )

                # Rule 5 (FSD 4.3.6) is the home-battery gate: it decides whether
                # any candidates exist at all — none means the battery can no
                # longer reach its target today, so the car yields all surplus to
                # the battery. The Topic 2 step-up floor (FSD 4.3.7) has already
                # removed the only candidate that drains the battery (the step-up
                # step) when the instantaneous SOC is below the floor, so the
                # highest remaining candidate is safe to take directly — there is
                # no separate 48 h-forecast veto (the former Rule 4 was superseded
                # by Rule 5). min48h stays computed (cached, for the dashboard and
                # the step-up gate) but is no longer an EV-charge veto.
                min_soc_forecast = self._battery_min_soc_forecast
                ev_safe = bool(candidates)
                if candidates:
                    ev_charging_power_w = candidates[0]
                    ev_step_offset = POWER_STEPS_3P.index(
                        ev_charging_power_w
                    ) - POWER_STEPS_3P.index(candidate_power)
                    if ev_charging_power_w > candidate_power:
                        detail = f", snap-up {candidate_power}→{ev_charging_power_w}W"
                    elif ev_charging_power_w < candidate_power:
                        detail = f", stepped {candidate_power}→{ev_charging_power_w}W"
                    else:
                        detail = ""
                    ev_source_reason = (
                        f"Surplus {surplus_power:.0f}W ≥ {threshold:.0f}W, "
                        f"forecast → {ev_charging_power_w:.0f}W{detail} "
                        f"({snap_up_gate_reason})"
                    )
                else:
                    ev_charging_power_w = 0.0
                    if not battery_will_be_full:
                        ev_source_reason = (
                            f"battery won't reach target "
                            f"{self._battery_target_soc:.0f}% today → "
                            f"car yields surplus to home battery"
                        )
                    else:
                        ev_source_reason = (
                            f"No charging — surplus {surplus_power:.0f}W below "
                            f"available power steps"
                        )
                    ev_charging_source = "none"
            else:
                # No EV candidate (surplus below threshold, mode not solar,
                # or wallbox unplugged). Skip the Influx queries — ~17k
                # unnecessary Flux round-trips per day when idle. Use
                # cached/default values for the dashboard.
                ev_safe = self._ev_safe
                min_soc_forecast = self._battery_min_soc_forecast
                battery_will_be_full = False
                battery_full_time = None
            self._ev_safe = ev_safe
            self._battery_min_soc_forecast = min_soc_forecast

            if ev_charging_source == "none" and ev_mode == "solar":
                # Explain why neither rule fired
                if surplus_power < ev_threshold:
                    ev_source_reason = (
                        f"No charging — surplus {surplus_power:.0f}W < {ev_threshold:.0f}W"
                    )

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

            # Phase 3 — manual-charge budget inputs (immediate/cheap only)
            target_soc_raw = self.ha_client.get_sensor_value(self.ev_target_soc_entity)
            target_soc = float(target_soc_raw) if target_soc_raw is not None else 100.0
            car_soc_raw = self.ha_client.get_sensor_value(self.car_soc_last_known_entity)
            car_soc = float(car_soc_raw) if car_soc_raw is not None else None
            session_wh_raw = self.ha_client.get_sensor_value(self.wallbox_session_energy_entity)
            session_energy_wh = float(session_wh_raw) if session_wh_raw is not None else 0.0
            # Age of the raw smarthashtag reading.  Used for logging only —
            # the SOC stop fires regardless of freshness.  Read against the
            # raw source (not *_last_known, whose last_updated lies when the
            # value stays flat) so the age reflects real polling cadence.
            car_soc_age_s: float | None = None
            raw_soc_state = self.ha_client.get_state(self.smart_car_soc_entity)
            if raw_soc_state and raw_soc_state.get("state") not in (
                "unknown",
                "unavailable",
                "none",
                None,
            ):
                lu = raw_soc_state.get("last_updated")
                if lu:
                    try:
                        ts = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                        car_soc_age_s = (datetime.now(UTC) - ts).total_seconds()
                    except ValueError:
                        pass

            # Build inputs and run state machine
            inputs = EVInputs(
                wallbox_available=wallbox_available,
                wallbox_power_w=wallbox_power,
                wallbox_status=wb_status,
                wallbox_idle=wallbox_idle,
                battery_soc=battery_soc,
                charging_mode=ev_mode,
                is_cheap_tariff=tariff.is_cheap_now,
                grid_power_w=grid_power,
                surplus_power_w=surplus_power,
                pv_power_w=pv_power,
                load_power_w=load_power,
                min_power_w=ev_min_power,
                manual_power_w=manual_power,
                ev_charging_power_w=ev_charging_power_w,
                target_soc=target_soc,
                car_soc=car_soc,
                car_soc_age_s=car_soc_age_s,
                session_energy_wh=session_energy_wh,
                capacity_kwh=self.smart_car_capacity_kwh,
                efficiency=self.smart_car_charge_efficiency,
            )
            prev_ev_state = self._ev_sm.state
            output = self._ev_sm.step(inputs)

            # Dense one-line decision log with all inputs needed to
            # reconstruct the verdict. Deduped: INFO on state/power
            # change, DEBUG otherwise, with a 60 s INFO heartbeat.
            parts = [f"EV [{output.state.value}] {output.target_power_w:.0f}W"]
            parts.append(f"mode={ev_mode}")
            if ev_mode == "solar":
                op = ("≥" if surplus_power >= ev_threshold else "<") if ev_threshold else "="
                parts.append(f"surplus={surplus_power:.0f}W{op}{ev_threshold:.0f}W")
                parts.append(f"batt={battery_soc:.0f}%")
                if ev_charging_source != "none" or ev_safe is False:
                    floor_op = "≥" if min_soc_forecast >= self.ev_reserve_percent else "<"
                    parts.append(
                        f"min48h={min_soc_forecast:.0f}%{floor_op}{self.ev_reserve_percent:.0f}%"
                    )
            else:
                parts.append(f"batt={battery_soc:.0f}%")
            parts.append(f"src={ev_charging_source}")
            ev_log_line = "  ".join(parts)

            signature = (
                output.state.value,
                int(output.target_power_w),
                ev_charging_source,
                ev_safe,
            )
            now_mono = time.monotonic()
            changed = signature != self._ev_log_signature
            heartbeat_due = now_mono - self._ev_log_last_info_monotonic >= 60
            if changed or heartbeat_due:
                logger.info(ev_log_line)
                self._ev_log_signature = signature
                self._ev_log_last_info_monotonic = now_mono
            else:
                logger.debug(ev_log_line)

            # Auto-revert: state machine ended this tick at IDLE while the
            # user-set mode is cheap/immediate, AND either:
            #   - we were charging (prev_ev_state in IMMEDIATE/CHEAP) — covers
            #     wallbox-idle timeout, SOC stop, kWh budget, wallbox unplug;
            #   - the wallbox was available, so the state machine just entered
            #     IMMEDIATE/CHEAP and immediately bounced back to IDLE because
            #     the target was already met.
            # If wallbox is *not* available and we weren't charging, leave mode
            # armed — user pressed Cheap Charge before plugging in.
            if (
                output.state == EVState.IDLE
                and ev_mode in ("immediate", "cheap")
                and (prev_ev_state in (EVState.IMMEDIATE, EVState.CHEAP) or wallbox_available)
            ):
                logger.info(f"Reverting mode to solar — {output.reason}")
                # Button-press bounce: the user just selected immediate/cheap and
                # the state machine returned to IDLE the *same* tick (target/budget
                # already met on entry). Surface it so the silent mode-revert isn't
                # confusing — otherwise the mode just flips back with no explanation.
                if prev_ev_state not in (EVState.IMMEDIATE, EVState.CHEAP):
                    self.ha_client.create_notification(
                        message=(
                            f"{ev_mode.capitalize()} charge not started — "
                            f"{output.reason}. Raise the EV target SOC to charge."
                        ),
                        title="EV charge: target already reached",
                        notification_id="ev_charge_target_reached",
                    )
                self.ha_client.set_input_select(self.ev_charging_mode_entity, "solar")
                self._ev_idle_since = None

            # Send power limit to OCPP (on change only; OCPP server handles re-sends)
            # Rate limit: min 30s between changes to prevent wallbox oscillation
            # at amp-step boundaries. 0W bypasses (safety).
            if output.target_power_w != self._last_ev_power_limit:
                since_last = time.monotonic() - self._last_ev_power_limit_at
                if output.target_power_w == 0 or since_last >= 30:
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
                        self._last_ev_power_limit_at = time.monotonic()
                    else:
                        logger.error("Failed to set wallbox power limit")
                else:
                    logger.debug(
                        f"Rate-limited: want {output.target_power_w:.0f}W "
                        f"but only {since_last:.0f}s since last change"
                    )

            # Discharge blocking: block when IMMEDIATE/CHEAP and power > 0
            should_block = (
                output.state in (EVState.IMMEDIATE, EVState.CHEAP) and output.target_power_w > 0
            )
            if should_block != self._discharge_blocked_by_ev:
                self._discharge_blocked_by_ev = should_block
                self._update_discharge_control()

            # Integration test observer
            if self._observer is not None:
                self._observer.observe(
                    CycleSnapshot(
                        inputs=inputs,
                        output=output,
                        prev_state=prev_ev_state,
                        discharge_blocked_by_ev=self._discharge_blocked_by_ev,
                        last_power_limit_sent=self._last_ev_power_limit,
                        wb_connected=wb_connected,
                        idle_since=self._ev_idle_since,
                        excess_w=(
                            (pv_power - load_power)
                            if battery_soc < 100
                            else (-grid_power + wallbox_power)
                        ),
                        ts=datetime.now(UTC),
                    )
                )

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
                    "reason": ev_source_reason,
                    "ev_charging_rule": ev_charging_source,
                    "battery_soc": battery_soc,
                    "battery_min_soc_forecast_48h": self._battery_min_soc_forecast,
                    "battery_min_soc_floor": self.ev_reserve_percent,
                    "battery_will_be_full": battery_will_be_full,
                    "battery_full_time": battery_full_time,
                    "battery_target_soc": self._battery_target_soc,
                    "shaving_day_mode": self._shaving_day_mode,
                    "car_target_time": self.ev_soc_forecast_full_time,
                    "car_target_soc": self.ev_soc_forecast_target_soc,
                    "ev_safe": self._ev_safe,
                    "threshold_w": ev_threshold,
                    "surplus_power_w": surplus_power,
                    "grid_export_w": grid_export,
                    "snap_power_w": ev_charging_power_w,
                    "ev_step_offset": ev_step_offset,
                    "icon": "mdi:ev-station",
                },
            )

        except Exception as e:
            logger.error(f"EV charging control failed: {e}", exc_info=True)

    def update_car_soc(self) -> None:
        """Read SOC and charging state from smarthashtag HA entities."""
        if not self.smart_car_enabled:
            return

        try:
            soc_raw = self.ha_client.get_sensor_value(self.smart_car_soc_entity)
            if soc_raw is None:
                return

            charger_status = self.ha_client.get_state("sensor.smart_charging_status")
            charging_current = self.ha_client.get_state("sensor.smart_charging_current")
            time_remaining = self.ha_client.get_state("sensor.smart_charging_time_remaining")
            range_state = self.ha_client.get_state("sensor.smart_range")

            charger_str = charger_status.get("state", "unknown") if charger_status else "unknown"
            current_a = float(charging_current.get("state", 0)) if charging_current else 0.0
            time_raw = time_remaining.get("state", "unknown") if time_remaining else "unknown"
            time_min = int(float(time_raw)) if time_raw not in ("unknown", "unavailable") else None
            range_km = int(float(range_state.get("state", 0))) if range_state else 0

            logger.info(
                f"Smart car SOC: {soc_raw}% charger={charger_str} "
                f"current={current_a}A range={range_km}km"
                + (f" time_remaining={time_min}min" if time_min is not None else "")
            )

        except Exception as e:
            logger.error(f"Smart car SOC update failed: {e}")

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

    def _ensure_sensor_exists(self, entity_id: str, default_state, attributes: dict) -> None:
        """Ensure a sensor exists in HA. Restores last value from InfluxDB if missing."""
        existing = self.ha_client.get_state(entity_id)
        if existing is not None:
            logger.debug(f"Sensor {entity_id} exists (state={existing.get('state')})")
            return
        # Try to restore from InfluxDB history
        restored = self._query_last_value(entity_id)
        state = restored if restored is not None else default_state
        logger.info(
            f"Creating sensor {entity_id} with state={state}"
            f"{' (from InfluxDB)' if restored is not None else ' (default)'}"
        )
        self.ha_client.set_sensor_state(entity_id, state, attributes=attributes)

    def _publish_initial_sensors(self) -> None:
        """Ensure all managed sensors exist in HA at startup."""
        try:
            self._ensure_sensor_exists(
                self.ev_target_power_entity,
                0,
                {
                    "friendly_name": "EV Target Power",
                    "unit_of_measurement": "W",
                    "reason": "Waiting for first update",
                    "icon": "mdi:ev-station",
                },
            )
            self._ensure_sensor_exists(
                self.ev_charge_status_entity,
                "unknown",
                {
                    "friendly_name": "EV Charge Status",
                    "status_text": "Waiting for first update",
                    "icon": "mdi:ev-station",
                },
            )
            logger.info("Initial sensor check complete")
        except Exception as e:
            logger.warning(f"Failed to check initial sensors: {e}")

    def write_flows_daily(self) -> None:
        """Write the daily household flows summary (long-term reporting)."""
        try:
            self.flows_daily.write_summary()
        except Exception as e:
            logger.error(f"Daily flows summary failed: {e}", exc_info=True)

    def start(self) -> None:
        """Start the scheduler."""
        logger.info(f"Starting scheduler (every {self.update_interval} minutes)")

        # Ensure all sensors exist in HA before anything else
        self._publish_initial_sensors()

        # Read current EV charging mode from HA (preserve user's choice across restarts)
        if self.ev_charging_enabled:
            try:
                ev_mode = self.ha_client.get_input_select(self.ev_charging_mode_entity)
                logger.info(f"EV charging mode on startup: {ev_mode}")
            except Exception as e:
                logger.warning(f"Failed to read EV charging mode on startup: {e}")

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

        # Daily household flows summary (23:58 local; long-term reporting)
        from apscheduler.triggers.cron import CronTrigger

        self.scheduler.add_job(
            self.write_flows_daily,
            CronTrigger.from_crontab("58 23 * * *", timezone="Europe/Zurich"),
            id="flows_daily",
            name="Daily flows summary",
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
            logger.info(f"Next optimization at {swiss_datetime(job.next_run_time)}")

    def stop(self) -> None:
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
    1. Load defaults from /usr/share/energy-manager/energy-manager.yaml.example
    2. Load user config from /config/energy-manager.yaml (via --config)
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
    defaults_path = Path("/usr/share/energy-manager/energy-manager.yaml.example")
    if defaults_path.exists():
        logger.debug(f"Loading defaults from {defaults_path}")
        with open(defaults_path) as f:
            defaults = yaml.safe_load(f) or {}

    # Load user config (non-secrets from /config/energy-manager.yaml)
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


def main() -> None:
    """Run the EnergyManager add-on (CLI entry point)."""
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
    def shutdown(signum, frame) -> None:
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
