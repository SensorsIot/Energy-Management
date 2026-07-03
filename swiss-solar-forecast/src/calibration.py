"""Calibration tracker — corrections separated from physics (FSD §10).

Learns three corrections from clear-sky observations, each owned by a distinct
physical cause:

- shade[string](sun_az, sun_el)  — fixed infrastructure shading (static map)
- eff[string](power fraction)    — model/efficiency deviation (time-invariant curve)
- gain[string]                   — soiling/snow (slow EWMA, resets on cleaning)

Clouds are never calibrated: they belong to the ICON ensemble. Learning compares
actual per-string production against a clear-sky physics reference (pvlib
Ineichen), so it is independent of forecast quality.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pvlib
import yaml
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)

# Shade map bins (degrees)
AZ_BIN_DEG = 10.0
EL_BIN_DEG = 5.0
SHADE_MIN_OBS = 5
SHADE_WINDOW_DAYS = 90
# The unshaded reference level of a string's map: P90 of populated bins
SHADE_REFERENCE_PERCENTILE = 90

# Efficiency curve: power-fraction bins
EFF_NUM_BINS = 10
EFF_MIN_OBS = 20
EFF_REFERENCE_BAND = (0.4, 0.6)
# Bins are treated as unshaded for eff/gain learning above this shade value
UNSHADED_THRESHOLD = 0.98

# Gain (soiling) EWMA
GAIN_REFERENCE_PF = (0.3, 0.8)
GAIN_TIME_CONSTANT_DAYS = 7.0
GAIN_ALERT_THRESHOLD = 0.93

# Sunny gate (per 15-min interval)
SUNNY_MIN_RATIO = 0.75
SUNNY_MAX_ROLLING_STD = 0.05
RATIO_CLAMP = (0.1, 1.3)
CLIP_EXCLUDE_FRACTION = 0.98

# Actual production sensors (HomeAssistant bucket). South is one AC sensor for
# all five panels, so South is learned at inverter level and applied to its
# strings uniformly.
STRING_SENSORS = {
    "East": "inverter_pv_1_power",
    "West": "inverter_pv_2_power",
    "South": "enphase_power",
}


def shade_bin_key(azimuth: float, elevation: float) -> str:
    """Bin a solar position; key format 'az,el' with bin lower edges."""
    az = int(azimuth // AZ_BIN_DEG * AZ_BIN_DEG)
    el = int(elevation // EL_BIN_DEG * EL_BIN_DEG)
    return f"{az},{el}"


def eff_bin_index(power_fraction: float) -> int:
    return min(EFF_NUM_BINS - 1, max(0, int(power_fraction * EFF_NUM_BINS)))


class CalibrationTracker:
    """Builds calibration observations and maintains the correction maps."""

    def __init__(
        self,
        influx_host: str,
        influx_port: int,
        influx_token: str,
        influx_org: str,
        pv_bucket: str,
        ha_bucket: str,
        plants: list[dict],
        data_dir: Path,
        local_timezone: str = "Europe/Zurich",
    ) -> None:
        self.influx_host = influx_host
        self.influx_port = influx_port
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.pv_bucket = pv_bucket
        self.ha_bucket = ha_bucket
        self.plants = plants
        self.local_tz = ZoneInfo(local_timezone)
        self.yaml_path = Path(data_dir) / "calibration.yaml"

        loc = plants[0]["location"]
        self.location = pvlib.location.Location(
            loc["latitude"], loc["longitude"],
            altitude=loc.get("altitude", 0), tz="UTC",
        )

        self.client: InfluxDBClient | None = None
        self.write_api = None
        self.query_api = None

    # ------------------------------------------------------------------ setup

    def connect(self) -> None:
        url = f"http://{self.influx_host}:{self.influx_port}"
        self.client = InfluxDBClient(url=url, token=self.influx_token, org=self.influx_org)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()
        logger.info(f"CalibrationTracker connected to InfluxDB at {url}")

    def close(self) -> None:
        if self.client:
            self.client.close()

    # ------------------------------------------------- clear-sky reference

    def _string_specs(self) -> dict[str, dict]:
        """Rated power and learning group per learnable unit.

        East/West are learned per string against DC sensors; South against its
        single AC sensor at inverter level.
        """
        specs = {}
        for plant in self.plants:
            for inverter in plant["inverters"]:
                if inverter["name"] == "EastWest":
                    for s in inverter["strings"]:
                        specs[s["name"]] = {
                            "kind": "dc_string",
                            "rated": s["dc_power"],
                            "strings": [s],
                            "inverter": inverter,
                        }
                else:
                    specs[inverter["name"]] = {
                        "kind": "ac_inverter",
                        "rated": inverter["max_power"],
                        "strings": list(inverter["strings"]),
                        "inverter": inverter,
                    }
        return specs

    def clearsky_reference(self, index: pd.DatetimeIndex) -> pd.DataFrame:
        """Clear-sky power per learnable unit with all corrections neutral.

        East/West: string DC. South: inverter AC (efficiency + clip applied).
        """
        from src import pv_model

        cs = self.location.get_clearsky(index, model="ineichen")
        weather = pd.DataFrame(
            {"ghi": cs["ghi"].values, "temp_air": 20.0, "wind_speed": 2.0},
            index=index,
        )
        loc = self.plants[0]["location"]

        out = {}
        for name, spec in self._string_specs().items():
            dc_total = None
            for s in spec["strings"]:
                dc = pv_model.forecast_string_dc_power(
                    weather, s,
                    loc["latitude"], loc["longitude"],
                    loc.get("altitude", 0), loc["timezone"],
                )
                dc_total = dc if dc_total is None else dc_total + dc
            if spec["kind"] == "ac_inverter":
                inv = spec["inverter"]
                out[name] = np.clip(
                    dc_total.values * inv["efficiency"], 0, inv["max_power"]
                )
            else:
                out[name] = dc_total.values
        return pd.DataFrame(out, index=index)

    # ------------------------------------------------------------ actuals

    def fetch_actuals(self, start: datetime, stop: datetime) -> pd.DataFrame:
        """15-min mean actual power per learnable unit from the HA bucket.

        aggregateWindow stamps each window at its END; the caller accounts for
        that by evaluating references at window midpoints.
        """
        frames = {}
        for name, entity in STRING_SENSORS.items():
            start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
            stop_s = stop.strftime("%Y-%m-%dT%H:%M:%SZ")
            query = f'''
            from(bucket: "{self.ha_bucket}")
              |> range(start: {start_s}, stop: {stop_s})
              |> filter(fn: (r) => r.entity_id == "{entity}" and r._field == "value")
              |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
              |> keep(columns: ["_time", "_value"])
            '''
            try:
                result = self.query_api.query_data_frame(query)
                if isinstance(result, list):
                    result = pd.concat(result) if result else pd.DataFrame()
                if result.empty:
                    logger.warning(f"No actuals for {entity}")
                    continue
                s = result.set_index("_time")["_value"].astype(float)
                s.index = pd.DatetimeIndex(s.index).tz_convert("UTC")
                frames[name] = s
            except Exception as e:
                logger.error(f"Failed to fetch actuals for {entity}: {e}")
        return pd.DataFrame(frames)

    # ------------------------------------------------------- observations

    def build_observations(self, day_local: datetime | None = None) -> pd.DataFrame:
        """Build calibration observations for one local day (default: today)."""
        if day_local is None:
            day_local = datetime.now(self.local_tz)
        day_start = day_local.astimezone(self.local_tz).replace(
            hour=4, minute=0, second=0, microsecond=0
        )
        day_end = day_start.replace(hour=22)
        start_utc = day_start.astimezone(UTC)
        stop_utc = day_end.astimezone(UTC)

        actuals = self.fetch_actuals(start_utc, stop_utc)
        if actuals.empty:
            logger.warning("No actual data — skipping calibration observations")
            return pd.DataFrame()

        # aggregateWindow stamps window ends; evaluate astronomy at midpoints
        midpoints = actuals.index - pd.Timedelta(minutes=7.5)
        reference = self.clearsky_reference(pd.DatetimeIndex(midpoints))
        reference.index = actuals.index
        solpos = self.location.get_solarposition(pd.DatetimeIndex(midpoints))
        solpos.index = actuals.index

        specs = self._string_specs()
        units = [u for u in specs if u in actuals.columns and u in reference.columns]
        if not units:
            return pd.DataFrame()

        # Sunny gate on the whole system: level + smoothness
        total_actual = actuals[units].sum(axis=1)
        total_ref = reference[units].sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            total_ratio = total_actual / total_ref
        total_ratio = total_ratio.replace([np.inf, -np.inf], np.nan)
        rolling_std = total_ratio.rolling(3, center=True, min_periods=2).std()
        sunny = (total_ratio > SUNNY_MIN_RATIO) & (rolling_std < SUNNY_MAX_ROLLING_STD)

        # System-level clipping (Huawei AC cap) excludes eff/gain learning
        ew_inverter = specs.get("East", {}).get("inverter")
        rows = []
        snapshot_id = day_start.strftime("%Y-%m-%d")
        for ts in actuals.index:
            if not bool(sunny.get(ts, False)):
                continue
            el = float(solpos.loc[ts, "elevation"])
            az = float(solpos.loc[ts, "azimuth"])
            if el <= 0:
                continue
            for unit in units:
                ref = float(reference.loc[ts, unit])
                act = float(actuals.loc[ts, unit]) if not pd.isna(actuals.loc[ts, unit]) else None
                if ref < 50 or act is None:
                    continue
                ratio = float(np.clip(act / ref, *RATIO_CLAMP))
                rated = specs[unit]["rated"]
                pf = min(1.0, ref / rated)
                if specs[unit]["kind"] == "ac_inverter":
                    clipping = ref >= CLIP_EXCLUDE_FRACTION * rated
                else:
                    ew_ref = float(reference.loc[ts, "East"] + reference.loc[ts, "West"])
                    ew_cap = ew_inverter["max_power"] / ew_inverter["efficiency"]
                    clipping = ew_ref >= CLIP_EXCLUDE_FRACTION * ew_cap
                rows.append({
                    "time": ts,
                    "string": unit,
                    "snapshot_id": snapshot_id,
                    "ratio": ratio,
                    "sun_azimuth": az,
                    "sun_elevation": el,
                    "power_fraction": pf,
                    "clearsky_power_w": ref,
                    "actual_power_w": act,
                    "is_clipping": bool(clipping),
                })

        df = pd.DataFrame(rows)
        logger.info(
            f"Built {len(df)} calibration observations for {snapshot_id} "
            f"({int(sunny.sum())} sunny intervals)"
        )
        return df

    def store_observations(self, obs: pd.DataFrame) -> int:
        if obs.empty:
            return 0
        points = []
        for _, r in obs.iterrows():
            points.append(
                Point("calibration_observations")
                .tag("string", r["string"])
                .tag("snapshot_id", r["snapshot_id"])
                .field("ratio", float(r["ratio"]))
                .field("sun_azimuth", float(r["sun_azimuth"]))
                .field("sun_elevation", float(r["sun_elevation"]))
                .field("power_fraction", float(r["power_fraction"]))
                .field("clearsky_power_w", float(r["clearsky_power_w"]))
                .field("actual_power_w", float(r["actual_power_w"]))
                .field("is_clipping", bool(r["is_clipping"]))
                .time(r["time"].to_pydatetime(), WritePrecision.S)
            )
        self.write_api.write(bucket=self.pv_bucket, record=points)
        logger.info(f"Stored {len(points)} calibration observations")
        return len(points)

    def fetch_observations(self, days: int = SHADE_WINDOW_DAYS) -> pd.DataFrame:
        query = f'''
        from(bucket: "{self.pv_bucket}")
          |> range(start: -{days}d)
          |> filter(fn: (r) => r._measurement == "calibration_observations")
          |> pivot(rowKey: ["_time", "string"], columnKey: ["_field"], valueColumn: "_value")
        '''
        try:
            result = self.query_api.query_data_frame(query)
            if isinstance(result, list):
                result = pd.concat(result) if result else pd.DataFrame()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch calibration observations: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------- rebuild

    @staticmethod
    def build_maps(obs: pd.DataFrame, previous: dict | None = None) -> dict:
        """Rebuild shade / eff / gain per learnable unit from observations.

        Pure function of the observation set (+ previous gain state for the
        EWMA) so it is unit-testable without InfluxDB.
        """
        previous = previous or {}
        calibration: dict[str, dict] = {}
        if obs.empty:
            return calibration

        for unit, g in obs.groupby("string"):
            # --- shade map: median ratio per (az, el) bin, normalized to the
            # unshaded level so it carries geometry only
            g = g.copy()
            g["bin"] = [
                shade_bin_key(a, e)
                for a, e in zip(g["sun_azimuth"], g["sun_elevation"], strict=True)
            ]
            per_bin = g.groupby("bin")["ratio"].agg(["median", "count"])
            populated = per_bin[per_bin["count"] >= SHADE_MIN_OBS]
            shade: dict[str, float] = {}
            if not populated.empty:
                ref_level = float(np.percentile(
                    populated["median"], SHADE_REFERENCE_PERCENTILE
                ))
                if ref_level > 0:
                    for b, row in populated.iterrows():
                        shade[b] = round(min(1.0, float(row["median"]) / ref_level), 3)

            # --- eff curve: unshaded, non-clipping observations only
            unshaded_bins = {b for b, f in shade.items() if f >= UNSHADED_THRESHOLD}
            not_clipping = ~g["is_clipping"].astype(bool)
            in_unshaded = g["bin"].isin(unshaded_bins) if shade else True
            ge = g[not_clipping & in_unshaded]
            eff = [1.0] * EFF_NUM_BINS
            ref_band = ge[
                (ge["power_fraction"] >= EFF_REFERENCE_BAND[0])
                & (ge["power_fraction"] < EFF_REFERENCE_BAND[1])
            ]["ratio"]
            ref_value = float(ref_band.median()) if len(ref_band) >= EFF_MIN_OBS else None
            if ref_value and ref_value > 0:
                ge_bins = ge.copy()
                ge_bins["pf_bin"] = [eff_bin_index(p) for p in ge_bins["power_fraction"]]
                for b, gb in ge_bins.groupby("pf_bin"):
                    if len(gb) >= EFF_MIN_OBS:
                        eff[int(b)] = round(float(gb["ratio"].median()) / ref_value, 3)

            # --- gain: EWMA over daily medians in the reference regime
            gain_prev = previous.get(unit, {}).get("gain", 1.0)
            gr = ge[
                (ge["power_fraction"] >= GAIN_REFERENCE_PF[0])
                & (ge["power_fraction"] < GAIN_REFERENCE_PF[1])
            ]
            gain = gain_prev
            if not gr.empty:
                daily = gr.groupby("snapshot_id")["ratio"].median().sort_index()
                alpha = 1.0 - float(np.exp(-1.0 / GAIN_TIME_CONSTANT_DAYS))
                gain = 1.0  # re-run the EWMA over the window for determinism
                for _, day_ratio in daily.items():
                    # divide out the eff at the reference band (normalized to 1.0)
                    gain = gain + alpha * (float(day_ratio) - gain)
                gain = round(gain, 3)

            calibration[unit] = {"shade": shade, "eff": eff, "gain": gain}

        return calibration

    # -------------------------------------------------------------- state

    def write_yaml(self, calibration: dict) -> None:
        payload = {
            "calibration": {
                "description": (
                    "Learned corrections (FSD §10): shade / eff / gain "
                    "per string. Neutral (absent) entries mean 1.0."
                ),
                "units": calibration,
            }
        }
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.yaml_path, "w") as f:
            yaml.safe_dump(payload, f, sort_keys=True)
        logger.info(f"Calibration written to {self.yaml_path}")

    def load_calibration(self) -> dict:
        """Load calibration and expand it to per-string application form.

        South's inverter-level calibration is applied to all South strings.
        """
        try:
            if not self.yaml_path.exists():
                return {}
            with open(self.yaml_path) as f:
                data = yaml.safe_load(f) or {}
            units = data.get("calibration", {}).get("units", {})
        except Exception as e:
            logger.warning(f"Failed to load calibration YAML: {e}")
            return {}

        expanded = {}
        for plant in self.plants:
            for inverter in plant["inverters"]:
                for s in inverter["strings"]:
                    cal = units.get(s["name"]) or units.get(inverter["name"])
                    if cal:
                        expanded[s["name"]] = cal
        return expanded

    def learn_daily(self) -> bool:
        """Daily learning cycle (21:15): observe today, rebuild maps, cache."""
        obs_today = self.build_observations()
        if not obs_today.empty:
            self.store_observations(obs_today)

        all_obs = self.fetch_observations()
        if all_obs.empty:
            logger.info("No calibration observations yet — corrections stay neutral")
            return False

        previous = {}
        try:
            if self.yaml_path.exists():
                with open(self.yaml_path) as f:
                    previous = (yaml.safe_load(f) or {}).get("calibration", {}).get("units", {})
        except Exception:
            previous = {}

        calibration = self.build_maps(all_obs, previous)
        self.write_yaml(calibration)

        for unit, cal in calibration.items():
            if cal["gain"] < GAIN_ALERT_THRESHOLD:
                try:
                    from src.notifications import notify_warning
                    notify_warning(
                        "PV gain low",
                        f"{unit} gain {cal['gain']:.2f} < {GAIN_ALERT_THRESHOLD} "
                        "— panels may need cleaning",
                    )
                except Exception:
                    pass
        return True


def create_calibration_tracker(
    options: dict, plants: list[dict], data_dir: Path
) -> CalibrationTracker:
    influx = options.get("influxdb", {})
    return CalibrationTracker(
        influx_host=influx.get("host", "192.168.0.203"),
        influx_port=influx.get("port", 8087),
        influx_token=influx.get("token", ""),
        influx_org=influx.get("org", "energymanagement"),
        pv_bucket=influx.get("bucket", "pv_forecast"),
        ha_bucket=influx.get("ha_bucket", "HomeAssistant"),
        plants=plants,
        data_dir=data_dir,
        local_timezone=options.get("location", {}).get("timezone", "Europe/Zurich"),
    )
