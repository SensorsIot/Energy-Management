# EnergyManagement.md
## Functional System Document (FSD)

**Project:** Intelligent energy management with PV, battery, EV, and tariffs
**Location:** Lausen (BL), Switzerland
**Version:** 1.12
**Status:** Updated Energy Dashboard config, removed Forecast.Solar
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

## 5. Data Sources

This chapter consolidates all external data sources used by the energy management system.

### 5.1 Infrastructure Overview

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| Home Assistant | 192.168.0.202 | 8123 | Device integration, Huawei Solar |
| InfluxDB | 192.168.0.203 | 8087 | Time series storage |
| Grafana | 192.168.0.203 | 3000 | Visualization |
| MQTT Broker | 192.168.0.203 | 1883 | IoT messaging (Enphase, sensors) |

**Data flow:**
- MQTT → Home Assistant → InfluxDB
- Huawei Solar API → Home Assistant → InfluxDB
- Shelly 3EM → Home Assistant → InfluxDB

Credentials are stored in `/home/energymanagement/Documents/secrets.txt`.

### 5.2 Electrical System Overview

```
                                    GRID
                                      │
                                      ▼
                          ┌───────────────────────┐
                          │    EBL Smartmeter     │  Grid connection point
                          │  (utility meter)      │  (import/export to grid)
                          └───────────────────────┘
                                      │
                                      ▼
                          ┌───────────────────────┐
                          │      Wallbox          │  EV charging
                          │   (between meters)    │
                          └───────────────────────┘
                                      │
                                      ▼
                          ┌───────────────────────┐
                          │   Huawei Smartmeter   │  sensor.power_meter_active_power
                          │   (DTSU666-H)         │  (neg = export, pos = import)
                          └───────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
              ▼                       ▼                       ▼
   ┌─────────────────┐    ┌─────────────────────┐    ┌───────────────┐
   │  Huawei Sun2000 │    │    House Loads      │    │   Enphase     │
   │    Inverter     │    │                     │    │ Microinverters│
   │                 │    │  ┌───────────────┐  │    │               │
   │  ┌───────────┐  │    │  │  Shelly 3EM   │  │    │  (3x IQ7+)    │
   │  │  Battery  │  │    │  │  (CT clamps)  │  │    │               │
   │  │  (LUNA)   │  │    │  │ phase_1/2/3   │  │    └───────────────┘
   │  └───────────┘  │    │  └───────────────┘  │            │
   │        │        │    │         │           │            │
   │   charge/       │    │    Measures:        │       AC output
   │   discharge     │    │    Pure load        │     sensor.enphase_
   │                 │    │    consumption      │     energy_power
   └─────────────────┘    └─────────────────────┘
           │
      DC input from
      PV strings
      (East/West)
```

**Measurement Points:**

**Power (W) - Real-time:**

| Category | Location | Device | Measures | Key Entity |
|----------|----------|--------|----------|------------|
| Grid | Grid | EBL Smartmeter | Grid import/export | (utility, not in HA) |
| Grid | After Wallbox | Huawei DTSU666-H | Intermediate power flow | `sensor.power_meter_active_power` |
| Solar | Inverter | Huawei Sun2000 | Inverter AC output | `sensor.inverter_active_power` |
| Solar | Inverter | Huawei Sun2000 | DC input (strings) | `sensor.inverter_input_power` |
| Solar | Microinverters | Enphase IQ7+ | AC output | `sensor.enphase_energy_power` |
| Solar | Combined | HA Template | Total PV AC | `sensor.solar_pv_total_ac_power` |
| Battery | Battery | Huawei LUNA | Charge/discharge | `sensor.battery_charge_discharge_power` |
| Load | House | Shelly 3EM | Phase A | `sensor.phase_1_power` |
| Load | House | Shelly 3EM | Phase B | `sensor.phase_2_power` |
| Load | House | Shelly 3EM | Phase C | `sensor.phase_3_power` |
| Load | Calculated | Huawei Integration | Load (balance) | `sensor.load_power` |

**Energy (kWh) - Totals:**

| Category | Location | Device | Measures | Key Entity |
|----------|----------|--------|----------|------------|
| Grid | After Wallbox | Huawei DTSU666-H | Grid import total | `sensor.power_meter_consumption` |
| Grid | After Wallbox | Huawei DTSU666-H | Grid export total | `sensor.power_meter_exported` |
| Solar | Inverter | Huawei Sun2000 | Daily yield | `sensor.inverter_daily_yield` |
| Solar | Inverter | Huawei Sun2000 | Total yield | `sensor.inverter_total_yield` |
| Solar | Microinverters | Enphase IQ7+ | Today | `sensor.enphase_energy_today` |
| Solar | Microinverters | Enphase IQ7+ | Total | `sensor.enphase_energy_total` |
| Battery | Battery | Huawei LUNA | Daily charge | `sensor.battery_day_charge` |
| Battery | Battery | Huawei LUNA | Daily discharge | `sensor.battery_day_discharge` |
| Battery | Battery | Huawei LUNA | Total charge | `sensor.battery_total_charge` |
| Battery | Battery | Huawei LUNA | Total discharge | `sensor.battery_total_discharge` |
| Load | House | Shelly 3EM | Phase A total | `sensor.phase_1_energy` |
| Load | House | Shelly 3EM | Phase B total | `sensor.phase_2_energy` |
| Load | House | Shelly 3EM | Phase C total | `sensor.phase_3_energy` |
| Load | Calculated | Huawei Integration | Load total | `sensor.load_energy` |

**State:**

| Category | Device | Measures | Unit | Key Entity |
|----------|--------|----------|------|------------|
| Battery | Huawei LUNA | State of charge | % | `sensor.battery_state_of_capacity` |

**HA Energy Dashboard Compatibility:**

The HA Energy Dashboard requires sensors with specific attributes for proper statistics calculation:
- `state_class: total_increasing` (handles meter resets)
- `device_class: energy`
- `unit_of_measurement: kWh`

| Entity | state_class | Dashboard Compatible |
|--------|-------------|---------------------|
| `sensor.power_meter_consumption` | total_increasing | ✓ Grid import |
| `sensor.power_meter_exported` | total_increasing | ✓ Grid export |
| `sensor.solar_pv_total_ac_energy` | total_increasing | ✓ Solar production |
| `sensor.load_energy` | total_increasing | ✓ House consumption |
| `sensor.phase_1_energy` | total_increasing | ✓ Phase A (Shelly) |
| `sensor.phase_2_energy` | total_increasing | ✓ Phase B (Shelly) |
| `sensor.phase_3_energy` | total_increasing | ✓ Phase C (Shelly) |
| `sensor.battery_day_charge` | total_increasing | ✓ Battery (daily) |
| `sensor.battery_day_discharge` | total_increasing | ✓ Battery (daily) |
| `sensor.battery_total_charge` | total | ✗ (use day counters) |
| `sensor.battery_total_discharge` | total | ✗ (use day counters) |
| `sensor.enphase_energy_total` | total | ✗ (use today counter) |

**Recommended Energy Dashboard Configuration:**
- **Grid consumption:** `sensor.power_meter_consumption`
- **Return to grid:** `sensor.power_meter_exported`
- **Solar production:** `sensor.solar_pv_total_ac_energy`
- **Battery charge:** `sensor.battery_day_charge`
- **Battery discharge:** `sensor.battery_day_discharge`

**Note:** Forecast data (stored in InfluxDB with future timestamps) is separate from the Energy Dashboard, which only tracks historical consumption. The forecast bucket `pv_forecast` is used by the energy management add-on for decision-making, not for dashboard statistics.

**Energy Balance:**

```
Grid Power = PV Production - Load + Battery Discharge - Battery Charge - Wallbox

Where:
  PV Production = Huawei Inverter + Enphase
  Load = Shelly 3EM measurement (or calculated from balance)

Huawei calculates: load = solar_pv_total - power_meter + battery_power
```

### 5.3 Home Assistant - Huawei Solar Integration

Home Assistant provides the primary data source for solar/battery measurements via the
`huawei_solar` integration. Data is written to InfluxDB bucket `EnergyV1` via the
HA InfluxDB integration.

#### 5.3.1 Power Measurements (W) - Real-time

**Solar Production:**

| Entity ID | Description | MPC Use |
|-----------|-------------|---------|
| `sensor.inverter_input_power` | DC input (both strings) | PV production |
| `sensor.inverter_pv_1_power` | String 1 power | Per-string monitoring |
| `sensor.inverter_pv_2_power` | String 2 power | Per-string monitoring |
| `sensor.inverter_active_power` | Huawei inverter AC output | Huawei only |
| `sensor.solar_pv_total_ac_power` | Total AC output (Huawei + Enphase) | **Primary PV input** |
| `sensor.enphase_energy_power` | Enphase microinverter power | Secondary PV |
| `sensor.inverter_day_active_power_peak` | Today's peak power | Peak tracking |

Note: `sensor.solar_pv_total_ac_power` combines Huawei inverter + Enphase microinverters.

**Battery:**

| Entity ID | Description | MPC Use |
|-----------|-------------|---------|
| `sensor.battery_charge_discharge_power` | Charge/discharge power (+/-) | **Battery flow** |

**Grid:**

| Entity ID | Description | MPC Use |
|-----------|-------------|---------|
| `sensor.power_meter_active_power` | Grid power (neg=export) | **Critical: Grid flow** |
| `sensor.power_meter_phase_a_active_power` | Phase A power | Load balancing |
| `sensor.power_meter_phase_b_active_power` | Phase B power | Load balancing |
| `sensor.power_meter_phase_c_active_power` | Phase C power | Load balancing |

**Load (calculated):**

| Entity ID | Description | MPC Use |
|-----------|-------------|---------|
| `sensor.load_power` | House consumption | **Critical: Load input** |

Note: `sensor.load_power` is **calculated** by the huawei_solar integration using energy balance:
```
load = solar_pv_total_ac_power - power_meter_active_power + battery_charge_discharge_power
```
The Sun2000 inverter does not have a direct load meter. For actual measurement, see Section 5.3.6 (Shelly 3EM).

#### 5.3.2 Energy Measurements (kWh) - Totals

**Solar Production:**

| Entity ID | Description | Use |
|-----------|-------------|-----|
| `sensor.inverter_daily_yield` | Today's production | Daily reporting |
| `sensor.inverter_total_yield` | Lifetime AC yield | System totals |
| `sensor.inverter_total_dc_input_energy` | Lifetime DC input | Efficiency calc |
| `sensor.solar_pv_total_ac_energy` | Total AC energy | System totals |

**Battery:**

| Entity ID | Description | Use |
|-----------|-------------|-----|
| `sensor.battery_day_charge` | Today's charge | Daily reporting |
| `sensor.battery_day_discharge` | Today's discharge | Daily reporting |
| `sensor.battery_total_charge` | Lifetime charge | System totals |
| `sensor.battery_total_discharge` | Lifetime discharge | System totals |

**Grid:**

| Entity ID | Description | Use |
|-----------|-------------|-----|
| `sensor.power_meter_consumption` | Total grid import | Cost calculation |
| `sensor.power_meter_exported` | Total grid export | Revenue calculation |
| `sensor.power_meter_total_energy_2` | Net energy (export - import) | Net metering |

**Load:**

| Entity ID | Description | Use |
|-----------|-------------|-----|
| `sensor.load_energy` | Total consumption | Historical analysis |

#### 5.3.3 Battery State

| Entity ID | Description | Unit | MPC Use |
|-----------|-------------|------|---------|
| `sensor.battery_state_of_capacity` | State of charge | % | **Critical: SOC for MPC** |
| `sensor.battery_bus_voltage` | Battery voltage | V | Health monitoring |

#### 5.3.4 Battery Control (Outputs)

| Entity ID | Description | Unit | MPC Use |
|-----------|-------------|------|---------|
| `number.battery_maximum_discharging_power` | Max discharge limit | W | **Night strategy control** |
| `number.battery_maximum_charging_power` | Max charge limit | W | Charge limiting |
| `number.battery_end_of_discharge_soc` | Min SOC limit | % | SOC protection |
| `number.battery_end_of_charge_soc` | Max SOC limit | % | SOC protection |
| `number.battery_backup_power_soc` | Backup reserve | % | Emergency reserve |
| `select.battery_working_mode` | Operating mode | - | Mode selection |

#### 5.3.5 Shelly 3EM - Direct Load Measurement

The Shelly 3EM (device ID: `shellyem3-ECFABCC7F0F5`) provides **direct measurement** of house
load via 3-phase current transformers, as opposed to the calculated value from energy balance.

**Power Measurements (W):**

| Entity ID | Description | Phase |
|-----------|-------------|-------|
| `sensor.phase_1_power` | Phase A Power | L1 |
| `sensor.phase_2_power` | Phase B Power | L2 |
| `sensor.phase_3_power` | Phase C Power | L3 |

**Energy Measurements (kWh):**

| Entity ID | Description | Phase |
|-----------|-------------|-------|
| `sensor.phase_1_energy` | Phase A Energy (total) | L1 |
| `sensor.phase_2_energy` | Phase B Energy (total) | L2 |
| `sensor.phase_3_energy` | Phase C Energy (total) | L3 |
| `sensor.phase_1_energy_returned` | Phase A Energy returned | L1 |
| `sensor.phase_2_energy_returned` | Phase B Energy returned | L2 |
| `sensor.phase_3_energy_returned` | Phase C Energy returned | L3 |

**Additional Measurements:**

| Entity ID | Description | Unit |
|-----------|-------------|------|
| `sensor.phase_1_current` | Phase A Current | A |
| `sensor.phase_2_current` | Phase B Current | A |
| `sensor.phase_3_current` | Phase C Current | A |
| `sensor.phase_1_voltage` | Phase A Voltage | V |
| `sensor.phase_2_voltage` | Phase B Voltage | V |
| `sensor.phase_3_voltage` | Phase C Voltage | V |
| `sensor.phase_1_power_factor` | Phase A Power Factor | % |
| `sensor.phase_2_power_factor` | Phase B Power Factor | % |
| `sensor.phase_3_power_factor` | Phase C Power Factor | % |

**Total Load Calculation:**
```
load_measured = phase_1_power + phase_2_power + phase_3_power
```

**Comparison: Measured vs Calculated Load**

| Source | Entity | Method |
|--------|--------|--------|
| Shelly 3EM | Sum of `sensor.phase_*_power` | Direct CT measurement |
| Huawei Solar | `sensor.load_power` | Energy balance calculation |

The Shelly 3EM measurement is more accurate for actual house consumption but may differ
from the calculated value due to measurement timing, inverter/battery losses, and the
measurement point location in the electrical system.

#### 5.3.6 Enphase Microinverters

The Enphase microinverters provide additional solar production separate from the Huawei
inverter. Data is received via MQTT (Tasmota format) and integrated into Home Assistant.

**MQTT Topics:**
- `tele/Enphase/SENSOR` - Energy data (published every ~5 minutes)
- `tele/Enphase/STATE` - Device state, WiFi info
- `tele/Enphase/LWT` - Online/Offline status

**MQTT Payload Example (`tele/Enphase/SENSOR`):**
```json
{
  "Time": "2026-01-06T16:50:55",
  "ENERGY": {
    "TotalStartTime": "2023-02-11T10:09:42",
    "Total": 3511.448,
    "Yesterday": 6.986,
    "Today": 0.612,
    "Power": 4,
    "ApparentPower": 86,
    "ReactivePower": 86,
    "Factor": 0.04,
    "Voltage": 237,
    "Current": 0.364
  }
}
```

**Home Assistant Entities:**

| Entity ID | Description | Unit |
|-----------|-------------|------|
| `sensor.enphase_energy_power` | Current power output | W |
| `sensor.enphase_energy_total` | Lifetime energy | kWh |
| `sensor.enphase_energy_today` | Today's production | kWh |
| `sensor.enphase_energy_yesterday` | Yesterday's production | kWh |
| `sensor.enphase_energy_voltage` | Grid voltage | V |
| `sensor.enphase_energy_current` | Output current | A |
| `sensor.enphase_energy_factor` | Power factor | - |
| `sensor.enphase_energy_apparentpower` | Apparent power | VA |
| `sensor.enphase_energy_reactivepower` | Reactive power | var |
| `switch.enphase` | On/Off control | - |

**Integration with Huawei:**

The total PV power combines both sources:
```
sensor.solar_pv_total_ac_power = sensor.inverter_active_power + sensor.enphase_energy_power
```

This calculation is done in Home Assistant (template sensor or automation).

### 5.4 InfluxDB Buckets

| Bucket | Source | Content | Update Frequency |
|--------|--------|---------|------------------|
| `EnergyV1` | Home Assistant | Huawei Solar data (FusionSolar API names) | ~30s |
| `HomeAssistant` | Home Assistant | General HA entities | ~30s |

**Primary bucket for MPC:** `EnergyV1` (written by Home Assistant InfluxDB integration)

#### 5.4.1 EnergyV1 Field Mapping

The `EnergyV1` bucket uses FusionSolar API field names:

| InfluxDB Field | HA Entity | Description |
|----------------|-----------|-------------|
| `solar_ac_total_power` | `sensor.solar_pv_total_ac_power` | PV AC output |
| `battery_state_of_capacity` | `sensor.battery_state_of_capacity` | Battery SOC |
| `power_meter_active_power` | `sensor.power_meter_active_power` | Grid power |
| `load_total_power` | `sensor.load_power` | House load |
| `inverter_input_power` | `sensor.inverter_input_power` | PV DC input |
| `battery_charge_discharge_power` | `sensor.battery_charge_discharge_power` | Battery flow |

### 5.5 MeteoSwiss Weather Data

See Section 6 for detailed MeteoSwiss ICON forecast data fetching.

| Model | Source | Variables | Horizon |
|-------|--------|-----------|---------|
| ICON-CH1-EPS | MeteoSwiss STAC API | GHI, Temperature | 33h |
| ICON-CH2-EPS | MeteoSwiss STAC API | GHI, Temperature | 5 days |

### 5.6 Required Measurements Summary

For the MPC optimizer, these are the **critical real-time inputs**:

| Measurement | Source | InfluxDB Field | Unit |
|-------------|--------|----------------|------|
| PV AC power | HA → EnergyV1 | `solar_ac_total_power` | W |
| Battery SOC | HA → EnergyV1 | `battery_state_of_capacity` | % |
| Grid power | HA → EnergyV1 | `power_meter_active_power` | W |
| House load (calculated) | HA → EnergyV1 | `load_total_power` | W |
| House load (measured) | Shelly 3EM via HA | Sum of `phase_*_power` | W |
| Battery power | HA → EnergyV1 | `battery_charge_discharge_power` | W |

**Note on load measurements:** Two sources are available:
1. **Calculated** (`sensor.load_power`): From energy balance, always available
2. **Measured** (Shelly 3EM phases): Direct CT measurement, more accurate

For MPC, either source can be used. The measured value is preferred when available.

### 5.7 Tariff Data

| Parameter | Description | Unit | Source |
|-----------|-------------|------|--------|
| `tariff_import_day` | Day import price | CHF/kWh | YAML config |
| `tariff_import_night` | Night import price | CHF/kWh | YAML config |
| `tariff_export` | Feed-in compensation | CHF/kWh | YAML config |

Tariffs are time-dependent and defined in the YAML configuration file.

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

### 6.12 Forecast Output and Storage

The solar forecast is calculated from MeteoSwiss weather data and stored in InfluxDB
for use by the energy management system. Three percentile curves (P10/P50/P90) are
produced from the ensemble members to quantify uncertainty.

#### 6.12.1 Forecast Calculation Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│  For each ensemble member (11 for CH1, 21 for CH2):             │
│                                                                 │
│  1. Extract GHI, Temperature at PV location                     │
│  2. Decompose GHI → DNI + DHI (Erbs model)                      │
│  3. Calculate solar position (pvlib)                            │
│  4. Transpose to plane-of-array for each string orientation     │
│  5. Calculate cell temperature (Faiman model)                   │
│  6. Calculate DC power (PVWatts with temp coefficient)          │
│  7. Apply inverter efficiency and clipping → AC power           │
│  8. Sum all strings → total PV power for this member            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stack all members → array [members × time_steps]               │
│                                                                 │
│  Calculate percentiles across member axis:                      │
│    • P10 = 10th percentile (pessimistic, 90% chance to exceed)  │
│    • P50 = 50th percentile (median, expected value)             │
│    • P90 = 90th percentile (optimistic, 10% chance to exceed)   │
└─────────────────────────────────────────────────────────────────┘
```

#### 6.12.2 Output Resolution

| Parameter | Value |
|-----------|-------|
| Time resolution | 15 minutes |
| Forecast horizon | 48 hours |
| Buckets per forecast | 192 (48h × 4 per hour) |
| Update frequency | Every 15 minutes |
| Percentiles | P10, P50, P90 |

#### 6.12.3 Power and Energy Curves

**Power Forecast (W):**
Instantaneous power at each 15-minute bucket.

```
pv_power_p10[t] = 10th percentile of ensemble at time t
pv_power_p50[t] = 50th percentile of ensemble at time t
pv_power_p90[t] = 90th percentile of ensemble at time t
```

**Energy Forecast (Wh):**
Cumulative energy from forecast start, calculated by integrating power.

```
pv_energy_p50[t] = sum(pv_power_p50[0:t]) × 0.25h
```

#### 6.12.4 InfluxDB Storage Schema

**Measurement:** `pv_forecast`

| Tag | Description | Example |
|-----|-------------|---------|
| `percentile` | P10, P50, or P90 | `P50` |
| `run_time` | When forecast was calculated | `2026-01-06T12:00:00Z` |
| `model` | ICON model used | `ch1` or `ch2` or `hybrid` |

| Field | Description | Unit |
|-------|-------------|------|
| `power_w` | Forecasted PV power | W |
| `energy_wh` | Cumulative energy from start | Wh |

| Timestamp | Description |
|-----------|-------------|
| `_time` | Future time (bucket timestamp) |

**Example data points:**
```
pv_forecast,percentile=P50,run_time=2026-01-06T12:00:00Z,model=hybrid power_w=2340,energy_wh=585 1736168400000000000
pv_forecast,percentile=P50,run_time=2026-01-06T12:00:00Z,model=hybrid power_w=2520,energy_wh=1215 1736169300000000000
pv_forecast,percentile=P10,run_time=2026-01-06T12:00:00Z,model=hybrid power_w=1890,energy_wh=472 1736168400000000000
pv_forecast,percentile=P90,run_time=2026-01-06T12:00:00Z,model=hybrid power_w=2780,energy_wh=695 1736168400000000000
```

#### 6.12.5 Grafana Visualization

The forecast can be visualized in Grafana with future timestamps:

```flux
// Query forecast with uncertainty band
from(bucket: "EnergyV1")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r._field == "power_w")
  |> pivot(rowKey: ["_time"], columnKey: ["percentile"], valueColumn: "_value")
```

**Visualization options:**
- P50 as main line
- P10-P90 as shaded uncertainty band
- Overlay actual production for comparison

#### 6.12.6 Forecast Accuracy Tracking

Each forecast run is tagged with `run_time` to enable accuracy analysis:

```flux
// Compare forecast vs actual for a specific run
forecast = from(bucket: "EnergyV1")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.percentile == "P50")
  |> filter(fn: (r) => r.run_time == "2026-01-05T12:00:00Z")

actual = from(bucket: "EnergyV1")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._measurement == "Energy")
  |> filter(fn: (r) => r._field == "solar_ac_total_power")

// Join and calculate error
join(tables: {forecast: forecast, actual: actual}, on: ["_time"])
  |> map(fn: (r) => ({r with error: r._value_forecast - r._value_actual}))
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

## 17. Home Assistant

Home Assistant serves as the central hub for device integration, data collection, and visualization.
This chapter describes the dashboard configurations for monitoring the energy system.

### 17.1 Energy Dashboard

The built-in HA Energy Dashboard provides historical energy tracking with daily, weekly, and monthly views.
It requires sensors with specific attributes for proper statistics calculation.

#### 17.1.1 Requirements

Sensors must have:
- `state_class: total_increasing` (handles meter resets correctly)
- `device_class: energy`
- `unit_of_measurement: kWh`

#### 17.1.2 Customizations

Some sensors require state_class override via `/config/customize.yaml`:

```yaml
# Energy sensor state_class fixes for HA Energy Dashboard compatibility
sensor.enphase_energy_total:
  state_class: total_increasing

sensor.inverter_total_yield:
  state_class: total_increasing
```

Configuration reference in `/config/configuration.yaml`:
```yaml
homeassistant:
  customize: !include customize.yaml
```

#### 17.1.3 Current Configuration

The Energy Dashboard is configured in `/config/.storage/energy`:

| Category | Sensor | Price (2026) |
|----------|--------|--------------|
| **Grid import** | `sensor.power_meter_consumption` | 0.2962 CHF/kWh |
| **Grid export** | `sensor.power_meter_exported` | 0.2252 CHF/kWh |
| **Solar (Huawei)** | `sensor.inverter_total_yield` | - |
| **Solar (Enphase)** | `sensor.enphase_energy_total` | - |
| **Battery charge** | `sensor.battery_day_charge` | - |
| **Battery discharge** | `sensor.battery_day_discharge` | - |

**Solar Forecast:** No external forecast services (e.g., Forecast.Solar) are used.
Solar forecasts are generated by the custom MeteoSwiss/pvlib system (see Chapter 6).

#### 17.1.4 Individual Devices

For detailed consumption tracking, add individual device sensors:
- `sensor.phase_1_energy` / `phase_2_energy` / `phase_3_energy` (Shelly 3EM per-phase)
- `sensor.evcc_stat_total_charged_kwh` (EV total charged)

### 17.2 Power Flow Dashboard

Real-time power visualization using custom Lovelace cards.

#### 17.2.1 Installed Cards (via HACS)

| Card | Purpose |
|------|---------|
| `power-flow-card-plus` | Animated power flow diagram |
| `apexcharts-card` | Custom power/energy graphs |

#### 17.2.2 Power Flow Card Plus Configuration

Recommended configuration for the system:

```yaml
type: custom:power-flow-card-plus
entities:
  grid:
    entity: sensor.power_meter_active_power
    invert_state: true  # negative = export
  solar:
    entity: sensor.solar_pv_total_ac_power
    display_zero_state: true
  battery:
    entity: sensor.battery_charge_discharge_power
    state_of_charge: sensor.battery_state_of_capacity
  home:
    entity: sensor.load_power
  individual:
    - entity: sensor.evcc_actec_charge_power
      name: EV
      icon: mdi:car-electric
    - entity: sensor.enphase_energy_power
      name: Enphase
      icon: mdi:solar-panel
watt_threshold: 50
display_zero_lines:
  mode: show
```

#### 17.2.3 Power Flow Visualization

```
                    ┌─────────┐
                    │  Solar  │
                    │ (total) │
                    └────┬────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
    ┌─────────┐    ┌─────────┐    ┌─────────┐
    │  Grid   │◄──►│  Home   │◄──►│ Battery │
    │         │    │  Load   │    │  (SOC)  │
    └─────────┘    └────┬────┘    └─────────┘
                        │
              ┌─────────┴─────────┐
              │                   │
         ┌────▼────┐         ┌────▼────┐
         │   EV    │         │ Enphase │
         │Wallbox  │         │  (PV)   │
         └─────────┘         └─────────┘
```

**Flow direction indicators:**
- Arrow animation shows power flow direction
- Colors indicate import (red) vs export (green) vs self-consumption (yellow)
- SOC percentage displayed on battery icon

#### 17.2.4 Entity Summary for Dashboards

| Entity | Type | Unit | Dashboard Use |
|--------|------|------|---------------|
| `sensor.power_meter_active_power` | Power | W | Grid flow (neg=export) |
| `sensor.solar_pv_total_ac_power` | Power | W | Solar production |
| `sensor.battery_charge_discharge_power` | Power | W | Battery flow |
| `sensor.battery_state_of_capacity` | State | % | Battery SOC |
| `sensor.load_power` | Power | W | Home consumption |
| `sensor.evcc_actec_charge_power` | Power | kW | EV charging |
| `sensor.enphase_energy_power` | Power | W | Enphase solar |

---

**End of document**
