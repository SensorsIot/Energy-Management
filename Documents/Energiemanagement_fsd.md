# EnergyManagement.md
## Functional System Document (FSD)

**Project:** Intelligent energy management with PV, battery, EV, and tariffs  
**Location:** Lausen (BL), Switzerland  
**Version:** 1.2  
**Status:** PV forecast implemented  
**Implementation:** Python  
**Data storage:** InfluxDB  
**Weather/forecast data:** MeteoSwiss (ICON-CH2-EPS Open Data)

---

## 1. Goal and motivation

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

---

## 2. Principles

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

## 3. System overview

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

## 4. Data basis (InfluxDB)

### 4.1 Measurements (required)

| Measurement        | Description                          | Unit    |
|--------------------|--------------------------------------|---------|
| pv_power_ac        | PV AC power                          | W       |
| house_power        | House consumption                    | W       |
| grid_power         | Grid power (+import / -export)       | W       |
| battery_soc        | Battery state of charge              | %       |
| battery_power      | Battery power                        | W       |
| ev_power           | Wallbox / EV power                   | W       |

### 4.2 Tariff data

| Measurement      | Description                 | Unit      |
|------------------|-----------------------------|-----------|
| tariff_import    | Import electricity price    | CHF/kWh   |
| tariff_export    | Feed-in compensation (opt.) | CHF/kWh   |

Tariffs are time-dependent and known for the planning horizon.

---

## 5. PV forecast (pvlib, MeteoSwiss)

### 5.1 Meteorological inputs

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

### 5.2 Model chain

- Solar position (pvlib.solarposition)
- Decomposition of GHI -> DNI/DHI if not provided (Erbs model)
- Transposition to plane of array (POA) - isotropic sky model
- Cell temperature model (Faiman)
- DC power (PVWatts model with temperature coefficient)
- AC power (inverter efficiency + clipping)

### 5.3 PV configuration hierarchy

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

### 5.4 Panel types

| ID          | Model                    | Pdc0 (W) | γ (1/K)  |
|-------------|--------------------------|----------|----------|
| AE455       | AE Solar AC-455MH/144V   | 455      | -0.0035  |
| Generic400  | Generic 400W             | 400      | -0.0035  |

### 5.5 Plant configuration

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

## 6. Dynamic loss modeling

No fixed system losses are used.

### 6.1 Ideal power

Calculation of an ideal, loss-free power:

P_pv_ideal(t)

### 6.2 Loss factor

Comparison with measurement:

k(t) = P_pv_meas(t) / P_pv_ideal(t)

- Plausibility checks and clipping
- Smoothing (time-dependent)

### 6.3 Use in the forecast

P_pv_forecast(t) = k_forecast(t) * P_pv_ideal_forecast(t)

The factor k reflects:
- Soiling
- Snow
- Partial shading
- Real inverter efficiency

---

## 7. Load forecast

### 7.1 Principle

The load forecast uses only historical data
(no leakage, no future information).

### 7.2 Baseline model

- Profiles by weekday and time slot (e.g., 15 min)
- Robust statistics (median, trimmed mean)
- Smoothing over time

P_load_forecast(t) = Profile[weekday, slot]

### 7.3 Short-term adaptation

Scaling/offset based on the last hours to reflect day-specific patterns.

### 7.4 Uncertainty band (optional)

Calculation of P10/P50/P90 for risk-aware optimization.

---

## 8. Optimization: battery & wallbox

### 8.1 Optimization objective

Primary:
- Minimize electricity costs considering tariffs

Secondary:
- Maximize self-consumption
- Preserve battery health
- Ensure EV target energy

### 8.2 Decision variables

- Battery power P_bat_set(t)
- Wallbox power P_ev_set(t)

### 8.3 Constraints

**Battery**
- SoC_min <= SoC <= SoC_max
- Charging/discharging power limited
- Efficiency considered

**EV / wallbox**
- Departure time
- Target energy / target SoC
- Maximum power, minimum power

**Grid**
- Import/export limits
- Zero-export (optional)

### 8.4 Method

- Rolling horizon MPC
- Planning horizon: 24-48 h
- Compute cycle: 5-15 min

---

## 9. LLM daily review & parameter tuning

### 9.1 Purpose

The LLM creates a daily (e.g., 08:00) critical analysis:

- Forecast vs. reality
- Loss model stability
- Load forecast quality
- Optimizer decisions
- Cost and goal attainment

### 9.2 Analysis contents

1. Data quality (gaps, outliers, time shift)
2. PV forecast bias and daily profiles
3. Load forecast deviations
4. Battery and EV behavior
5. Tariff usage (night tariff)

### 9.3 Improvement suggestions

The LLM provides:
- Concrete recommendations
- Expected impact
- Affected parameters
- Validation steps

### 9.4 Parameter governance

- **Safe auto-tune** (e.g., smoothing factors)
- **Review required** (e.g., SoC reserves)
- **Never auto** (hardware and protection limits)

All changes are made as suggestions (config diffs), not automatically.

---

## 10. Outputs (InfluxDB)

| Measurement                   | Content                           |
|------------------------------|-----------------------------------|
| pv_forecast_power_ac         | PV power forecast                 |
| load_forecast_power          | Load forecast                     |
| optimizer_battery_setpoint   | Battery setpoint                  |
| optimizer_ev_setpoint        | Wallbox setpoint                  |
| daily_llm_report             | Analysis & recommendations (text) |

---

## 11. Success criteria

The system is considered successful when:

- Energy costs decrease compared to a baseline
- EV targets are reliably met
- Battery constraints are respected
- Forecast errors are transparently detected and addressed
- Improvements are traceable and rollbackable

---

**End of document**
