"""Daily household energy-flow summary (long-term reporting).

Writes one `flows_daily` point per day to the infinite-retention
`energy_longterm` bucket at 23:58 local: consumption per consumer (car, lab,
house rest), grid import/export, tariff-aware cost/revenue (EBL calendar via
BatteryOptimizer.expensive_mask), production, autarky, and self-consumption.

Counterpart of swiss-solar-forecast's `pv_daily` (PV physics); this measurement
owns the household flows and money.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)

LONGTERM_BUCKET = "energy_longterm"

# Cumulative energy counters in the HomeAssistant bucket (entity_id, unit-to-kWh divisor)
COUNTERS = {
    "car": ("wallbox_energy", 1000.0),          # Wh, resets per session
    "desk": ("shelly_2pm_white_switch_1_energy", 1.0),
    "bench": ("shelly_2pm_white_switch_0_energy", 1.0),
    "house": ("load_energy", 1.0),              # Shelly 3EM total (excl. car)
}
# Grid import/export are integrated from the M-Bus power signal (`grid_power`,
# positive = export) at the main connection — the WHOLE-SITE exchange including
# the wallbox branch, so cost/autarky reflect what the utility actually meters.
GRID_POWER_ENTITY = "grid_power"
# Daily-resetting production counters
PRODUCTION = {"huawei": "inverter_daily_yield", "enphase": "enphase_energy_today"}


def compute_flows(
    daily_kwh: dict[str, float],
    hourly_import_kwh: pd.Series,
    expensive_mask: pd.Series,
    production_kwh: float,
    ht_chf_kwh: float,
    nt_chf_kwh: float,
    feed_in_chf_kwh: float,
    battery_charge_kwh: float = 0.0,
    battery_discharge_kwh: float = 0.0,
) -> dict:
    """Pure computation of the flows_daily fields (unit-testable).

    hourly_import_kwh and expensive_mask share a (UTC) hourly index.
    battery_charge_kwh/battery_discharge_kwh are the day's counters (used for a
    battery-aware, grid-independent self-consumption).
    """
    car = daily_kwh.get("car", 0.0)
    lab = daily_kwh.get("desk", 0.0) + daily_kwh.get("bench", 0.0)
    house = daily_kwh.get("house", 0.0)
    imp = daily_kwh.get("import", 0.0)
    exp = daily_kwh.get("export", 0.0)

    fields = {
        "car_kwh": round(car, 3),
        "lab_kwh": round(lab, 3),
        "house_rest_kwh": round(max(0.0, house - lab), 3),
        "house_kwh": round(house, 3),
        "import_kwh": round(imp, 3),
        "export_kwh": round(exp, 3),
        "production_kwh": round(production_kwh, 3),
    }

    # Tariff-aware import cost (hourly attribution via the EBL calendar)
    if len(hourly_import_kwh):
        mask = expensive_mask.reindex(hourly_import_kwh.index).fillna(False)
        rates = mask.map({True: ht_chf_kwh, False: nt_chf_kwh})
        fields["import_cost_chf"] = round(float((hourly_import_kwh * rates).sum()), 3)
    else:
        fields["import_cost_chf"] = round(imp * nt_chf_kwh, 3)
    fields["export_revenue_chf"] = round(exp * feed_in_chf_kwh, 3)
    fields["net_cost_chf"] = round(fields["import_cost_chf"] - fields["export_revenue_chf"], 3)

    # Consumption is the directly-metered load (house + car), NOT the grid
    # balance P - E + I: the latter breaks on battery-cycling days and when a
    # grid reading is bad (e.g. a spurious export spike), which can push the
    # ratios out of [0, 1]. Fall back to the balance only if the load meters
    # are missing.
    load = house + car
    consumption = load if load > 0 else max(0.0, production_kwh - exp + imp)
    fields["consumption_kwh"] = round(consumption, 3)
    if consumption > 0:
        fields["autarky"] = round(max(0.0, min(1.0, 1.0 - imp / consumption)), 3)
    if production_kwh > 0:
        # Self-consumed PV = PV that served the load or charged the battery.
        # The battery is PV-only charged, so with metered load and the battery
        # counters this is exact and immune to a bad export reading:
        #   self_pv = load - battery_discharge - import + battery_charge
        self_pv = load - battery_discharge_kwh - imp + battery_charge_kwh
        self_pv = max(0.0, min(production_kwh, self_pv))
        fields["self_consumption"] = round(self_pv / production_kwh, 3)

    return fields


class FlowsDaily:
    """Collects the day's counters and writes the flows_daily point."""

    def __init__(
        self,
        influx_host: str,
        influx_port: int,
        influx_token: str,
        influx_org: str,
        tariff,  # object with expensive_mask(index) -> pd.Series (BatteryOptimizer)
        ht_chf_kwh: float = 0.3202,
        nt_chf_kwh: float = 0.2434,
        feed_in_chf_kwh: float = 0.09,
        ha_bucket: str = "HomeAssistant",
        bucket: str = LONGTERM_BUCKET,
        local_timezone: str = "Europe/Zurich",
    ) -> None:
        self.tariff = tariff
        self.rates = (ht_chf_kwh, nt_chf_kwh, feed_in_chf_kwh)
        self.ha_bucket = ha_bucket
        self.bucket = bucket
        self.local_tz = ZoneInfo(local_timezone)
        self.client = InfluxDBClient(
            url=f"http://{influx_host}:{influx_port}", token=influx_token, org=influx_org
        )
        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    def close(self) -> None:
        self.client.close()

    def _query(self, flux: str) -> pd.DataFrame:
        result = self.query_api.query_data_frame(flux)
        if isinstance(result, list):
            result = pd.concat(result) if result else pd.DataFrame()
        return result

    def _counter_delta(self, entity: str, start: datetime, stop: datetime,
                       every: str = "1d") -> pd.Series:
        """Reset-safe counter increase per window (sum of positive differences)."""
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = stop.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f'''
        from(bucket: "{self.ha_bucket}")
          |> range(start: {s}, stop: {e})
          |> filter(fn: (r) => r.entity_id == "{entity}" and r._field == "value")
          |> difference(nonNegative: true)
          |> aggregateWindow(every: {every}, fn: sum, createEmpty: false)
          |> keep(columns: ["_time", "_value"])
        '''
        try:
            df = self._query(flux)
            if df.empty:
                return pd.Series(dtype=float)
            ser = df.set_index("_time")["_value"].astype(float)
            ser.index = pd.DatetimeIndex(ser.index).tz_convert("UTC")
            return ser
        except Exception as exc:
            logger.error(f"Counter delta failed for {entity}: {exc}")
            return pd.Series(dtype=float)

    def _grid_energy(self, start: datetime, stop: datetime,
                     every: str = "1d") -> tuple[pd.Series, pd.Series]:
        """Integrate the signed M-Bus power into import/export kWh per window.

        `grid_power` positive = export. 1-min mean power summed over each window
        gives W·min; /60000 → kWh. Returns (import_kwh, export_kwh) series.
        """
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = stop.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        def integ(expr: str) -> pd.Series:
            flux = f'''
            from(bucket: "{self.ha_bucket}")
              |> range(start: {s}, stop: {e})
              |> filter(fn: (r) => r.entity_id == "{GRID_POWER_ENTITY}" and r._field == "value")
              |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
              |> map(fn: (r) => ({{r with _value: {expr}}}))
              |> aggregateWindow(every: {every}, fn: sum, createEmpty: false)
              |> map(fn: (r) => ({{r with _value: r._value / 60000.0}}))
              |> keep(columns: ["_time", "_value"])
            '''
            try:
                df = self._query(flux)
                if df.empty:
                    return pd.Series(dtype=float)
                ser = df.set_index("_time")["_value"].astype(float)
                ser.index = pd.DatetimeIndex(ser.index).tz_convert("UTC")
                return ser
            except Exception as exc:
                logger.error(f"Grid integration failed: {exc}")
                return pd.Series(dtype=float)

        imp = integ("if r._value < 0.0 then r._value * -1.0 else 0.0")
        exp = integ("if r._value > 0.0 then r._value else 0.0")
        return imp, exp

    def _counter_max(self, entity: str, start: datetime, stop: datetime) -> float:
        """Day total of a midnight-resetting counter = its max over the day."""
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = stop.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f'''
        from(bucket: "{self.ha_bucket}")
          |> range(start: {s}, stop: {e})
          |> filter(fn: (r) => r.entity_id == "{entity}" and r._field == "value")
          |> max()
        '''
        try:
            df = self._query(flux)
            return float(df["_value"].iloc[0]) if not df.empty else 0.0
        except Exception as exc:
            logger.error(f"Counter max failed for {entity}: {exc}")
            return 0.0

    def _soc_range(
        self, day_start: datetime, day_end: datetime
    ) -> tuple[float | None, float | None]:
        """Daily (min, max) battery SOC as 0–1 fractions.

        Min near the reserve floor = battery ran empty → import → lower autarky.
        Max short of full = battery never fully charged (weak-production day).
        Together they show the battery's daily swing.
        """
        s = day_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = day_end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f'''
        from(bucket: "{self.ha_bucket}")
          |> range(start: {s}, stop: {e})
          |> filter(fn: (r) => r.entity_id == "battery_state_of_capacity" and r._field == "value")
          |> reduce(fn: (r, accumulator) => ({{
                mn: if r._value < accumulator.mn then r._value else accumulator.mn,
                mx: if r._value > accumulator.mx then r._value else accumulator.mx
             }}), identity: {{mn: 100.0, mx: 0.0}})
        '''
        try:
            df = self._query(flux)
            if df.empty:
                return None, None
            return float(df["mn"].iloc[0]) / 100.0, float(df["mx"].iloc[0]) / 100.0
        except Exception as exc:
            logger.error(f"SOC range fetch failed: {exc}")
            return None, None

    def _production_total(self, start: datetime, stop: datetime) -> float:
        total = 0.0
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = stop.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for entity in PRODUCTION.values():
            flux = f'''
            from(bucket: "{self.ha_bucket}")
              |> range(start: {s}, stop: {e})
              |> filter(fn: (r) => r.entity_id == "{entity}" and r._field == "value")
              |> max()
            '''
            try:
                df = self._query(flux)
                if not df.empty:
                    total += float(df["_value"].iloc[0])
            except Exception as exc:
                logger.error(f"Production fetch failed for {entity}: {exc}")
        return total

    def write_summary(self, day_local: datetime | None = None) -> bool:
        """Compute and write the flows_daily point for one local day (default today)."""
        if day_local is None:
            day_local = datetime.now(self.local_tz)
        day_start = day_local.astimezone(self.local_tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)

        daily = {}
        for name, (entity, div) in COUNTERS.items():
            ser = self._counter_delta(entity, day_start, day_end, every="1d")
            daily[name] = float(ser.sum()) / div if len(ser) else 0.0

        # Whole-site grid import/export from the M-Bus power signal
        imp_d, exp_d = self._grid_energy(day_start, day_end, every="1d")
        daily["import"] = float(imp_d.sum()) if len(imp_d) else 0.0
        daily["export"] = float(exp_d.sum()) if len(exp_d) else 0.0

        hourly_import, _ = self._grid_energy(day_start, day_end, every="1h")
        # aggregateWindow stamps window END; tariff attribution uses window START
        if len(hourly_import):
            hourly_import.index = hourly_import.index - pd.Timedelta(hours=1)
        mask = (
            self.tariff.expensive_mask(hourly_import.index)
            if len(hourly_import) else pd.Series(dtype=bool)
        )

        batt_charge = self._counter_max("battery_day_charge", day_start, day_end)
        batt_discharge = self._counter_max("battery_day_discharge", day_start, day_end)

        ht, nt, feed_in = self.rates
        fields = compute_flows(
            daily_kwh=daily,
            hourly_import_kwh=hourly_import,
            expensive_mask=mask,
            production_kwh=self._production_total(day_start, day_end),
            ht_chf_kwh=ht, nt_chf_kwh=nt, feed_in_chf_kwh=feed_in,
            battery_charge_kwh=batt_charge, battery_discharge_kwh=batt_discharge,
        )

        soc_min, soc_max = self._soc_range(day_start, day_end)
        if soc_min is not None:
            fields["battery_min_soc"] = round(soc_min, 3)
        if soc_max is not None:
            fields["battery_max_soc"] = round(soc_max, 3)

        point = Point("flows_daily").time(day_start, WritePrecision.S)
        for k, v in fields.items():
            point = point.field(k, float(v))
        self.write_api.write(bucket=self.bucket, record=point)
        logger.info(
            f"flows_daily written for {day_start.date()}: car {fields['car_kwh']} kWh, "
            f"house {fields['house_kwh']} kWh, net {fields['net_cost_chf']} CHF"
        )
        return True
