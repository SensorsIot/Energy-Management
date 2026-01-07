# Energy Management System
## Functional Specification Document (FSD)

**Project:** Intelligent energy management with PV, battery, EV, and tariffs
**Location:** Lausen (BL), Switzerland
**Version:** 2.0
**Status:** Active Development
**Architecture:** 3 Home Assistant Add-ons
**Data Storage:** InfluxDB

---

# Chapter 1: System Overview

## 1.1 Purpose

This document describes an intelligent energy management system that optimizes household energy usage through:

- **PV Power Forecasting** - Probabilistic solar production forecasts (P10/P50/P90)
- **Load Forecasting** - Statistical consumption predictions based on historical patterns
- **Energy Optimization** - MPC-based control of battery, EV charging, and deferrable loads

The system minimizes electricity costs while maximizing self-consumption and respecting device constraints.

## 1.2 Architecture Overview

The system consists of three Home Assistant add-ons that work together:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Home Assistant                                   │
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │ SwissSolarFore- │  │   LoadForecast  │  │    EnergyOptimizer      │  │
│  │      cast       │  │                 │  │        (MPC)            │  │
│  │                 │  │                 │  │                         │  │
│  │ PV P10/P50/P90  │  │ Load P10/P50/P90│  │  Battery/EV/Dishwasher  │  │
│  │    Forecasts    │  │    Forecasts    │  │     Control Signals     │  │
│  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────┘  │
│           │                    │                        │               │
│           ▼                    ▼                        ▼               │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                         InfluxDB                                  │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │  │
│  │  │ pv_forecast  │  │load_forecast │  │     HomeAssistant      │  │  │
│  │  │              │  │              │  │    (measurements)      │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## 1.3 Add-on Summary

| Add-on | Version | Purpose | Update Frequency |
|--------|---------|---------|------------------|
| **SwissSolarForecast** | 1.0.1 | PV power forecasting using MeteoSwiss ICON ensemble data | Every 15 min (calculator) |
| **LoadForecast** | 1.0.1 | Statistical load consumption forecasting | Every hour |
| **EnergyOptimizer** | Planned | MPC-based battery/EV/load optimization | Every 5-15 min |

## 1.4 Data Flow

```
MeteoSwiss STAC API                    InfluxDB (HomeAssistant bucket)
        │                                        │
        │ GRIB weather data                      │ Historical load_power
        ▼                                        ▼
┌─────────────────┐                    ┌─────────────────┐
│SwissSolarForecast│                    │   LoadForecast  │
│                 │                    │                 │
│ • Fetch ICON    │                    │ • Query 90 days │
│ • Parse GRIB    │                    │ • Build profile │
│ • Calculate PV  │                    │ • Generate 48h  │
│   with pvlib    │                    │   forecast      │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         │ Write to pv_forecast                 │ Write to load_forecast
         ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            InfluxDB                                  │
│                                                                      │
│  pv_forecast bucket          load_forecast bucket                   │
│  • power_w_p10/p50/p90       • energy_wh_p10/p50/p90               │
│  • energy_wh_p10/p50/p90     • Per 15-min periods                  │
│  • Per-inverter data         • 48h horizon                          │
│  • 48h horizon                                                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               │ Query forecasts + measurements
                               ▼
                    ┌─────────────────────┐
                    │  EnergyOptimizer    │
                    │       (MPC)         │
                    │                     │
                    │ • Read forecasts    │
                    │ • Read current SOC  │
                    │ • Apply tariffs     │
                    │ • Optimize 24-48h   │
                    │ • Output setpoints  │
                    └──────────┬──────────┘
                               │
                               ▼
                    Battery / Wallbox / Dishwasher
                         Control Signals
```

## 1.5 Infrastructure

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| Home Assistant | 192.168.0.202 | 8123 | Device integration, add-on host |
| InfluxDB | 192.168.0.203 | 8087 | Time series storage |
| Grafana | 192.168.0.203 | 3000 | Visualization |
| MQTT Broker | 192.168.0.203 | 1883 | IoT messaging (Enphase) |

## 1.6 InfluxDB Buckets

| Bucket | Source | Content | Retention |
|--------|--------|---------|-----------|
| `HomeAssistant` | HA Integration | Real-time measurements | Long-term |
| `pv_forecast` | SwissSolarForecast | PV forecasts P10/P50/P90 | 30 days |
| `load_forecast` | LoadForecast | Load forecasts P10/P50/P90 | 30 days |

## 1.7 Physical System

### 1.7.1 PV Installation

| Inverter | Panels | DC Power | Max AC | Orientation |
|----------|--------|----------|--------|-------------|
| EastWest (Huawei Sun2000) | 17× AE455 | 7,735 W | 10,000 W | East (8) + West (9) |
| South (Enphase IQ7+) | 5× Generic400 | 2,000 W | 1,500 W | South facade |
| **Total** | 22 panels | 9,735 W | 11,500 W | |

### 1.7.2 Energy Storage

| Component | Specification |
|-----------|--------------|
| Battery | Huawei LUNA 2000 |
| Usable Capacity | ~10 kWh |
| Max Charge/Discharge | 5 kW |

### 1.7.3 Electrical Topology

```
                                GRID
                                  │
                      ┌───────────────────────┐
                      │    EBL Smartmeter     │  Grid connection point
                      └───────────────────────┘
                                  │
                      ┌───────────────────────┐
                      │      Wallbox          │  EV charging
                      └───────────────────────┘
                                  │
                      ┌───────────────────────┐
                      │   Huawei Smartmeter   │  sensor.power_meter_active_power
                      │   (DTSU666-H)         │
                      └───────────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────────┐    ┌───────────────┐
│  Huawei Sun2000 │    │    House Loads      │    │   Enphase     │
│    Inverter     │    │                     │    │ Microinverters│
│  ┌───────────┐  │    │  ┌───────────────┐  │    │  (3× IQ7+)    │
│  │  Battery  │  │    │  │  Shelly 3EM   │  │    └───────────────┘
│  │  (LUNA)   │  │    │  │  (CT clamps)  │  │
│  └───────────┘  │    │  └───────────────┘  │
└─────────────────┘    └─────────────────────┘
```

## 1.8 Key Measurements

### Power (Real-time, W)

| Entity | Description | Source |
|--------|-------------|--------|
| `sensor.solar_pv_total_ac_power` | Total PV AC output | Huawei + Enphase |
| `sensor.battery_charge_discharge_power` | Battery flow (+/-) | Huawei |
| `sensor.power_meter_active_power` | Grid flow (neg=export) | Huawei DTSU |
| `sensor.load_power` | House consumption | Calculated |
| `sensor.phase_1/2/3_power` | Per-phase load | Shelly 3EM |

### State

| Entity | Description | Unit |
|--------|-------------|------|
| `sensor.battery_state_of_capacity` | Battery SOC | % |

## 1.9 Design Principles

1. **Deterministic Core Logic** - All numerical calculations produce identical results for identical inputs
2. **Probabilistic Uncertainty** - P10/P50/P90 percentiles quantify forecast uncertainty
3. **InfluxDB as Single Source of Truth** - All data stored as time series
4. **Rolling Horizon** - Decisions recalculated every 5-15 minutes
5. **Decoupled Components** - Each add-on operates independently with clear interfaces

---

# Chapter 2: SwissSolarForecast Add-on

## 2.1 Overview

SwissSolarForecast generates probabilistic PV power forecasts using MeteoSwiss ICON ensemble weather data and the pvlib solar modeling library. It produces P10/P50/P90 percentile forecasts for each inverter and the total system.

| Property | Value |
|----------|-------|
| Name | SwissSolarForecast |
| Version | 1.0.1 |
| Slug | `swisssolarforecast` |
| Architectures | aarch64, amd64, armv7 |
| Timeout | 300 seconds |
| Storage | /share/swisssolarforecast (GRIB data) |

## 2.2 Features

- **Weather Data**: MeteoSwiss ICON-CH1 (1km, 33h) and ICON-CH2 (2.1km, 120h) ensemble forecasts
- **Ensemble Members**: 11 (CH1) or 21 (CH2) members for uncertainty quantification
- **Output**: P10/P50/P90 percentiles at 15-minute resolution
- **Per-Inverter**: Separate forecasts for each inverter (EastWest, South)
- **Energy Balance**: Integrated with load forecast for net surplus/deficit calculation
- **Notifications**: Optional Telegram alerts for errors

## 2.3 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SwissSolarForecast Add-on                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  FETCHER (scheduled via cron)                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ CH1: 8× daily (30 2,5,8,11,14,17,20,23 * * *)                 │  │
│  │ CH2: 4× daily (45 2,8,14,20 * * *)                            │  │
│  │                                                                │  │
│  │ MeteoSwiss STAC API ──▶ GRIB files (/share/swisssolarforecast)│  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                 │                                   │
│                                 │ Local files                       │
│                                 ▼                                   │
│  CALCULATOR (every 15 minutes)                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ 1. Load GRIB files from disk                                  │  │
│  │ 2. Extract GHI + Temperature at location                      │  │
│  │ 3. For each ensemble member:                                  │  │
│  │    • Decompose GHI → DNI + DHI (Erbs model)                   │  │
│  │    • Calculate solar position (pvlib)                         │  │
│  │    • Calculate POA irradiance per string                      │  │
│  │    • Calculate cell temperature (Faiman)                      │  │
│  │    • Calculate DC power (PVWatts)                             │  │
│  │    • Apply inverter efficiency + clipping                     │  │
│  │ 4. Calculate P10/P50/P90 across ensemble members              │  │
│  │ 5. Query load forecast from load_forecast bucket              │  │
│  │ 6. Calculate energy balance (PV - Load)                       │  │
│  │ 7. Write to InfluxDB pv_forecast bucket                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 2.4 MeteoSwiss ICON Models

| Property | ICON-CH1-EPS | ICON-CH2-EPS |
|----------|--------------|--------------|
| Resolution | 1 km | 2.1 km |
| Forecast Horizon | 33 hours | 120 hours (5 days) |
| Ensemble Members | 11 (1 ctrl + 10 pert) | 21 (1 ctrl + 20 pert) |
| Model Runs (UTC) | 00, 03, 06, 09, 12, 15, 18, 21 | 00, 06, 12, 18 |
| Publication Delay | ~2.5 hours | ~2.5 hours |
| Grid Points | ~1.1 million | 283,876 |

**Variables Fetched:**

| Variable | ICON Name | Description | Unit |
|----------|-----------|-------------|------|
| GHI | `ASOB_S` | Net shortwave radiation at surface | W/m² |
| Temperature | `T_2M` | Air temperature at 2m height | K |

## 2.5 PV System Configuration

Configuration is defined in `/config/swisssolarforecast.yaml` or via HA add-on options:

```yaml
panels:
  - id: "AE455"
    model: "AE Solar AC-455MH/144V"
    pdc0: 455
    gamma_pdc: -0.0035

  - id: "Generic400"
    model: "Generic 400W"
    pdc0: 400
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
        efficiency: 0.82
        strings:
          - name: "East"
            azimuth: 90
            tilt: 15
            panel: "AE455"
            count: 8
          - name: "West"
            azimuth: 270
            tilt: 15
            panel: "AE455"
            count: 9

      - name: "South"
        max_power: 1500
        efficiency: 0.80
        strings:
          - name: "SouthFront"
            azimuth: 180
            tilt: 70
            panel: "Generic400"
            count: 3
          - name: "SouthBack"
            azimuth: 180
            tilt: 60
            panel: "Generic400"
            count: 2
```

## 2.6 Configuration Options

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  token: "your-influxdb-token"
  org: "energymanagement"
  bucket: "pv_forecast"
  load_bucket: "load_forecast"

location:
  latitude: 47.475
  longitude: 7.767
  altitude: 330
  timezone: "Europe/Zurich"

schedule:
  ch1_cron: "30 2,5,8,11,14,17,20,23 * * *"  # UTC, 2.5h after model runs
  ch2_cron: "45 2,8,14,20 * * *"              # UTC, 2.75h after model runs
  calculator_interval_minutes: 15

storage:
  data_path: "/share/swisssolarforecast"
  max_storage_gb: 3.0
  cleanup_old_runs: true

notifications:
  telegram_enabled: false
  telegram_bot_token: ""
  telegram_chat_id: ""

log_level: "info"
```

## 2.7 InfluxDB Output Schema

**Measurement:** `pv_forecast`

**Resolution:** 15-minute intervals (aligned to :00, :15, :30, :45)

### Tags

| Tag | Values | Description |
|-----|--------|-------------|
| `inverter` | `total`, `EastWest`, `South` | Inverter identifier |
| `model` | `ch1`, `ch2`, `hybrid` | ICON model used |
| `run_time` | ISO timestamp | When forecast was calculated |

### Fields (inverter="total")

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | PV power (pessimistic, 90% chance to exceed) |
| `power_w_p50` | W | PV power (expected/median) |
| `power_w_p90` | W | PV power (optimistic, 10% chance to exceed) |
| `energy_wh_p10` | Wh | Per-period energy (pessimistic) |
| `energy_wh_p50` | Wh | Per-period energy (expected) |
| `energy_wh_p90` | Wh | Per-period energy (optimistic) |
| `load_energy_wh_p10` | Wh | Load energy (pessimistic) |
| `load_energy_wh_p50` | Wh | Load energy (expected) |
| `load_energy_wh_p90` | Wh | Load energy (optimistic) |
| `net_energy_wh_p10` | Wh | Net = PV_p10 - Load_p90 (pessimistic) |
| `net_energy_wh_p50` | Wh | Net = PV_p50 - Load_p50 (expected) |
| `net_energy_wh_p90` | Wh | Net = PV_p90 - Load_p10 (optimistic) |
| `ghi` | W/m² | Global horizontal irradiance |
| `temp_air` | °C | Air temperature |

### Fields (inverter="EastWest" or "South")

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | Inverter power (pessimistic) |
| `power_w_p50` | W | Inverter power (expected) |
| `power_w_p90` | W | Inverter power (optimistic) |

## 2.8 Calculation Pipeline

```
For each ensemble member (11 for CH1, 21 for CH2):
│
├─► Extract GHI, Temperature at PV location
│
├─► Decompose GHI → DNI + DHI (Erbs model)
│
├─► For each string:
│   ├─► Calculate solar position (lat/lon/time)
│   ├─► Transpose to plane-of-array (azimuth/tilt)
│   ├─► Calculate cell temperature (Faiman model)
│   └─► Calculate DC power (PVWatts with γ coefficient)
│
├─► Sum strings → Inverter DC power
│
├─► Apply inverter efficiency
│
└─► Clip to max_power → Inverter AC power

Stack all members → array [members × time_steps]
│
└─► Calculate percentiles:
    • P10 = 10th percentile (pessimistic)
    • P50 = 50th percentile (median)
    • P90 = 90th percentile (optimistic)
```

## 2.9 Source Files

| File | Lines | Purpose |
|------|-------|---------|
| `run.py` | 386 | Main entry point, scheduler initialization |
| `src/icon_fetcher.py` | 466 | MeteoSwiss STAC API client, GRIB download |
| `src/grib_parser.py` | 840 | GRIB file parsing, grid handling |
| `src/pv_model.py` | 338 | pvlib-based PV power calculations |
| `src/influxdb_writer.py` | 405 | InfluxDB forecast writer |
| `src/scheduler.py` | 202 | APScheduler wrapper |
| `src/config.py` | 146 | PV system configuration loader |
| `src/notifications.py` | 135 | Telegram notifications |

## 2.10 Dependencies

```
pvlib>=0.10.0              # Industry-standard PV modeling
pandas>=2.0.0              # Data manipulation
numpy>=1.24.0              # Numerical computing
requests>=2.28.0           # HTTP client for STAC API
xarray>=2023.1.0           # N-dimensional arrays
cfgrib>=0.9.10             # GRIB file handling
eccodes>=1.5.0             # GRIB codec library
PyYAML>=6.0                # YAML parsing
influxdb-client>=1.36.0    # InfluxDB client
APScheduler>=3.10.0        # Task scheduling
```

## 2.11 Grafana Queries

**PV Power Forecast with uncertainty band:**
```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "power_w_p10" or
                       r._field == "power_w_p50" or
                       r._field == "power_w_p90")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

**Per-inverter comparison:**
```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r._field == "power_w_p50")
  |> pivot(rowKey: ["_time"], columnKey: ["inverter"], valueColumn: "_value")
```

**Energy Balance:**
```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "energy_wh_p50" or
                       r._field == "load_energy_wh_p50" or
                       r._field == "net_energy_wh_p50")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

---

# Chapter 3: LoadForecast Add-on

## 3.1 Overview

LoadForecast generates statistical household load consumption forecasts using historical consumption patterns. It analyzes 90 days of historical data to build time-of-day profiles and produces P10/P50/P90 percentile forecasts.

| Property | Value |
|----------|-------|
| Name | LoadForecast |
| Version | 1.0.1 |
| Slug | `loadforecast` |
| Architectures | aarch64, amd64, armv7 |
| Timeout | 120 seconds |
| Schedule | Hourly (cron: `15 * * * *`) |

## 3.2 Features

- **Statistical Profiling**: Time-of-day consumption profiles (96 daily slots)
- **Historical Analysis**: Uses 90 days of consumption data
- **Probabilistic Output**: P10/P50/P90 percentiles for uncertainty bands
- **15-Minute Resolution**: Aligned with MPC optimization timestep
- **48-Hour Horizon**: Sufficient for next-day planning

## 3.3 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                       LoadForecast Add-on                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  FORECAST CYCLE (every hour at :15)                                  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ 1. Query 90 days of load_power from HomeAssistant bucket       │  │
│  │                                                                 │  │
│  │ 2. Build time-of-day profile:                                  │  │
│  │    • Group into 96 daily slots (15-min periods)                │  │
│  │    • Calculate P10/P50/P90 percentiles per slot                │  │
│  │                                                                 │  │
│  │ 3. Generate 48-hour forecast:                                  │  │
│  │    • Map future timestamps to profile slots                    │  │
│  │    • Look up P10/P50/P90 values                                │  │
│  │                                                                 │  │
│  │ 4. Write to load_forecast bucket                               │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 3.4 Algorithm

### Time-of-Day Profiling

The algorithm divides each day into 96 slots (15-minute periods):

```
Slot = hour × 4 + (minute ÷ 15)

Slot  0 = 00:00 - 00:15
Slot  1 = 00:15 - 00:30
...
Slot 47 = 11:45 - 12:00
Slot 48 = 12:00 - 12:15
...
Slot 95 = 23:45 - 00:00
```

### Profile Building

For each of the 96 slots:
1. Collect all historical consumption values at that time slot
2. Calculate statistics across the 90-day window:
   - P10 (10th percentile): Low consumption, 90% chance to exceed
   - P50 (50th percentile): Median/typical consumption
   - P90 (90th percentile): High consumption, 10% chance to exceed

### Forecast Generation

For each future timestamp in the 48-hour horizon:
1. Calculate the slot number from the timestamp
2. Look up P10/P50/P90 values from the profile
3. Convert power (W) to per-period energy (Wh): `power × 0.25h`

## 3.5 Configuration Options

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  token: "your-influxdb-token"
  org: "energymanagement"
  source_bucket: "HomeAssistant"    # Where to read historical data
  target_bucket: "load_forecast"     # Where to write forecasts

load_sensor:
  entity_id: "load_power"            # HA entity to use for load

forecast:
  history_days: 90                   # Days of history to analyze
  horizon_hours: 48                  # Forecast horizon

schedule:
  cron: "15 * * * *"                 # Run at :15 every hour

log_level: "info"
```

## 3.6 InfluxDB Output Schema

**Measurement:** `load_forecast`

**Resolution:** 15-minute intervals

### Tags

| Tag | Values | Description |
|-----|--------|-------------|
| `model` | `statistical` | Forecast model type |
| `run_time` | ISO timestamp | When forecast was generated |

### Fields

| Field | Unit | Description |
|-------|------|-------------|
| `energy_wh_p10` | Wh | Per-period energy (low, 90% chance to exceed) |
| `energy_wh_p50` | Wh | Per-period energy (median/typical) |
| `energy_wh_p90` | Wh | Per-period energy (high, 10% chance to exceed) |

**Note:** Values represent energy per 15-minute period, not instantaneous power.

## 3.7 Data Source

The add-on queries historical consumption data from the `HomeAssistant` InfluxDB bucket:

```flux
from(bucket: "HomeAssistant")
  |> range(start: -90d)
  |> filter(fn: (r) => r.entity_id == "load_power")
  |> filter(fn: (r) => r._field == "value")
  |> aggregateWindow(every: 15m, fn: mean)
```

**Important:** The `sensor.load_power` entity in Home Assistant is calculated by the Huawei Solar integration:
```
load = solar_pv_total_ac_power - power_meter_active_power + battery_charge_discharge_power
```

For more accurate measurements, Shelly 3EM phase sensors can be used as an alternative.

## 3.8 Source Files

| File | Lines | Purpose |
|------|-------|---------|
| `run.py` | 192 | Main entry point, scheduler loop |
| `src/load_predictor.py` | 183 | Statistical forecasting algorithm |
| `src/influxdb_writer.py` | 140 | InfluxDB forecast writer |

## 3.9 Dependencies

```
pandas>=2.0.0              # Data manipulation
numpy>=1.24.0              # Numerical computing
influxdb-client>=1.36.0    # InfluxDB client
croniter>=1.3.0            # Cron expression parsing
```

## 3.10 Grafana Queries

**Load Forecast with uncertainty band:**
```flux
from(bucket: "load_forecast")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "load_forecast")
  |> filter(fn: (r) => r._field == "energy_wh_p10" or
                       r._field == "energy_wh_p50" or
                       r._field == "energy_wh_p90")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

**Forecast vs Actual:**
```flux
forecast = from(bucket: "load_forecast")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._field == "energy_wh_p50")

actual = from(bucket: "HomeAssistant")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.entity_id == "load_power")
  |> aggregateWindow(every: 15m, fn: mean)

union(tables: [forecast, actual])
```

## 3.11 Limitations and Future Enhancements

**Current Limitations:**
- No weekday/weekend differentiation
- No seasonal adjustment
- No special event handling (holidays, vacations)
- No appliance-level modeling

**Potential Enhancements:**
- Separate weekday/weekend profiles
- Seasonal scaling factors
- Short-term adaptation based on recent hours
- Integration with calendar events
- Machine learning models (LSTM, XGBoost)

---

# Chapter 4: EnergyOptimizer Add-on (Planned)

## 4.1 Overview

The EnergyOptimizer add-on will implement Model Predictive Control (MPC) to optimize battery charging/discharging, EV charging, and deferrable loads based on PV and load forecasts, tariff schedules, and device constraints.

| Property | Planned Value |
|----------|---------------|
| Name | EnergyOptimizer |
| Version | 0.1.0 (planned) |
| Slug | `energyoptimizer` |
| Update Frequency | Every 5-15 minutes |
| Optimization Horizon | 24-48 hours |

## 4.2 Objectives

### Primary Objective
**Minimize total electricity cost** over the optimization horizon:

```
min Σ [ (P_import × tariff_import) - (P_export × tariff_export) ] × Δt
```

### Secondary Objectives
- Maximize self-consumption
- Preserve battery health (limit deep cycles)
- Ensure EV reaches target SOC by departure time
- Minimize grid peak power

## 4.3 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      EnergyOptimizer Add-on                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  OPTIMIZATION CYCLE (every 5-15 minutes)                            │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ 1. READ INPUTS                                                 │  │
│  │    • PV forecast P10/P50/P90 from pv_forecast bucket           │  │
│  │    • Load forecast P10/P50/P90 from load_forecast bucket       │  │
│  │    • Current battery SOC from HomeAssistant                    │  │
│  │    • Current time and tariff period                            │  │
│  │    • EV connection status and target                           │  │
│  │                                                                 │  │
│  │ 2. APPLY CONSTRAINTS                                           │  │
│  │    • Battery: SOC limits, power limits, efficiency             │  │
│  │    • EV: departure time, target SOC, power limits              │  │
│  │    • Grid: import/export limits                                │  │
│  │    • Policy: night reserve, discharge blocking                 │  │
│  │                                                                 │  │
│  │ 3. SOLVE OPTIMIZATION                                          │  │
│  │    • Rolling horizon MPC (24-48h lookahead)                    │  │
│  │    • Linear or MILP solver                                     │  │
│  │    • Robust optimization using P10/P90 for constraints         │  │
│  │                                                                 │  │
│  │ 4. OUTPUT SETPOINTS                                            │  │
│  │    • Battery: discharge_allowed, power_setpoint                │  │
│  │    • Wallbox: charging_enabled, power_limit                    │  │
│  │    • Dishwasher: start_recommended, start_time                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 4.4 Input Data

### From InfluxDB

| Source | Data | Use |
|--------|------|-----|
| `pv_forecast` | P10/P50/P90 power forecasts | Solar production prediction |
| `load_forecast` | P10/P50/P90 energy forecasts | Consumption prediction |
| `HomeAssistant` | Real-time measurements | Current state |

### From Home Assistant

| Entity | Description | Use |
|--------|-------------|-----|
| `sensor.battery_state_of_capacity` | Current SOC | Initial condition |
| `sensor.battery_charge_discharge_power` | Current battery flow | Validation |
| `sensor.power_meter_active_power` | Current grid flow | Validation |
| `binary_sensor.ev_connected` | EV plug status | EV scheduling |

### From Configuration

| Parameter | Description |
|-----------|-------------|
| Tariff schedule | Day/night prices, time windows |
| Battery limits | Capacity, power, efficiency, SOC bounds |
| EV parameters | Departure time, target SOC, power limits |
| Policy rules | Night reserve, discharge blocking |

## 4.5 Decision Variables

### Battery Control

| Variable | Type | Unit | Description |
|----------|------|------|-------------|
| `P_bat[t]` | Continuous | W | Battery power (+ = discharge, - = charge) |
| `discharge_allowed` | Binary | - | Allow discharge during night hours |
| `min_soc_target` | Continuous | % | Overnight SOC reserve |

### Wallbox Control

| Variable | Type | Unit | Description |
|----------|------|------|-------------|
| `P_ev[t]` | Continuous | W | EV charging power |
| `ev_charging_enabled` | Binary | - | Enable/disable charging |
| `ev_phase_count` | Integer | - | Number of phases (1 or 3) |

### Dishwasher Control

| Variable | Type | Unit | Description |
|----------|------|------|-------------|
| `dishwasher_start[t]` | Binary | - | Start at time t |
| `recommended_start_time` | Time | - | Suggested start time |

## 4.6 Constraints

### Battery Constraints

```
SOC_min ≤ SOC[t] ≤ SOC_max                    # SOC bounds
-P_charge_max ≤ P_bat[t] ≤ P_discharge_max    # Power limits
SOC[t+1] = SOC[t] - (P_bat[t] × Δt) / (C_bat × η)  # State update
```

### EV Constraints

```
0 ≤ P_ev[t] ≤ P_ev_max                        # Power limits
Σ P_ev[t] × Δt ≥ E_target (by departure)      # Energy target
P_ev[t] = 0 when not connected                # Connection status
```

### Grid Constraints

```
P_grid[t] = P_load[t] - P_pv[t] + P_bat[t] + P_ev[t]  # Power balance
-P_export_max ≤ P_grid[t] ≤ P_import_max              # Grid limits
```

### Policy Constraints

**Night Reserve Rule:**
```
If time ∈ [21:00, 06:00]:
    SOC[06:00] ≥ SOC_reserve
    OR discharge_allowed = false
```

**Night Tariff Strategy:**
```
If time ∈ [21:00, 06:00] AND tariff = night:
    Prefer: charge battery from grid
    Avoid: discharge battery (unless SOC high and tomorrow sunny)
```

## 4.7 Configuration Schema

```yaml
energy_management:
  tariffs:
    import:
      windows:
        - name: night
          start: "21:00"
          end: "06:00"
          price_chf_per_kwh: 0.15
        - name: day
          start: "06:00"
          end: "21:00"
          price_chf_per_kwh: 0.30
    export:
      price_chf_per_kwh: 0.08

  battery:
    usable_kwh: 10.0
    soc_min_pct: 10.0
    soc_max_pct: 100.0
    max_charge_kw: 5.0
    max_discharge_kw: 5.0
    eta_charge: 0.95
    eta_discharge: 0.95
    control_mode: "power_setpoint"

  ev:
    max_charge_kw: 11.0
    min_charge_kw: 1.4      # Single phase minimum
    departure_time: "07:30"
    target_energy_kwh: 20.0
    phase_switching: true

  dishwasher:
    earliest_start: "09:00"
    latest_finish: "18:00"
    duration_h: 2.0
    energy_kwh: 1.5
    actuation: "notification"

  policy:
    overnight_reserve:
      type: "soc_pct"
      value: 30.0
      enforce_at: "06:00"
    night_discharge_block:
      start: "21:00"
      end: "06:00"
      allow_if_reserve_ok: true

  optimizer:
    horizon_hours: 36
    timestep_minutes: 15
    update_interval_minutes: 15
    solver: "highs"           # or "cbc", "glpk"
    risk_mode: "robust"       # Use P10/P90 for constraints
```

## 4.8 Output Signals

### Battery Control

| Signal | Type | Description |
|--------|------|-------------|
| `battery_discharge_allowed` | Boolean | Allow discharge during current period |
| `battery_power_setpoint` | Float (W) | Target battery power |
| `battery_min_soc_target` | Float (%) | Overnight SOC reserve |

### Wallbox Control

| Signal | Type | Description |
|--------|------|-------------|
| `ev_charging_enabled` | Boolean | Enable/disable charging |
| `ev_power_limit` | Float (W) | Maximum charging power |
| `ev_phases` | Integer | Number of phases to use |

### Dishwasher Control

| Signal | Type | Description |
|--------|------|-------------|
| `dishwasher_start_recommended` | Boolean | Should start now |
| `dishwasher_optimal_start` | Time | Recommended start time |

## 4.9 Planned Dependencies

```
pandas>=2.0.0              # Data manipulation
numpy>=1.24.0              # Numerical computing
influxdb-client>=1.36.0    # InfluxDB client
scipy>=1.10.0              # Optimization
cvxpy>=1.3.0               # Convex optimization (optional)
highspy>=1.5.0             # HiGHS MIP solver
APScheduler>=3.10.0        # Task scheduling
```

## 4.10 Implementation Roadmap

### Phase 1: Basic Battery Control
- Read PV and load forecasts
- Simple rule-based battery control
- Night discharge blocking
- InfluxDB logging of decisions

### Phase 2: MPC Optimization
- Linear programming formulation
- Rolling horizon optimization
- Cost minimization objective
- SOC and power constraints

### Phase 3: EV Integration
- EV connection status detection
- Departure time and target SOC
- Coordinated battery/EV optimization

### Phase 4: Advanced Features
- Robust optimization with uncertainty
- Dishwasher scheduling
- Grid peak shaving
- Dynamic tariff integration

## 4.11 Integration with Home Assistant

### Input Entities

```yaml
# Required sensors
sensor.battery_state_of_capacity     # Battery SOC (%)
sensor.battery_charge_discharge_power # Battery power (W)
sensor.power_meter_active_power      # Grid power (W)

# Optional for EV
binary_sensor.ev_connected           # EV plug status
sensor.ev_battery_level              # EV SOC (%)
```

### Output Entities

```yaml
# Battery control
number.battery_maximum_discharging_power  # Limit discharge
number.battery_end_of_discharge_soc       # Min SOC limit
switch.battery_discharge_enabled          # Custom switch

# Wallbox control
number.wallbox_current_limit              # Charging current
switch.wallbox_charging                   # Enable/disable
```

### Automation Example

```yaml
automation:
  - alias: "Apply EnergyOptimizer Battery Setpoint"
    trigger:
      - platform: state
        entity_id: sensor.energyoptimizer_battery_discharge_allowed
    action:
      - service: number.set_value
        target:
          entity_id: number.battery_maximum_discharging_power
        data:
          value: >
            {% if states('sensor.energyoptimizer_battery_discharge_allowed') == 'on' %}
              5000
            {% else %}
              0
            {% endif %}
```

---

# Appendix A: Installation Guide

## A.1 Prerequisites

- Home Assistant OS or Supervised installation
- InfluxDB 2.x with buckets configured
- Network access to MeteoSwiss API

## A.2 Add Repository

1. Navigate to **Settings** → **Add-ons** → **Add-on Store**
2. Click **⋮** → **Repositories**
3. Add: `https://github.com/SensorsIot/Energy-Management`

## A.3 Install Add-ons

1. Find each add-on in the store
2. Click **Install**
3. Configure options in the **Configuration** tab
4. Start the add-on

## A.4 InfluxDB Setup

Create required buckets:

```bash
influx bucket create --name pv_forecast --retention 30d
influx bucket create --name load_forecast --retention 30d
```

## A.5 Verify Operation

Check add-on logs:
```
Settings → Add-ons → [Add-on Name] → Log
```

Query InfluxDB:
```flux
from(bucket: "pv_forecast")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> limit(n: 10)
```

---

# Appendix B: Grafana Dashboard

A pre-built Grafana dashboard is available at:
`/home/energymanagement/swisssolarforecast/grafana-forecast-dashboard.json`

**Import:**
1. Grafana → **Dashboards** → **New** → **Import**
2. Upload JSON file
3. Select InfluxDB datasource

**Panels:**
- PV Power Forecast (P10/P50/P90 bands)
- Load Forecast (P10/P50/P90 bands)
- Net Power (Surplus/Deficit)
- Cumulative Energy
- Weather (GHI, Temperature)
- Statistics Table

---

# Appendix C: Troubleshooting

## C.1 No Forecast Data

**Check GRIB downloads:**
```bash
ls -la /share/swisssolarforecast/icon-ch1/
ls -la /share/swisssolarforecast/icon-ch2/
```

**Check add-on logs for errors:**
```
Settings → Add-ons → SwissSolarForecast → Log
```

## C.2 InfluxDB Connection Failed

**Test connection:**
```bash
curl -H "Authorization: Token YOUR_TOKEN" \
  http://192.168.0.203:8087/api/v2/buckets
```

**Verify credentials in add-on configuration.**

## C.3 Load Forecast Empty

**Check historical data exists:**
```flux
from(bucket: "HomeAssistant")
  |> range(start: -7d)
  |> filter(fn: (r) => r.entity_id == "load_power")
  |> count()
```

**Verify entity_id matches your sensor.**

---

**End of Document**

*Version 2.0 - January 2026*
