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
| GHI | `ASOB_S` | Net shortwave radiation at surface | W/m² |
| Temperature | `T_2M` | Air temperature at 2 m height | K |

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

The panel and plant geometry is defined in `/config/swiss-solar-forecast.yaml`:

```yaml
panels:
  - id: "AE445"
    model: "AE Solar AC-455MH/144V (calibrated 445W)"
    pdc0: 445
    gamma_pdc: -0.0035
  - id: "AE490"
    model: "AE Solar AC-455MH/144V (calibrated 490W)"
    pdc0: 490
    gamma_pdc: -0.0035
  - id: "Generic425"
    model: "Generic 400W (calibrated 425W)"
    pdc0: 425
    gamma_pdc: -0.0035

plants:
  - name: "House"
    location:
      latitude: 47.475053232432145
      longitude: 7.767335653734485
      altitude: 330
      timezone: "Europe/Zurich"
    inverters:
      - name: "EastWest"
        max_power: 10000
        efficiency: 0.98
        strings:
          - { name: "East", azimuth: 103.3, tilt: 15, panel: "AE445", count: 8 }
          - { name: "West", azimuth: 283.3, tilt: 15, panel: "AE490", count: 9 }
      - name: "South"
        max_power: 1500
        efficiency: 0.98
        strings:
          - { name: "SouthFront", azimuth: 193.3, tilt: 70, panel: "Generic425", count: 3 }
          - { name: "SouthBack",  azimuth: 193.3, tilt: 60, panel: "Generic425", count: 2 }
```

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
├─► Extract GHI, Temperature at PV location
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

## 10. Shading correction

### 10.1 Problem

Each PV string (East, West, South) is physically shaded by buildings, trees, and roof edges at
certain sun positions. The pvlib model assumes an unobstructed sky, so a per-hour, per-string
shading factor corrects for the actual obstructions. Shading depends on sun position (elevation +
azimuth), which changes with hour and season: factors calibrated in winter do not apply in summer,
when the sun is higher and shadows fall differently.

### 10.2 Architecture

```
Layer 1: CLEAR-SKY REFERENCE (astronomy, no weather dependency)
  pvlib.clearsky.ineichen → theoretical GHI → PV model (no shading) → clearsky_power_w per string/hour
Layer 2: SUNNY HOUR DETECTION (per hour)
  actual_ghi / clearsky_ghi > 0.85 → hour usable for calibration
Layer 3: SHADING FACTOR
  on sunny hours: shading_factor = actual_power_w / clearsky_power_w  (< 1.0 means shading)
```

Clear-sky GHI is pure astronomy (no forecast involved), so sunny-hour detection and shading
calculation are independent of forecast quality.

### 10.3 Clear-sky reference

`pvlib.clearsky.ineichen()` computes the theoretical maximum GHI for the installation
(47.475°N, 7.767°E, 330 m). The same pipeline (GHI decomposition → POA → PVWatts DC → inverter
efficiency) is run with clear-sky input and **no** shading factors, yielding `clearsky_power_w` per
string. This reference depends only on sun position and atmospheric clarity (Linke turbidity) — not
on forecasts, history, or current shading factors.

### 10.4 Sunny-hour detection

```
ghi_ratio = actual_ghi / clearsky_ghi
sunny = ghi_ratio > 0.85
```

A per-hour decision (a partly cloudy day still contributes sunny hours at noon). Actual GHI comes
from a HomeAssistant sensor or is derived from actual PV production. The 0.85 threshold tolerates
haze and thin cirrus while excluding significant cloud cover.

### 10.5 Shading factor

```
shading_factor[string][hour] = actual_power_w / clearsky_power_w
```

| Factor | Meaning |
|--------|---------|
| 1.00 | No shading — full sky |
| 0.80 | 20% lost to physical shading |
| 0.50 | Heavy shading — half the sky blocked |

Factors are aggregated over the last 10 sunny observations per slot using the median to reject
outliers.

### 10.6 Strings

| String | Azimuth | Tilt | Pattern |
|--------|---------|------|---------|
| East | 103.3° | 15° | Shaded in the morning (buildings to the east), clears by midday |
| West | 283.3° | 15° | Clear in the morning, may shade in late afternoon |
| South | 193.3° | 60°/70° | Least shading (high tilt catches midday sun) |

### 10.7 InfluxDB storage

**Measurement:** `shading_observations` (bucket `pv_forecast`).

| Field | Type | Description |
|-------|------|-------------|
| `string` | tag | East / West / South |
| `date` | tag | YYYY-MM-DD |
| `hour` | int | Local hour (0–23) |
| `clearsky_ghi` | float | Theoretical clear-sky GHI (W/m²) |
| `actual_ghi` | float | Measured GHI (W/m²) |
| `ghi_ratio` | float | actual_ghi / clearsky_ghi |
| `clearsky_power_w` | float | PV model output with clear-sky, no shading |
| `actual_power_w` | float | Actual string production (W) |
| `shading_factor` | float | actual_power / clearsky_power |
| `sun_elevation` | float | Solar elevation angle (°) |
| `sun_azimuth` | float | Solar azimuth angle (°) |
| `is_sunny` | bool | ghi_ratio > 0.85 |

### 10.8 Application and seasonal update

The forecast pipeline applies factors after the clear-sky power is computed:

```
forecasted_power = pvlib_model_output × shading_factor[string][hour]
```

Factors are loaded from `shading_factors.yaml`, refreshed after each evaluation cycle (21:15 daily);
the YAML serves as a cache when InfluxDB is unavailable. A rolling 90-day window of recent sunny
observations (weighted toward recent) tracks the seasonal change in sun elevation. Stored
observations support retrospective analysis: per-string shading profiles, seasonal drift, model
quality (forecast-with-shading vs actual), and validation of the 0.85 sunny threshold.

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

## Changelog

- 2026-06-29: FSD made self-contained — folded the full SwissSolarForecast spec (ICON/STAC pipeline,
  PV config, output schema, calculation pipeline, shading correction) in from the combined
  combined system FSD (since split into per-add-on FSDs).
