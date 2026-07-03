# SwissSolarForecast Add-on — Functional Specification

SwissSolarForecast generates probabilistic PV power forecasts for a Swiss PV installation using
MeteoSwiss ICON ensemble weather data and the pvlib solar-modeling library. It produces P10/P50/P90
percentile forecasts per inverter and for the total system, written to InfluxDB for dashboards and
for EnergyManager optimization.

## 1. System context

SwissSolarForecast is one of four independent Home Assistant add-ons in the Energy-Management
suite. Each add-on ships, installs, and runs on its own; they cooperate **only** through InfluxDB
buckets and Home Assistant entities — there are no direct calls between add-ons.

| Add-on | Role | Produces | Consumes |
|--------|------|----------|----------|
| **SwissSolarForecast** (this add-on) | PV production forecast | `pv_forecast` bucket | HA battery SOC (context only) |
| **LoadForecast** | household load forecast | `load_forecast` bucket | HA load history |
| **EnergyManager** | battery / EV / appliance optimization | HA control entities, `energy_manager` bucket | `pv_forecast` + `load_forecast` |
| **OCPP Server** | OCPP 1.6j wallbox bridge | wallbox HA entities | EnergyManager power setpoint |

**This add-on's interfaces:**

- **Output (the contract EnergyManager depends on):** writes the `pv_forecast` bucket, measurement
  `pv_forecast`, at 15-minute resolution. EnergyManager reads `inverter="total"` — the median
  `power_w_p50` / `energy_wh_p50` plus the `p10`/`p90` bands — as its PV input. Full schema in §8.
- **Input (context only):** reads `sensor.battery_state_of_capacity` and
  `number.battery_maximum_discharging_power` from Home Assistant via the Supervisor API and records
  them with each forecast for accuracy tracking. It does not act on them.

## 2. Purpose & scope

In scope:

- Fetch MeteoSwiss ICON-CH1 and ICON-CH2 ensemble GRIB data.
- Parse the weather variables needed for PV modeling.
- Model PV output per plant, inverter, and string with pvlib.
- Produce P10/P50/P90 forecast bands at 15-minute resolution.
- Apply per-string, per-hour shading correction.
- Write PV forecasts to InfluxDB; optionally track forecast accuracy.

Out of scope: load forecasting, battery/EV optimization, and wallbox control (other add-ons).

| Property | Value |
|----------|-------|
| Slug | `swiss-solar-forecast` |
| Architectures | aarch64, amd64, armv7 |
| Timeout | 300 seconds |
| GRIB storage | `/share/swiss-solar-forecast` |

## 3. Architecture

Build/architecture (fetcher/calculator structure, module layout) is HOW — see the Harness:
[`Harness/project/modules/swiss-solar-forecast.md`](../../Harness/project/modules/swiss-solar-forecast.md).
Runtime cadence is in §11; the per-member calculation is in §9.

## 4. MeteoSwiss ICON models

| Property | ICON-CH1-EPS | ICON-CH2-EPS |
|----------|--------------|--------------|
| Resolution | 1 km | 2.1 km |
| Forecast horizon | 33 hours | 120 hours (5 days) |
| Ensemble members | 11 (1 ctrl + 10 pert) | 21 (1 ctrl + 20 pert) |
| Model runs (UTC) | 00, 03, 06, 09, 12, 15, 18, 21 | 00, 06, 12, 18 |
| Publication delay | ~2.5 hours | ~2.5 hours |
| Grid points | ~1.1 million | 283,876 |

**Variables fetched:**

| Variable | ICON name | Description | Unit |
|----------|-----------|-------------|------|
| Net shortwave | `ASOB_S` | Net shortwave radiation at surface (running time-mean) | W/m² |
| Temperature | `T_2M` | Air temperature at 2 m height | K |

**Radiation processing.** ICON radiation fields are running time-means since forecast start and
`ASOB_S` is *net* shortwave (downward minus ground-reflected). The parser recovers usable GHI in
three steps:

1. **De-accumulate** the running mean to per-interval means:
   `interval(h) = avg(h)·h − avg(h_prev)·h_prev`.
2. **Drop the accumulation anchor** (the series' first point — zero at h0, a since-run-start
   average when a series starts mid-run, e.g. CH2 at h33).
3. **Stamp each interval mean at the interval midpoint** — an interval mean stamped at the
   interval end lags the diurnal ramp by half an interval (under-forecasts mornings,
   over-forecasts evenings).

GHI is then `ASOB_S_interval / (1 − albedo)` with ground albedo 0.13 — ICON's effective surface
albedo at this grid cell, set so that clear-day derived GHI integrates to the Ineichen clear-sky
level. The MeteoSwiss local point forecast (`gre000h0`, hourly means stamped at hour end) receives
the same midpoint shift.

**Model selection:** today's forecast uses ICON-CH1-EPS (higher resolution, sufficient horizon);
tomorrow's uses ICON-CH2-EPS (longer horizon). In hybrid mode, CH1 covers hours 0–33 and CH2 covers
hours 33–60. DNI/DHI are derived from GHI with the Erbs decomposition model.

## 5. STAC API integration

- **Provider:** MeteoSwiss (Federal Office of Meteorology and Climatology), Open Government Data.
- **Endpoint:** `https://data.geo.admin.ch/api/stac/v1` (SpatioTemporal Asset Catalog).
- **Collections:** `ch.meteoschweiz.ogd-forecasting-icon-ch1`,
  `ch.meteoschweiz.ogd-forecasting-icon-ch2`.

### 5.1 Query example

```python
POST https://data.geo.admin.ch/api/stac/v1/search
{
    "collections": ["ch.meteoschweiz.ogd-forecasting-icon-ch1"],
    "forecast:reference_datetime": "2026-01-07T03:00:00Z",
    "forecast:variable": "ASOB_S",
    "forecast:horizon": "P0DT12H00M00S",  # ISO 8601 duration
    "forecast:perturbed": false,           # true for ensemble members
    "limit": 1
}
```

Horizon format is the ISO 8601 duration `P{days}DT{hours}H{minutes}M{seconds}S` (hour 0 =
`P0DT00H00M00S`, hour 36 = `P1DT12H00M00S`).

### 5.2 GRIB file naming

```
icon-{model}-{YYYYMMDDHHMM}-h{HHH}-{variable}-{member}.grib2
```

- `icon-ch1-202601070300-h012-asob_s-m00.grib2` — CH1, 03:00 run, hour 12, GHI, control member
- `icon-ch1-202601070300-h012-asob_s-perturbed.grib2` — all perturbed members
- `m00` = control member (single message); `perturbed` = all perturbed members (10 for CH1, 20 for CH2)

### 5.3 Grid handling

ICON uses an unstructured triangular grid, not a regular lat/lon grid:

- Grid coordinates live in a separate "horizontal constants" GRIB file (`tlat`/`tlon` per point,
  stored in radians and converted to degrees).
- The nearest grid point to the target location is found by Euclidean distance.
- Grid coordinates are cached at `/tmp/meteoswiss_grib/grid_coords_{model}.npz` to avoid re-downloads.

### 5.4 Data volume and storage

Lite mode (default) fetches only GHI (`ASOB_S`) and temperature (`T_2M`); DNI/DHI are derived from
GHI. Only future forecast hours are downloaded.

| Model | Hours | Files | Approx. size |
|-------|-------|-------|--------------|
| ICON-CH1-EPS | 0–33 | 2 vars × 34 h × 2 = 136 | ~1.6 GB |
| ICON-CH2-EPS | 33–60 | 2 vars × 28 h × 2 = 112 | ~0.9 GB |
| **Total** | 0–60 | 248 | **~2.5 GB** |

CH2 extends to hour 60 (not 48) so 48 h of coverage survives the CH1/CH2 run-time offset. Only the
latest run is kept; older runs are deleted before each download.

### 5.5 Fault tolerance

- Incomplete downloads are saved as `.tmp`; only `.grib2` files are treated as complete. Failed
  downloads are logged with exponential-backoff retry and never abort the run.
- Filename parsing accepts multiple timestamp formats; date/time is taken from GRIB metadata
  (authoritative); variable names match case-insensitively; unknown files are skipped with warnings.
- The system checks for the latest available run, falls back to an older run if the latest is not
  yet published, and can use a partial data set (with reduced ensemble size).

## 6. PV system configuration

The panel and plant geometry is defined in `/config/swiss-solar-forecast.yaml`. Panel entries
carry **manufacturer ground truth only** — nameplate STC power (`pdc0`, W) and the datasheet
temperature coefficient (`gamma_pdc`, 1/K). Empirical corrections (soiling, shading, seasonal
drift) belong exclusively to the shading-correction layer (§10) and are never folded into panel
data. The installed modules with full datasheet values and sources are listed in
[Appendix A](#appendix-a--installed-pv-modules-ground-truth).

```yaml
panels:
  - id: "AXITEC455"
    model: "AXITEC AC-455MH/144V (AXIpremium XL HC)"
    pdc0: 455
    gamma_pdc: -0.0035
  - id: "LONGI350"
    model: "LONGi LR4-60HPB-350M (Hi-MO 4m)"
    pdc0: 350
    gamma_pdc: -0.0037
  - id: "MB385"
    model: "Meyer Burger White 385 (HJT)"
    pdc0: 385
    gamma_pdc: -0.0026

plants:
  - name: "House"
    location:
      latitude: 47.475053232432145
      longitude: 7.767335653734485
      altitude: 330
      timezone: "Europe/Zurich"
    inverters:
      - name: "EastWest"        # Huawei 10 kW hybrid; per-string DC on PV1 (East) / PV2 (West)
        max_power: 10000
        efficiency: 0.98
        strings:
          - { name: "East", azimuth: 103.3, tilt: 15, panel: "AXITEC455", count: 8 }
          - { name: "West", azimuth: 283.3, tilt: 15, panel: "AXITEC455", count: 9 }
      - name: "South"           # Enphase microinverters, one per panel, 300 W AC each
        max_power: 1500         # 5 micros × 300 W
        efficiency: 0.98
        strings:
          - { name: "SouthFront",  azimuth: 193.3, tilt: 70, panel: "LONGI350", count: 3 }
          - { name: "SouthBack",   azimuth: 193.3, tilt: 30, panel: "LONGI350", count: 1 }
          - { name: "SouthBackMB", azimuth: 193.3, tilt: 30, panel: "MB385",    count: 1 }
```

The two SouthBack panels sit on an adjustable mount; `tilt` reflects the current setting and is
updated in the config when the mount is changed.

**Target — per-panel AC clipping:** each Enphase microinverter clips its panel at 300 W AC. The
model clips at inverter level only (`max_power`), which understates clipping when one panel would
exceed 300 W while others are below. Check: `grep -n "max_power" swiss-solar-forecast/src/pv_model.py`.

## 7. Configuration

### Secrets (Configuration UI)

Entered in **Settings → Add-ons → SwissSolarForecast → Configuration**:

| Secret | Schema | Purpose |
|--------|--------|---------|
| `influxdb_token` | `password` | InfluxDB write/read access (required) |
| `telegram_bot_token` | `password?` | Telegram notifications (optional) |
| `telegram_chat_id` | `str?` | Telegram notification target (optional) |

### Home Assistant API access

The add-on requires `homeassistant_api: true` in `config.yaml` to read battery state with each
forecast (see §1, Input). Values are fetched from the Supervisor REST API
(`http://supervisor/core/api/states/`) and recorded with every forecast write.

### Non-secrets (`/config/swiss-solar-forecast.yaml`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `influxdb.host` | 192.168.0.203 | InfluxDB server IP/hostname |
| `influxdb.port` | 8087 | InfluxDB HTTP port |
| `influxdb.org` | energymanagement | InfluxDB organization |
| `influxdb.bucket` | pv_forecast | Output bucket |
| `location.latitude` | 47.475 | PV installation latitude |
| `location.longitude` | 7.767 | PV installation longitude |
| `location.altitude` | 330 | Altitude (m) |
| `location.timezone` | Europe/Zurich | Local timezone |
| `panels[]` | — | Panel definitions (id, model, pdc0, gamma_pdc) |
| `plants[]` | — | Plant definitions (inverters, strings) |
| `schedule.ch1_cron` | `30 2,5,8,11,14,17,20,23 * * *` | CH1 fetch (UTC, ~2.5 h after model runs) |
| `schedule.ch2_cron` | `45 2,8,14,20 * * *` | CH2 fetch (UTC) |
| `schedule.calculator_interval_minutes` | 15 | Forecast recalculation interval |
| `storage.data_path` | /share/swiss-solar-forecast | GRIB storage path |
| `storage.max_storage_gb` | 3.0 | Storage cap |
| `log_level` | info | Logging level |

## 8. InfluxDB output schema

**Measurement:** `pv_forecast` (bucket `pv_forecast`), 15-minute intervals aligned to :00/:15/:30/:45.

**Tags:**

| Tag | Values | Description |
|-----|--------|-------------|
| `inverter` | `total`, `EastWest`, `South` | Inverter identifier |
| `model` | `ch1`, `ch2`, `hybrid` | ICON model used |

**Fields (`inverter="total"`):**

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | PV power (pessimistic, 90% chance to exceed) |
| `power_w_p50` | W | PV power (expected / median) |
| `power_w_p90` | W | PV power (optimistic, 10% chance to exceed) |
| `energy_wh_p10/p50/p90` | Wh | Per-period energy (pessimistic / expected / optimistic) |
| `ghi` | W/m² | Global horizontal irradiance |
| `temp_air` | °C | Air temperature |
| `run_time` | ISO string | When the forecast was calculated |
| `battery_soc` | % | Battery SOC recorded at forecast time |
| `discharge_power_limit` | W | Max discharge-power setting (0 = blocked) |

**Fields (`inverter="EastWest"` or `"South"`):** `power_w_p10` / `power_w_p50` / `power_w_p90`.

Companion measurements in the same bucket: `pv_forecast_snapshot` (frozen forecast for accuracy
comparison), `pv_accuracy` (accuracy metrics), `shading_observations` (see §10).

## 9. Calculation pipeline

```
For each ensemble member (11 for CH1, 21 for CH2):
├─► Extract ASOB_S, Temperature at PV location
├─► De-accumulate → interval means at midpoints, albedo-compensate → GHI (§4)
├─► Decompose GHI → DNI + DHI (Erbs model)
├─► For each string:
│   ├─► Solar position (lat/lon/time)
│   ├─► Transpose to plane-of-array (azimuth/tilt)
│   ├─► Cell temperature (Faiman model)
│   └─► DC power (PVWatts with γ coefficient)
├─► Sum strings → inverter DC power
├─► Apply inverter efficiency
└─► Clip to max_power → inverter AC power

Stack members → array [members × time_steps] → percentiles P10 / P50 / P90
```

## 10. Calibration — corrections separated from physics

### 10.1 Correction model

The forecast is pure physics (MeteoSwiss weather × ground-truth panel data, §6/§9) multiplied by
three learned corrections, each owned by a distinct physical cause with a distinct signature:

```
forecast[string] = physics[string] × shade[string](sun_az, sun_el) × eff[string](P/P_rated) × gain[string]
```

| Effect | Nature | Correction |
|--------|--------|-----------|
| Clouds | random | **never calibrated** — expressed by the ICON ensemble (P10/P50/P90) |
| Infrastructure shading | fixed function of sun position | `shade` — static map over (azimuth, elevation) bins |
| Model/efficiency deviation | constant in time, varies with power level | `eff` — curve over power-fraction bins |
| Soiling / snow | slow in time, broadband, resets on cleaning | `gain` — per-string scalar, EWMA |

Every correction is neutral (1.0) until observations justify it: with no data the system degrades
gracefully to pure physics. Weather-model error must never be absorbed into a correction — a
systematic sunny-day deviation indicates a physics/config defect to fix at the source.

### 10.2 Observations (daily, 21:15)

For each 15-minute interval of the past day:

1. **Clear-sky reference** — `pvlib.clearsky.ineichen()` GHI for the site, run through the same
   physics pipeline with **all corrections neutral**, yields `clearsky_power_w` per string (DC for
   East/West, AC for South). Pure astronomy + Linke turbidity; independent of forecasts.
2. **Actuals** — East: `inverter_pv_1_power` (DC), West: `inverter_pv_2_power` (DC), South:
   `enphase_power` (AC, whole inverter — the five panels share one sensor, so South is learned at
   inverter level and applied to its strings uniformly).
3. **Sunny gate** — an interval is usable when the whole-system ratio
   `actual_total / clearsky_total` exceeds 0.75 **and** is locally smooth (rolling 3-interval
   standard deviation of the ratio < 0.05). Clouds fail smoothness; fixed shading does not.
   Per-interval gating lets partly-cloudy days contribute their clear intervals.
4. **Observation** — per string: `ratio = actual / clearsky_power` clamped to [0.1, 1.3], recorded
   with solar azimuth/elevation at the interval midpoint and the power fraction
   `clearsky_power / rated_power`. Intervals where the string is clipping (inverter at
   `max_power`) are excluded from `eff`/`gain` learning.

### 10.3 Shade map (infrastructure shading)

- Bins: 10° solar azimuth × 5° solar elevation, per string.
- `shade_raw[bin]` = median observation ratio per bin over a 90-day window, minimum 5 observations.
- Normalization: `shade[bin] = min(1.0, shade_raw[bin] / reference_level)` where `reference_level`
  is the 90th percentile of all populated bins for that string — the unshaded level, so `shade`
  carries geometry only and `eff`/`gain` carry the rest.
- Unpopulated bins are neutral (1.0). The map converges once the sun has swept a bin on any clear
  day and then stays — the same sun position casts the same infrastructure shadow year-round.

### 10.4 Efficiency curve (model deviation)

- Bins: power fraction `P/P_rated` in 10 equal bins, per string.
- `eff[bin]` = median observation ratio per bin (unshaded bins only: `shade ≥ 0.98`), minimum 20
  observations, normalized to 1.0 at the 40–60 % reference band.
- Captures what the datasheet model misses: inverter low-load efficiency, low-light panel
  behaviour, nameplate tolerance. Time-invariant by construction (long-horizon median).

### 10.5 Gain (soiling)

- `gain[string]` = exponentially weighted moving average (7-day time constant) of the daily median
  observation ratio in the reference regime (unshaded bins, power fraction 0.3–0.8), divided by the
  learned `eff` at that band.
- A drop below 0.93 raises a notification (panels likely need cleaning); a step recovery after
  cleaning is expected and requires no intervention.

### 10.6 Application

`pv_model` applies corrections per string and timestep during forecast calculation: `shade` looked
up from the solar position already computed for the transposition, `eff` from the string's
instantaneous power fraction, `gain` as a scalar. South-inverter calibration applies to all three
South strings.

### 10.7 Storage

- **`calibration_observations`** (bucket `pv_forecast`): tags `string`, `snapshot_id`; fields
  `ratio`, `sun_azimuth`, `sun_elevation`, `power_fraction`, `clearsky_power_w`, `actual_power_w`,
  `is_clipping`.
- **`calibration.yaml`** (add-on data dir): the current `shade` map, `eff` curve, and `gain` per
  string — a cache so forecasts keep their corrections when InfluxDB is unreachable; rebuilt after
  each daily learning cycle.

### 10.8 Site shading patterns (reference)

| String | Azimuth | Tilt | Pattern |
|--------|---------|------|---------|
| East | 103.3° | 15° | Shaded in the morning (buildings to the east), clears by midday |
| West | 283.3° | 15° | Clear in the morning, may shade in late afternoon |
| South | 193.3° | 70° (front) / 30° (back, adjustable) | Back pair shaded in the morning **and** evening; mutual row-shading of the back pair by the front row at low sun is part of the same fixed geometry |

## 11. Runtime behavior

| Task | Cadence |
|------|---------|
| ICON-CH1 fetch | Cron (8× daily, after model runs) |
| ICON-CH2 fetch | Cron (4× daily, after model runs) |
| Forecast calculation | Every 15 minutes |
| Accuracy tracking | Optional scheduled evaluation |
| Shading update | Daily (21:15) |

Each calculation: load latest GRIB → parse variables at the grid point → run the PV model per
string/inverter → aggregate per-inverter and total → write to `pv_forecast` → optionally update
accuracy/shading measurements.

## 12. Source files

Source-file layout is HOW — see [`Harness/project/modules/swiss-solar-forecast.md`](../../Harness/project/modules/swiss-solar-forecast.md).

## 13. Dependencies

Requires network access to MeteoSwiss Open Data and correct PV plant metadata. The build dependency
list is HOW — see [`Harness/project/modules/swiss-solar-forecast.md`](../../Harness/project/modules/swiss-solar-forecast.md).

## 14. Grafana queries

Operator dashboard queries are OPERATE — see [`Handbook.md` → Dashboards & queries → SwissSolarForecast](../../Handbook.md#dashboards--queries).

## 15. Failure handling

- Missing weather files are logged and skipped.
- Existing forecast points are overwritten by timestamp/tag identity, not deleted first.
- A missing InfluxDB token logs a warning and prevents writes.
- Notification failures never stop forecast calculation.
- Shading and accuracy features fail independently of core forecast generation.

## 16. Tests and validation

Test approach and invocation are HOW — see [`Harness/project/modules/swiss-solar-forecast.md`](../../Harness/project/modules/swiss-solar-forecast.md)
and the testing hub [`Harness/project/testing.md`](../../Harness/project/testing.md).

**Test cases (radiation processing, §4):**

| Case | Assertion | Test |
|------|-----------|------|
| De-accumulation | Running time-mean series → exact per-interval means | `test_radiation.py::test_deaccumulate_recovers_interval_means` |
| Anchor semantics | First element of a de-accumulated series is an anchor, dropped by callers (h0 and mid-run starts) | `test_radiation.py::test_deaccumulate_first_element_is_anchor_only`, `::test_midpoints_drop_anchor_and_shift_half_interval` |
| No diurnal lag | Interval means stamped at midpoints reproduce a linear ramp exactly (regression: morning under-forecast) | `test_radiation.py::test_midpoints_no_morning_lag` |
| Albedo compensation | GHI = ASOB_S / (1 − albedo), albedo 0.13; compensation raises, never lowers | `test_radiation.py::test_ground_albedo_compensation_factor` |

**Test cases (calibration, §10):**

| Case | Assertion | Test |
|------|-----------|------|
| Shade normalization | Unshaded bins → 1.0 (model deviation goes to eff/gain, not shade); a blocked bin carries pure geometry | `test_calibration.py::test_shade_map_normalized_to_unshaded_level` |
| Minimum evidence | Bins below the observation minimum stay neutral | `test_calibration.py::test_shade_map_requires_min_observations` |
| Gain EWMA | Converges toward the sustained daily ratio with the 7-day time constant | `test_calibration.py::test_gain_tracks_daily_ratio` |
| Clipping exclusion | Clipped observations never disturb eff/gain | `test_calibration.py::test_clipping_excluded_from_eff_and_gain` |
| Application | shade×eff×gain applied per timestep by sun position and power fraction; neutral calibration is the identity | `test_calibration.py::test_apply_calibration_*`, `::test_neutral_calibration_is_identity` |

## Appendix A — Installed PV modules (ground truth)

Reference data for the panel definitions in §6. All electrical values are manufacturer datasheet
values (sources below) — never calibration results.

**Site:** Lausen BL — 47.4751° N, 7.7673° E, 330 m altitude.
**Totals:** 9.52 kWp DC; AC ceiling 10 kW (Huawei) + 1.5 kW (Enphase).

| | East | West | SouthFront | SouthBack | SouthBack |
|---|---|---|---|---|---|
| Module | AXITEC AC-455MH/144V | AXITEC AC-455MH/144V | LONGi LR4-60HPB-350M | LONGi LR4-60HPB-350M | Meyer Burger White 385 |
| Series | AXIpremium XL HC | AXIpremium XL HC | Hi-MO 4m (Black) | Hi-MO 4m (Black) | White (HJT) |
| Count | 8 | 9 | 3 | 1 | 1 |
| Nameplate | 455 Wp | 455 Wp | 350 Wp | 350 Wp | 385 Wp |
| String DC | 3640 W | 4095 W | 1050 W | 350 W | 385 W |
| Cell type | 144 half-cut mono PERC | 144 half-cut mono PERC | 120 half-cut mono PERC | 120 half-cut mono PERC | 120 half-cut HJT, SmartWire |
| γ Pmax | −0.35 %/K | −0.35 %/K | −0.37 %/K | −0.37 %/K | −0.259 %/K |
| NOCT/NMOT | 45 °C | 45 °C | 45 °C | 45 °C | 44 °C |
| STC Vmp / Imp | 41.61 V / 10.94 A | 41.61 V / 10.94 A | 33.3 V / 10.52 A | 33.3 V / 10.52 A | 37.6 V / 10.3 A |
| STC Voc / Isc | 50.34 V / 11.54 A | 50.34 V / 11.54 A | 40.5 V / 11.02 A | 40.5 V / 11.02 A | 44.5 V / 10.9 A |
| Efficiency | 20.9 % | 20.9 % | 18.7 % | 18.7 % | 20.9 % |
| Tolerance | 0/+5 Wp | 0/+5 Wp | 0/+5 W | 0/+5 W | −0/+5 W |
| Azimuth | 103.3° | 283.3° | 193.3° | 193.3° | 193.3° |
| Tilt | 15° | 15° | 70° | 30° (adjustable) | 30° (adjustable) |
| Inverter | Huawei 10 kW, string PV1 | Huawei 10 kW, string PV2 | Enphase micro ×3, 300 W each | Enphase micro, 300 W | Enphase micro, 300 W |
| Actual-power sensor | `sensor.inverter_pv_1_power` (DC) | `sensor.inverter_pv_2_power` (DC) | `sensor.enphase_power` (AC, all 5 panels combined) | ← | ← |

Label note: "PV-R03C" printed on a module label is a junction-box component code, not a module
model. The Meyer Burger label reading "R White 385 M2" is "Meyer Burger® White 385", build
revision M2.

**Datasheet sources:**

- AXITEC AC-455MH/144V: `DB_144zlg_mono XL_HC_MiA_EN_1500V_0.pdf` —
  <https://web.archive.org/web/20220726125928/https://www.axitecsolar.com/sites/default/files/solar_modules_pdf/DB_144zlg_mono%20XL_HC_MiA_EN_1500V_0.pdf>
- LONGi LR4-60HPB 345~365M (V10): <https://midsummerwholesale.co.uk/pdfs/longi-345-360w-datasheet.pdf>
- Meyer Burger White (380–400 Wp): <https://www.meyerburger.com/fileadmin/user_upload/PDFs/Produktdatenblaetter/EN/DS_Meyer_Burger_White_en.pdf>

## Changelog

- 2026-07-03: §6 panel definitions replaced with datasheet ground truth (AXITEC / LONGi /
  Meyer Burger, corrected tilts); calibration values removed from panel data; Appendix A added.
- 2026-06-29: FSD made self-contained — folded the full SwissSolarForecast spec (ICON/STAC pipeline,
  PV config, output schema, calculation pipeline, shading correction) in from the combined
  combined system FSD (since split into per-add-on FSDs).
