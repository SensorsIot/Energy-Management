# EnergyManagement.md
## Functional System Document (FSD)

**Project:** Intelligent energy management with PV, battery, EV, and tariffs
**Location:** Lausen (BL), Switzerland
**Version:** 1.4
**Status:** PV forecast implemented with ensemble uncertainty, codebase cleaned
**Implementation:** Python
**Data storage:** InfluxDB
**Weather/forecast data:** MeteoSwiss ICON-CH1/CH2-EPS (11/21 ensemble members)

---

## 1. Purpose

Create an energy management system that optimizes

- Battery charging/discharging while
  respecting a night-tariff strategy: do not discharge the battery from 21:00-06:00
  if it risks leaving insufficient energy for the next day (till sun is producing enough power), because grid energy is cheaper at night.

- Provide a start signal for the dishwasher (recommended time window)
- Provide control signal (Ampere>6A) for the wallbox to optimize EV charging with solar energy

---

## 2. Goal and how it works

### 2.1 Goal

The goal of this system is cost- and energy-optimized control of a household
energy system consisting of:

- Photovoltaic system
- Battery storage
- Electric vehicle (EV) with wallbox
- Grid connection with time-varying tariffs (night tariff / time-of-use)

The optimization is based on:
- physically grounded PV yield forecasts (pvlib)
- consumption forecasts from historical data
- dynamically estimated system losses (no fixed lump sums)
- a rolling optimizer (Model Predictive Control, MPC)
- a daily, critical LLM analysis with improvement suggestions

The system is deterministic, reproducible, and auditable. An LLM takes on
analysis, explanation, and suggestion functions only.

### 2.2 How it works

1. Ingest current measurements and day-ahead forecasts (PV and load).
2. Apply tariffs, constraints, and device policies from the YAML configuration.
3. Run a rolling-horizon MPC to minimize total cost while meeting constraints.
4. Emit control signals for battery, wallbox, and dishwasher.
5. Store all results in InfluxDB and generate a daily LLM review.

---

## 3. Principles

1. **Deterministic core logic**  
   All numerical calculations (forecasts, optimization) run without an LLM
   and produce identical results for identical inputs.

2. **LLM as co-pilot**  
   The LLM analyzes results, detects patterns, and suggests improvements,
   but it does not autonomously set setpoints or safety-relevant parameters.

3. **InfluxDB as single source of truth**  
   All measurement, forecast, optimization, and analysis results are stored
   as time series in InfluxDB.

4. **Rolling horizon**  
   Decisions are recalculated regularly (e.g., every 5-15 minutes)
   using current measurements and forecast data.

---

## 4. System overview

```
MeteoSwiss ICON-CH2-EPS forecast (STAC API)
            |
            v
      GRIB parser (eccodes)
            |
            v
      PV forecast (pvlib)
            |
            +--> Dynamic loss calibration
            |
            v
     PV power forecast
            |
            v
     Load forecast (InfluxDB)
            |
            v
        Optimizer (MPC)
            |
            v
   Battery & wallbox setpoints
            |
            v
          InfluxDB
            |
            v
      Daily LLM analysis
```

---

## 5. Data basis (InfluxDB)

### 5.1 Measurements (required)

| Measurement        | Description                          | Unit    |
|--------------------|--------------------------------------|---------|
| pv_power_ac        | PV AC power                          | W       |
| house_power        | House consumption                    | W       |
| grid_power         | Grid power (+import / -export)       | W       |
| battery_soc        | Battery state of charge              | %       |
| battery_power      | Battery power                        | W       |
| ev_power           | Wallbox / EV power                   | W       |

### 5.2 Tariff data

| Measurement      | Description                 | Unit      |
|------------------|-----------------------------|-----------|
| tariff_import    | Import electricity price    | CHF/kWh   |
| tariff_export    | Feed-in compensation (opt.) | CHF/kWh   |

Tariffs are time-dependent and known for the planning horizon.

---

## 6. Data Fetching (MeteoSwiss)

This chapter describes how weather forecast data is fetched from MeteoSwiss for
PV power forecasting. The system uses the ICON (ICOsahedral Nonhydrostatic)
numerical weather prediction model data provided as Open Data.

### 6.1 Data Source

**Provider:** MeteoSwiss (Federal Office of Meteorology and Climatology)

**Access:** Open Government Data (OGD) via STAC API (SpatioTemporal Asset Catalog)

**API Endpoint:** `https://data.geo.admin.ch/api/stac/v1`

**Collections:**
- `ch.meteoschweiz.ogd-forecasting-icon-ch1` (ICON-CH1-EPS)
- `ch.meteoschweiz.ogd-forecasting-icon-ch2` (ICON-CH2-EPS)

**Data Format:** GRIB2 (GRIdded Binary, edition 2)

**Grid Type:** Unstructured triangular grid (requires special handling)

### 6.2 ICON Models

Two ICON model variants are used for different forecast horizons:

| Property | ICON-CH1-EPS | ICON-CH2-EPS |
|----------|--------------|--------------|
| Resolution | 1 km | 2.1 km |
| Forecast horizon | 33 hours | 120 hours (5 days) |
| Ensemble members | 11 (1 ctrl + 10 pert) | 21 (1 ctrl + 20 pert) |
| Model runs (UTC) | 00, 03, 06, 09, 12, 15, 18, 21 | 00, 06, 12, 18 |
| Update frequency | Every 3 hours | Every 6 hours |
| Grid points | ~1.1 million | 283,876 |
| Publication delay | ~2.5 hours | ~2.5 hours |

**Model Selection Strategy:**
- **Today's forecast:** Use ICON-CH1-EPS (higher resolution, sufficient horizon)
- **Tomorrow's forecast:** Use ICON-CH2-EPS (longer horizon needed)
- **Multi-day forecast:** Use ICON-CH2-EPS exclusively
- **Hybrid mode:** CH1 for hours 0-33, CH2 for hours 33-48

### 6.3 Variables to Fetch

For PV power forecasting, the following meteorological variables are available:

| Variable | ICON Name | Description | Unit | Mode |
|----------|-----------|-------------|------|------|
| GHI | `ASOB_S` | Net shortwave radiation at surface | W/m² | Lite + Full |
| Temperature | `T_2M` | Air temperature at 2m height | K | Lite + Full |
| DNI | `ASWDIR_S` | Direct shortwave radiation | W/m² | Full only |
| DHI | `ASWDIFD_S` | Diffuse shortwave radiation | W/m² | Full only |
| Wind speed | `U_10M` | Wind speed at 10m height | m/s | Full only |

**Lite Mode (default):** Uses only GHI and Temperature. DNI/DHI are derived
from GHI using the Erbs decomposition model, which is well-established and
introduces only ~5-10% additional uncertainty.

**Full Mode:** Downloads all 5 variables for maximum accuracy but requires
~10x more disk space and download time.

### 6.4 Fetch Schedule

Data fetching is scheduled via systemd timers to run shortly after MeteoSwiss
publishes new model runs:

**ICON-CH1-EPS Fetch Schedule (UTC):**
```
02:30, 05:30, 08:30, 11:30, 14:30, 17:30, 20:30, 23:30
```
(2.5 hours after each model run: 00, 03, 06, 09, 12, 15, 18, 21)

**ICON-CH2-EPS Fetch Schedule (UTC):**
```
02:30, 08:30, 14:30, 20:30
```
(2.5 hours after each model run: 00, 06, 12, 18)

**Fetch Strategy:**
1. Query STAC API to find latest available model run
2. Check if local data is already up-to-date
3. Download all required variables for all ensemble members
4. Download all forecast hours within the model's horizon
5. Save files locally with standardized naming
6. Clean up old runs to save disk space

### 6.5 Data Volume and Optimizations

To minimize download size and storage, several optimizations are applied:

**Lite Mode (default):**
- 2 variables only: GHI (ASOB_S) + Temperature (T_2M)
- All 11 ensemble members included (1 control + 10 perturbed in single file)
- DNI/DHI derived from GHI using Erbs decomposition model
- No overlap: CH1 covers 0-33h, CH2 covers 33-48h only
- Skip past hours: Only download future forecast hours to save bandwidth

**MeteoSwiss File Structure:**
- Control file: Single GRIB message per file (~2 MB)
- Perturbed file: All 10 perturbed members in one file (~22 MB)
- Total: 2 files per variable per hour (control + perturbed)

| Model | Hours | Files | Approx. Size |
|-------|-------|-------|--------------|
| ICON-CH1-EPS | 0-33 | 2 vars × 34 hours × 2 files = 136 files | ~1.6 GB |
| ICON-CH2-EPS | 33-48 | 2 vars × 16 hours × 2 files = 64 files | ~0.5 GB |
| **Total** | 0-48 | 200 files | **~2.1 GB** |

**Skip Past Hours Optimization:**
When fetching, past forecast hours are automatically skipped to save bandwidth:
- At 12:00 local, a 06:00 UTC run has 5 hours of past data (hours 0-4)
- These are skipped, saving ~15-20% download volume
- Use `--all-hours` flag to include past hours if needed for analysis

**Storage Policy:** Only the latest run is kept; older runs are automatically deleted before downloading.

### 6.6 File Naming Convention

Downloaded GRIB files follow this naming pattern:
```
icon-{model}-{YYYYMMDDHHMM}-h{HHH}-{variable}-{member}.grib2
```

**Examples:**
- `icon-ch1-202601060300-h012-asob_s-m00.grib2` (CH1, 03:00 run, hour 12, GHI, control)
- `icon-ch1-202601060300-h012-asob_s-perturbed.grib2` (CH1, 03:00 run, hour 12, GHI, all perturbed)
- `icon-ch2-202601060600-h048-t_2m-m00.grib2` (CH2, 06:00 run, hour 48, temp, control)

**Member naming:**
- `m00` = Control member (single GRIB message)
- `perturbed` = All perturbed members (10 GRIB messages for CH1, 20 for CH2)

**GRIB file structure:**
- Control files contain 1 GRIB message with `perturbationNumber=0`
- Perturbed files contain multiple GRIB messages, each with a unique `perturbationNumber` (1-10 for CH1, 1-20 for CH2)

### 6.7 STAC API Query

The fetch process queries the STAC API with these parameters:

```python
POST https://data.geo.admin.ch/api/stac/v1/search
{
    "collections": ["ch.meteoschweiz.ogd-forecasting-icon-ch1"],
    "forecast:reference_datetime": "2026-01-06T03:00:00Z",
    "forecast:variable": "ASOB_S",
    "forecast:horizon": "P0DT12H00M00S",  # ISO 8601 duration
    "forecast:perturbed": false,  # true for ensemble members
    "limit": 1
}
```

**Horizon format:** ISO 8601 duration `P{days}DT{hours}H{minutes}M{seconds}S`
- Hour 0: `P0DT00H00M00S`
- Hour 12: `P0DT12H00M00S`
- Hour 36: `P1DT12H00M00S`

### 6.8 Fault Tolerance

The fetching system is designed to be fault-tolerant:

**Download failures:**
- Incomplete downloads are saved as `.tmp` files
- Only `.grib2` files are considered complete
- Failed downloads are logged but don't abort the process
- Retry logic with exponential backoff

**Parsing flexibility:**
- Filename parsing supports multiple formats (12/14 digit timestamps)
- Date/time is extracted from GRIB metadata (authoritative source)
- Variable names are matched case-insensitively
- Unknown files are skipped with warnings

**Data availability:**
- System checks for latest available run before downloading
- Falls back to older runs if latest is not yet published
- Partial data sets can still be used (with reduced ensemble size)

### 6.9 Grid Handling

ICON uses an unstructured triangular grid, not a regular lat/lon grid:

**Grid coordinates:**
- Stored in a separate "horizontal constants" GRIB file
- Variables: `tlat` (latitude), `tlon` (longitude) for each grid point
- Coordinates are in radians, converted to degrees

**Value extraction:**
- Find nearest grid point to target location using Euclidean distance
- Cache grid coordinates locally to avoid repeated downloads
- Grid cache location: `/tmp/meteoswiss_grib/grid_coords_{model}.npz`

### 6.10 Ensemble Processing

All ensemble members are fetched to enable uncertainty quantification:

**Ensemble statistics:**
- P10 (10th percentile): Pessimistic estimate, 90% chance of exceeding
- P50 (50th percentile): Median/expected value
- P90 (90th percentile): Optimistic estimate, 10% chance of exceeding

**Processing:**
1. Calculate PV forecast for each ensemble member independently
2. Stack results into array (members × time steps)
3. Compute percentiles across member axis
4. Report spread (P90 - P10) as uncertainty measure

### 6.11 Fetcher and Calculator Separation

The system uses a clear separation between data fetching and forecast calculation:

**Fetcher Programs** (run independently via systemd timers):
- `fetch_icon_ch1.py` - downloads CH1 data (hours 0-33, higher resolution)
- `fetch_icon_ch2.py` - downloads CH2 data (hours 33-48 only, no overlap)

**Calculator Program** (reads local data only):
- `pv_forecast.py` - uses whatever data the fetchers have downloaded
- Fails with error message if required data is missing

**Manual Fetching:**

```bash
# Fetch CH1 data (skips past hours automatically)
python3 fetch_icon_ch1.py

# Fetch CH1 data including past hours (for analysis)
python3 fetch_icon_ch1.py --all-hours

# Fetch CH2 data (hours 33-48, no overlap with CH1)
python3 fetch_icon_ch2.py
```

**Forecast Calculation:**

```bash
# Today's forecast (uses CH1 data only)
python3 pv_forecast.py --today

# Tomorrow's forecast (uses hybrid CH1+CH2 data)
python3 pv_forecast.py --tomorrow

# 48h forecast (uses hybrid CH1+CH2 data)
python3 pv_forecast.py --ensemble
```

**Systemd commands:**
```bash
# Start fetch manually
systemctl start icon-ch1-fetch

# Check status
systemctl status icon-ch1-fetch

# View logs
journalctl -u icon-ch1-fetch -f
```

---

## 7. PV forecast (pvlib, MeteoSwiss)

### 7.1 Meteorological inputs

**Source:** MeteoSwiss ICON-CH2-EPS Open Data via STAC API

**API Configuration:**
- STAC API URL: `https://data.geo.admin.ch/api/stac/v1`
- Collection: `ch.meteoschweiz.ogd-forecasting-icon-ch2`

**Variables used:**

| ICON Variable | Description                    | Unit  | PV Variable |
|---------------|--------------------------------|-------|-------------|
| asob_s        | Net shortwave radiation        | W/m²  | ghi         |
| aswdir_s      | Direct shortwave radiation     | W/m²  | dni         |
| aswdifd_s     | Diffuse shortwave radiation    | W/m²  | dhi         |
| t_2m          | Air temperature at 2m          | K     | temp_air    |
| u_10m         | Wind speed at 10m              | m/s   | wind_speed  |

**ICON Grid:** Unstructured triangular grid (283,876 points). Grid coordinates
(tlat/tlon) are extracted from the horizontal constants file and cached locally.
Nearest-neighbor interpolation is used to extract values at the PV location.

### 7.2 Model chain

- Solar position (pvlib.solarposition)
- Decomposition of GHI -> DNI/DHI if not provided (Erbs model)
- Transposition to plane of array (POA) - isotropic sky model
- Cell temperature model (Faiman)
- DC power (PVWatts model with temperature coefficient)
- AC power (inverter efficiency + clipping)

### 7.3 PV configuration hierarchy

The PV system is configured hierarchically:

```
panels (catalog)
    |
plants (location)
    |
    +-- inverters (max_power, efficiency)
            |
            +-- strings (azimuth, tilt, panel reference, count)
```

**Panels:** Define panel types once, referenced by id in strings.

**Plants:** Physical locations with geographic coordinates. A system can have
multiple plants (e.g., house, garage, carport).

**Inverters:** Define max AC power (clipping) and efficiency. Each inverter
groups one or more strings.

**Strings:** Define orientation (azimuth, tilt), reference a panel type, and
specify the panel count. String DC power is calculated as count × panel pdc0.

### 7.4 Panel types

| ID          | Model                    | Pdc0 (W) | γ (1/K)  |
|-------------|--------------------------|----------|----------|
| AE455       | AE Solar AC-455MH/144V   | 455      | -0.0035  |
| Generic400  | Generic 400W             | 400      | -0.0035  |

### 7.5 Plant configuration

**Plant: House**

| Parameter  | Value                |
|------------|----------------------|
| Latitude   | 47.475053232432145   |
| Longitude  | 7.767335653734485    |
| Altitude   | 330 m                |
| Timezone   | Europe/Zurich        |

**Inverters:**

| Inverter   | Max Power (W) | Efficiency | Strings                    |
|------------|---------------|------------|----------------------------|
| East+West  | 10000         | 0.82       | East, West                 |
| South      | 1500          | 0.80       | South Front, South Back    |

**Strings:**

| String       | Azimuth (°) | Tilt (°) | Panel     | Count | DC Power (W) |
|--------------|-------------|----------|-----------|-------|--------------|
| East         | 90          | 15       | AE455     | 8     | 3640         |
| West         | 270         | 15       | AE455     | 9     | 4095         |
| South Front  | 180         | 70       | Generic400| 3     | 1200         |
| South Back   | 180         | 60       | Generic400| 2     | 800          |

**Total installed:** 9,735 W DC

---

## 8. Dynamic loss modeling

No fixed system losses are used.

### 8.1 Ideal power

Calculation of an ideal, loss-free power:

P_pv_ideal(t)

### 8.2 Loss factor

Comparison with measurement:

k(t) = P_pv_meas(t) / P_pv_ideal(t)

- Plausibility checks and clipping
- Smoothing (time-dependent)

### 8.3 Use in the forecast

P_pv_forecast(t) = k_forecast(t) * P_pv_ideal_forecast(t)

The factor k reflects:
- Soiling
- Snow
- Partial shading
- Real inverter efficiency

---

## 9. Load forecast

### 9.1 Principle

The load forecast uses only historical data
(no leakage, no future information).

### 9.2 Baseline model

- Profiles by weekday and time slot (e.g., 15 min)
- Robust statistics (median, trimmed mean)
- Smoothing over time

P_load_forecast(t) = Profile[weekday, slot]

### 9.3 Short-term adaptation

Scaling/offset based on the last hours to reflect day-specific patterns.

### 9.4 Uncertainty band

Calculation of P10/P50/P90 for risk-aware optimization.

---

## 10. Optimization: battery & wallbox

### 10.1 Decision variables

- Battery power P_bat_set(t)
- Wallbox power P_ev_set(t)

### 10.2 Method

- Rolling horizon MPC
- Planning horizon: 24-48 h
- Compute cycle: 5-15 min

### 10.3 Objective and constraints reference

Objectives, guardrails, and hard constraints are defined in Section 11 and in the
YAML configuration.

---

## 11. Energy Management

### 11.1 Assumptions

- Reliable PV power/energy forecast for today and tomorrow
- Reliable consumption forecast for today and tomorrow
- Current PV production, current consumption, and current battery SoC available

### 11.2 Additional required inputs

All constants and constraints are defined in a YAML configuration file
(path configurable; default `/home/energymanagement/data/energy_management.yaml`).

**Tariffs and grid economics**
- Import tariff schedule (day/night windows and CHF/kWh)
- Export remuneration (feed-in tariff), if export is allowed
- Grid import/export limits (optional but recommended)

**Battery model and limits**
- Usable energy capacity (kWh)
- Min/max SoC guardrails (%)
- Max charge/discharge power (kW)
- Charge/discharge efficiency (round-trip or separate)
- Control interface (power setpoint vs. mode/SoC target)

**EV / wallbox**
- Departure time (deadline) and target energy/SoC
- Max/min charging power (kW)
- Phase switching capability and control granularity
- Control interface (current limit, power limit, pause/resume)

**Dishwasher (deferrable load)**
- Earliest start and latest finish time
- Typical cycle duration and energy use (kWh)
- Allowed actuation method (smart plug, API, or notification only)

**Policy parameters**
- "Tomorrow reserve" rule: minimum SoC or kWh to keep at 06:00
- Night usage rule: avoid discharging from 21:00-06:00 if reserve is at risk

### 11.3 Optimization objectives and guardrails

Primary objective:
- Minimize total electricity cost over a 24-48 h horizon

Secondary objectives:
- Maximize self-consumption
- Preserve battery health (limit deep cycles)
- Ensure EV target energy by departure time

Hard constraints:
- SoC_min <= SoC <= SoC_max
- Battery power limits and efficiencies
- EV charging power limits and deadlines
- Grid import/export limits (if applicable)

### 11.4 Control signals (outputs)

**Battery**
- battery_discharge_allowed (boolean for 21:00-06:00 rule)
- battery_min_soc_target (overnight reserve)
- battery_power_setpoint (optional, if supported)

**Dishwasher**
- dishwasher_start_recommended (boolean)
- dishwasher_start_time (recommended slot)

**Wallbox**
- ev_charging_enabled (boolean)
- ev_power_limit (kW)
- ev_expected_energy_by_departure (diagnostic)

---

## 12. LLM daily review & parameter tuning

### 12.1 Purpose

The LLM creates a daily (e.g., 08:00) critical analysis:

- Forecast vs. reality
- Loss model stability
- Load forecast quality
- Optimizer decisions
- Cost and goal attainment

### 12.2 Analysis contents

1. Data quality (gaps, outliers, time shift)
2. PV forecast bias and daily profiles
3. Load forecast deviations
4. Battery and EV behavior
5. Tariff usage (night tariff)

### 12.3 Improvement suggestions

The LLM provides:
- Concrete recommendations
- Expected impact
- Affected parameters
- Validation steps

### 12.4 Parameter governance

- **Safe auto-tune** (e.g., smoothing factors)
- **Review required** (e.g., SoC reserves)
- **Never auto** (hardware and protection limits)

All changes are made as suggestions (config diffs), not automatically.

---

## 13. Outputs (InfluxDB)

| Measurement                   | Content                           |
|------------------------------|-----------------------------------|
| pv_forecast_power_ac         | PV power forecast                 |
| load_forecast_power          | Load forecast                     |
| optimizer_battery_setpoint   | Battery setpoint                  |
| optimizer_ev_setpoint        | Wallbox setpoint                  |
| daily_llm_report             | Analysis & recommendations (text) |

---

## 14. Success criteria

The system is considered successful when:

- Energy costs decrease compared to a baseline
- EV targets are reliably met
- Battery constraints are respected
- Forecast errors are transparently detected and addressed
- Improvements are traceable and rollbackable

---


---

## 15. Forecast Data Architecture

### 15.1 Overview

The forecast system is split into two components:

1. **Data Fetchers** (scheduled systemd jobs)
   - Download ICON forecast data from MeteoSwiss STAC API
   - Run on fixed schedules matching MeteoSwiss model runs
   - Store data locally in `/home/energymanagement/forecastData/`

2. **Calculation Program** (on-demand)
   - Reads locally cached forecast data
   - Calculates PV power forecast using pvlib
   - Auto-selects optimal model based on forecast horizon

### 15.2 ICON Models

| Model | Resolution | Update Frequency | Horizon | Use Case |
|-------|------------|------------------|---------|----------|
| ICON-CH1-EPS | 1 km | Every 3 hours | 33h | Today / short-term |
| ICON-CH2-EPS | 2.1 km | Every 6 hours | 5 days | Tomorrow / multi-day |

**Model Run Times (UTC):**
- CH1: 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00
- CH2: 00:00, 06:00, 12:00, 18:00

**Data Availability:** ~2.5 hours after model run time

### 15.3 Directory Structure

```
/home/energymanagement/
├── forecastData/
│   ├── icon-ch1/
│   │   └── YYYYMMDDHHMM/          # Latest run only
│   │       ├── metadata.json
│   │       └── *.grib2
│   └── icon-ch2/
│       └── YYYYMMDDHHMM/          # Latest run only
│           ├── metadata.json
│           └── *.grib2
├── fetch_icon_ch1.py              # CH1 fetcher script
├── fetch_icon_ch2.py              # CH2 fetcher script
└── pv_forecast.py                 # Calculation program
```

### 15.4 Systemd Services

**Services:**
- `icon-ch1-fetch.service` - Fetches ICON-CH1-EPS data
- `icon-ch2-fetch.service` - Fetches ICON-CH2-EPS data

**Timers:**
- `icon-ch1-fetch.timer` - Runs at xx:30 UTC (every 3 hours)
- `icon-ch2-fetch.timer` - Runs at 02:30, 08:30, 14:30, 20:30 UTC

**Retry Configuration:**
- On failure: retry after 5 minutes
- Maximum 5 attempts per hour
- Timeout: 10 minutes per attempt

**Commands:**
```bash
# Check timer status
systemctl list-timers --all | grep icon

# View logs
journalctl -u icon-ch1-fetch --since "1 hour ago"

# Manual trigger
systemctl start icon-ch1-fetch
```

### 15.5 Calculation Program Usage

```bash
# Forecast for today (auto-selects CH1)
python pv_forecast.py --today

# Forecast for tomorrow (auto-selects CH2)
python pv_forecast.py --tomorrow

# Specific date
python pv_forecast.py --date 2025-01-15

# Force specific model
python pv_forecast.py --today --model ch2

# Output to CSV
python pv_forecast.py --tomorrow --output forecast.csv
```

---

## 16. Appendix: Energy Management YAML Schema

The constants and constraints for energy management are defined in a single
YAML file. Default path: `/home/energymanagement/data/energy_management.yaml`.

```yaml
energy_management:
  tariffs:
    import:
      windows:
        - name: night
          start: "21:00"
          end: "06:00"
          price_chf_per_kwh: 0.00
        - name: day
          start: "06:00"
          end: "21:00"
          price_chf_per_kwh: 0.00
    export:
      price_chf_per_kwh: 0.00

  grid:
    max_import_kw: 0.0
    max_export_kw: 0.0

  battery:
    usable_kwh: 0.0
    soc_min_pct: 0.0
    soc_max_pct: 0.0
    max_charge_kw: 0.0
    max_discharge_kw: 0.0
    eta_charge: 1.0
    eta_discharge: 1.0
    control_mode: "power_setpoint"  # or "soc_target"

  ev:
    max_charge_kw: 0.0
    min_charge_kw: 0.0
    departure_time: "07:30"
    target_energy_kwh: 0.0
    phase_switching: false
    control_granularity: "current_limit"  # or "power_limit" / "pause_resume"

  dishwasher:
    earliest_start: "09:00"
    latest_finish: "18:00"
    duration_h: 2.0
    energy_kwh: 0.0
    actuation: "notification"  # or "smart_plug" / "api"

  policy:
    overnight_reserve:
      type: "soc_pct"  # or "kwh"
      value: 0.0
      enforce_at: "06:00"
    night_discharge_block:
      start: "21:00"
      end: "06:00"
      allow_if_reserve_ok: true

  horizon:
    planning_hours: 36
    timestep_minutes: 15
```

---

**End of document**
