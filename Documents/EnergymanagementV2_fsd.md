# Energy Management System
## Functional Specification Document (FSD)

**Project:** Intelligent energy management with PV, battery, EV, and tariffs
**Location:** Lausen (BL), Switzerland
**Version:** 2.33
**Status:** Active Development
**Architecture:** 3 Home Assistant Add-ons
**Data Storage:** InfluxDB

---

# Chapter 1: System Overview

## 1.1 Purpose

This document describes an intelligent energy management system that optimizes household energy usage through:

- **PV Power Forecasting** - Probabilistic solar production forecasts (P10/P50/P90)
- **Load Forecasting** - Statistical consumption predictions based on historical patterns
- **Energy Optimization** - Rolling forecast with rule-based control of battery, EV charging, and deferrable loads

The system minimizes electricity costs while maximizing self-consumption and respecting device constraints.

## 1.2 Architecture Overview

The system consists of three Home Assistant add-ons that work together:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Home Assistant                                   │
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │ SwissSolarFore- │  │   LoadForecast  │  │     EnergyManager       │  │
│  │      cast       │  │                 │  │                         │  │
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

| Add-on | Purpose | Update Frequency |
|--------|---------|------------------|
| **SwissSolarForecast** | PV power forecasting using MeteoSwiss ICON ensemble data | Every 15 min (calculator) |
| **LoadForecast** | Statistical load power forecasting | Every hour |
| **EnergyManager** | Battery/EV/appliance optimization signals | Every 15 min |

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
│ • Calculate PV  │                    │ • Generate 120h │
│   with pvlib    │                    │   forecast      │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         │ Write to pv_forecast                 │ Write to load_forecast
         ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            InfluxDB                                  │
│                                                                      │
│  pv_forecast bucket          load_forecast bucket                   │
│  • power_w_p10/p50/p90       • power_w_p10/p50/p90                 │
│  • energy_wh_p10/p50/p90     • Per 15-min periods                  │
│  • Per-inverter data         • 120h (5-day) horizon                 │
│  • 120h (5-day) horizon                                             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               │ Query forecasts + measurements
                               ▼
                    ┌─────────────────────┐
                    │   EnergyManager     │
                    │                     │
                    │ • Read forecasts    │
                    │ • Read current SOC  │
                    │ • Apply tariffs     │
                    │ • Calculate signals │
                    │ • Output to HA      │
                    └──────────┬──────────┘
                               │
                               ▼
                    Battery / Wallbox / Appliance
                         Control Signals
```

## 1.5 Infrastructure

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| Home Assistant | 192.168.0.202 | 8123 | Device integration, add-on host |
| InfluxDB | 192.168.0.203 | 8087 | Time series storage |
| Grafana | 192.168.0.203 | 3000 | Visualization |
| MQTT Broker | 192.168.0.203 | 1883 | IoT messaging (Enphase, Wallbox → Modbus Proxy) |

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
                      │      Wallbox          │  EV charging (outside solar control loop)
                      │   (AcTec / OCPP)      │
                      └───────────────────────┘
                                  │
                      ┌───────────────────────┐
                      │   Huawei Smartmeter   │  DTSU666-H (RS485 to SUN2000)
                      └───────────────────────┘
                                  │
                      ┌───────────────────────┐
                      │   ESP32 Modbus Proxy  │  Intercepts RS485, adds wallbox power
                      │   (ESP32-C3)          │  corrected = dtsu + wallbox_power
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

**Modbus Proxy Power Correction:** The wallbox is wired between the grid and the DTSU meter, so the SUN2000 doesn't see wallbox consumption. Without correction, the SUN2000 would see grid export when the wallbox is actually importing from the grid. The ESP32 Modbus Proxy sits on the RS485 bus between the DTSU and SUN2000, intercepts meter responses, and adds the wallbox power: `corrected = dtsu_power + wallbox_power`. The wallbox power arrives via MQTT (topic `wallbox`) published by the OCPP Server add-on every 10 seconds. See [Modbus-Proxy-FSD.md](Modbus-Proxy-FSD.md) for the full ESP32 specification.

**Example** (wallbox charging at 4343W, PV covering house load):
```
DTSU measures:     -4300 W  (house exporting PV surplus)
Wallbox (MQTT):    +4343 W  (actual wallbox consumption)
SUN2000 sees:         43 W  (≈ balanced — correct!)
Without correction: -4300 W  (SUN2000 would think grid is exporting)
```

## 1.8 Home Assistant Entities

### 1.8.1 Power Measurements (W) - Real-time

**Solar Production:**

| Entity ID | Description | Controller Use |
|-----------|-------------|---------|
| `sensor.inverter_input_power` | DC input (both strings) | PV production |
| `sensor.inverter_pv_1_power` | String 1 power | Per-string monitoring |
| `sensor.inverter_pv_2_power` | String 2 power | Per-string monitoring |
| `sensor.inverter_active_power` | Huawei inverter AC output | Huawei only |
| `sensor.solar_pv_total_ac_power` | Total AC output (Huawei + Enphase) | **Primary PV input** |
| `sensor.enphase_energy_power` | Enphase microinverter power | Secondary PV |

**Battery:**

| Entity ID | Description | Controller Use |
|-----------|-------------|---------|
| `sensor.battery_charge_discharge_power` | Charge/discharge power (+/-) | **Battery flow** |

**Grid:**

| Entity ID | Description | Controller Use |
|-----------|-------------|---------|
| `sensor.power_meter_active_power` | Grid power (neg=export) | **Critical: Grid flow** |
| `sensor.power_meter_phase_a_active_power` | Phase A power | Load balancing |
| `sensor.power_meter_phase_b_active_power` | Phase B power | Load balancing |
| `sensor.power_meter_phase_c_active_power` | Phase C power | Load balancing |

**Load (calculated):**

| Entity ID | Description | Controller Use |
|-----------|-------------|---------|
| `sensor.house_load_power` | House consumption (Shelly 3EM, 3-phase sum) | **Critical: Load input** |
| `sensor.total_load_power` | Total consumption incl. wallbox (house + EV) | Display on Fire tablet |
| `sensor.surplus_power` | Solar surplus (solar - house_load) | EV strategy input |

**Note:** `sensor.house_load_power` is the sum of Shelly 3EM phase measurements (template sensor `load_total_power` renamed in entity registry). The Huawei Solar integration also calculates a load value:
```
load = solar_pv_total_ac_power - power_meter_active_power + battery_charge_discharge_power
```

**Shelly 3EM (direct measurement):**

| Entity ID | Description | Phase |
|-----------|-------------|-------|
| `sensor.phase_1_power` | Phase A Power | L1 |
| `sensor.phase_2_power` | Phase B Power | L2 |
| `sensor.phase_3_power` | Phase C Power | L3 |
| `sensor.phase_1_current` | Phase A Current | L1 |
| `sensor.phase_2_current` | Phase B Current | L2 |
| `sensor.phase_3_current` | Phase C Current | L3 |
| `sensor.phase_1_voltage` | Phase A Voltage | L1 |
| `sensor.phase_2_voltage` | Phase B Voltage | L2 |
| `sensor.phase_3_voltage` | Phase C Voltage | L3 |

### 1.8.2 Energy Measurements (kWh) - Totals

**Solar Production:**

| Entity ID | Description | Use |
|-----------|-------------|-----|
| `sensor.inverter_daily_yield` | Today's production | Daily reporting |
| `sensor.inverter_total_yield` | Lifetime AC yield | System totals |
| `sensor.inverter_total_dc_input_energy` | Lifetime DC input | Efficiency calc |
| `sensor.solar_pv_total_ac_energy` | Total AC energy | System totals |
| `sensor.enphase_energy_total` | Enphase lifetime | System totals |
| `sensor.enphase_energy_today` | Enphase today | Daily reporting |

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

**Load:**

| Entity ID | Description | Use |
|-----------|-------------|-----|
| `sensor.load_energy` | Total consumption | Historical analysis |
| `sensor.phase_1_energy` | Phase A total | Per-phase tracking |
| `sensor.phase_2_energy` | Phase B total | Per-phase tracking |
| `sensor.phase_3_energy` | Phase C total | Per-phase tracking |

### 1.8.3 Battery State and Control

**State:**

| Entity ID | Description | Unit | Controller Use |
|-----------|-------------|------|---------|
| `sensor.battery_state_of_capacity` | State of charge | % | **Critical: SOC for controller** |
| `sensor.battery_bus_voltage` | Battery voltage | V | Health monitoring |

**Control (Outputs):**

| Entity ID | Description | Unit | Controller Use |
|-----------|-------------|------|---------|
| `number.battery_maximum_discharging_power` | Max discharge limit | W | **Night strategy control** |
| `number.battery_maximum_charging_power` | Max charge limit | W | Charge limiting |
| `number.battery_end_of_discharge_soc` | Min SOC limit | % | SOC protection |
| `number.battery_end_of_charge_soc` | Max SOC limit | % | SOC protection |
| `select.battery_working_mode` | Operating mode | - | Mode selection |

### 1.8.4 Enphase MQTT Integration

The Enphase microinverters publish via MQTT (Tasmota format):

**MQTT Topics:**
- `tele/Enphase/SENSOR` - Energy data (every ~5 minutes)
- `tele/Enphase/STATE` - Device state, WiFi info
- `tele/Enphase/LWT` - Online/Offline status

**MQTT Payload Example:**
```json
{
  "Time": "2026-01-07T10:30:00",
  "ENERGY": {
    "TotalStartTime": "2023-02-11T10:09:42",
    "Total": 3511.448,
    "Yesterday": 6.986,
    "Today": 0.612,
    "Power": 450,
    "ApparentPower": 460,
    "ReactivePower": 50,
    "Factor": 0.98,
    "Voltage": 237,
    "Current": 1.94
  }
}
```

### 1.8.5 Energy Balance Calculation

```
Grid Power = PV Production - Load + Battery Discharge - Battery Charge

Where:
  PV Production = sensor.inverter_active_power + sensor.enphase_energy_power
  Load = sensor.house_load_power (Shelly 3EM 3-phase sum)
  Battery = sensor.battery_charge_discharge_power (+ = discharge, - = charge)
```

### 1.8.6 HA Energy Dashboard Configuration

The HA Energy Dashboard requires sensors with `state_class: total_increasing`:

| Category | Sensor | Price (2026) |
|----------|--------|--------------|
| **Grid import** | `sensor.power_meter_consumption` | 0.2962 CHF/kWh |
| **Grid export** | `sensor.power_meter_exported` | 0.2252 CHF/kWh |
| **Solar (Huawei)** | `sensor.inverter_total_yield` | - |
| **Solar (Enphase)** | `sensor.enphase_energy_total` | - |
| **Battery charge** | `sensor.battery_day_charge` | - |
| **Battery discharge** | `sensor.battery_day_discharge` | - |

**Customizations** (`/config/customize.yaml`):
```yaml
sensor.enphase_energy_total:
  state_class: total_increasing

sensor.inverter_total_yield:
  state_class: total_increasing
```

### 1.8.7 Power Flow Card Plus Configuration

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
    - entity: sensor.wallbox_power
      name: EV
      icon: mdi:car-electric
    - entity: sensor.enphase_energy_power
      name: Enphase
      icon: mdi:solar-panel
watt_threshold: 50
display_zero_lines:
  mode: show
```

## 1.9 Sign Conventions

All power values follow a consistent sign convention. The canonical reference table below defines positive/negative meaning for every quantity used in the system.

| Quantity | Entity | Positive (+) | Negative (−) | Typical range |
|----------|--------|-------------|--------------|---------------|
| Grid power | `sensor.power_meter_active_power` | Export (to grid) | Import (from grid) | −11 000 … +11 000 W |
| Battery power | `sensor.battery_charge_discharge_power` | Discharge (to house) | Charge (from PV/grid) | −5 000 … +5 000 W |
| PV production | `sensor.solar_pv_total_ac_power` | Generation | *(never negative)* | 0 … 12 000 W |
| House load | `sensor.house_load_power` | Consumption | *(never negative)* | 0 … 10 000 W |
| Wallbox power | `sensor.wallbox_power` | Consumption | *(never negative)* | 0 … 11 000 W |
| Net energy (forecast) | calculated `net_energy_wh` | Surplus (PV > Load) | Deficit (PV < Load) | — |
| Excess power (EV) | calculated | Available for EV | Grid import needed | — |

### 1.9.1 Key Formulas with Sign Logic

| Formula | Location | Logic |
|---------|----------|-------|
| `excess = grid_power + wallbox_power` | run.py EV loop | Grid positive = export; adds back wallbox's own draw when already capturing |
| `net_energy_wh = pv_wh − load_wh` | forecast_reader.py | Positive = surplus to charge battery |
| `excess_power = pv − load` | appliance_signal.py | Positive = surplus available for appliance |

### 1.9.2 Sanity Invariants

The following invariants should hold under normal operation. Runtime sanity checks in `energymanager/src/sanity.py` validate these and log warnings on violation (but never block control):

1. **PV ≥ 0, Load ≥ 0, Wallbox ≥ 0** — always; negative values indicate sensor fault
2. **At midday with PV > 2 000 W and no wallbox load** — grid should typically be positive (exporting)
3. **|grid| should not exceed ~15 000 W** — above this suggests sensor fault (PV peak + battery max ≈ 17 kW)

## 1.10 Design Principles

1. **Deterministic Core Logic** - All numerical calculations produce identical results for identical inputs
2. **Probabilistic Uncertainty** - P10/P50/P90 percentiles quantify forecast uncertainty
3. **InfluxDB as Single Source of Truth** - All data stored as time series
4. **Rolling Horizon** - Decisions recalculated every 5-15 minutes
5. **Decoupled Components** - Each add-on operates independently with clear interfaces
6. **Power for Storage, Energy for Calculations** - Forecasts stored as Power (W), converted to Energy (Wh) only when needed

## 1.11 Data Units and Flow

All forecasts are stored and displayed in **Power (W)**. Energy (Wh) is calculated internally when needed for simulations.

```
┌─────────────────────────────────────────────────────────────┐
│  FORECASTS (stored in InfluxDB & displayed in Grafana)      │
│                                                             │
│    pv_forecast:   power_w_p10, power_w_p50, power_w_p90    │
│    load_forecast: power_w_p10, power_w_p50, power_w_p90    │
│                                                             │
│    Unit: Watts (W)                                          │
│    Meaning: Instantaneous power at each 15-min timestamp    │
└─────────────────────────────────────────────────────────────┘
                            │
                            │  × 0.25h (per 15-min step)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  CALCULATIONS (internal to EnergyManager)                   │
│                                                             │
│    pv_energy_wh   = pv_power_w × 0.25                      │
│    load_energy_wh = load_power_w × 0.25                    │
│    net_wh         = pv_energy_wh - load_energy_wh          │
│                                                             │
│    Unit: Watt-hours (Wh)                                    │
│    Meaning: Energy transferred per 15-min period            │
└─────────────────────────────────────────────────────────────┘
                            │
                            │  accumulate over time
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  SIMULATION OUTPUTS (stored in InfluxDB)                    │
│                                                             │
│    soc_forecast:  soc_percent at each timestamp            │
│    Energy Balance: cumulative Wh over forecast horizon     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Why Power (W) for storage:**
- Directly comparable to sensor readings
- No ambiguity about time periods
- Human intuition: "PV producing 5000W" is clearer than "1250Wh per 15-min"

**Why Energy (Wh) for calculations:**
- SOC changes require energy: `SOC += Wh × efficiency`
- Cost calculations: `cost = kWh × price`

## 1.12 Home Assistant Add-on Architecture

This section describes the canonical Home Assistant add-on configuration architecture used by all add-ons in this project.

### 1.12.1 Configuration Philosophy

Home Assistant add-ons follow a specific pattern for configuration management:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Configuration Architecture                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   SECRETS                              NON-SECRETS                          │
│   (tokens, passwords)                  (settings, options)                  │
│                                                                              │
│   ┌─────────────────────┐              ┌─────────────────────────────────┐  │
│   │  HA Configuration   │              │  /config/<addon>.yaml           │  │
│   │       Tab           │              │  (Public Add-on Config)         │  │
│   │                     │              │                                 │  │
│   │  • Masked fields    │              │  • Editable via File Editor    │  │
│   │  • Secure storage   │              │  • Editable via VS Code        │  │
│   │  • Never in files   │              │  • Version controlled          │  │
│   └──────────┬──────────┘              └───────────────┬─────────────────┘  │
│              │                                         │                    │
│              │ bashio::config                          │ YAML load          │
│              │ → Environment vars                      │                    │
│              ▼                                         ▼                    │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                         Python Runtime                                │  │
│   │                                                                       │  │
│   │   config = load_yaml("/config/addon.yaml")                           │  │
│   │   config["influxdb"]["token"] = os.environ["INFLUXDB_TOKEN"]         │  │
│   │   config["telegram"]["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"] │  │
│   │                                                                       │  │
│   │   # Final merged config ready for use                                 │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.12.2 Secrets (HA Configuration UI)

Secrets are sensitive values that should **never** be stored in YAML files.

**What qualifies as a secret:**
- API tokens (InfluxDB, external services)
- Passwords and credentials
- Bot tokens (Telegram, Discord)
- Private keys

**How secrets are configured:**

1. User opens **Settings → Add-ons → [Add-on] → Configuration**
2. User enters secrets in masked password fields
3. HA Supervisor stores secrets securely in `/data/options.json`
4. Startup script reads via `bashio::config` and exports as environment variables
5. Python reads from `os.environ`

**Example `config.yaml` schema:**

```yaml
options:
  influxdb_token: ""
  telegram_bot_token: ""
  telegram_chat_id: ""

schema:
  influxdb_token: password
  telegram_bot_token: password?
  telegram_chat_id: str?
```

**Example startup script:**

```bash
#!/command/with-contenv bashio

# Read secrets from HA Configuration UI
if bashio::config.has_value 'influxdb_token'; then
  export INFLUXDB_TOKEN="$(bashio::config 'influxdb_token')"
fi

if bashio::config.has_value 'telegram_bot_token'; then
  export TELEGRAM_BOT_TOKEN="$(bashio::config 'telegram_bot_token')"
fi

exec python3 /app/run.py --config "/config/addon.yaml"
```

### 1.12.3 Non-Secrets (Public Add-on Config)

All non-sensitive configuration is stored in user-editable YAML files.

**Storage location:**

| Context | Path |
|---------|------|
| Inside container | `/config/<addon>.yaml` |
| HA File Editor / VS Code | `/addon_configs/<addon_slug>/<addon>.yaml` |
| Host filesystem | `/usr/share/hassio/addon_configs/<addon_slug>/` |

**Enable with `map` in `config.yaml`:**

```yaml
map:
  - addon_config:rw
```

**What goes in YAML config:**
- Connection settings (host, port, org - but NOT tokens)
- Device settings (battery capacity, entity IDs)
- Schedule settings (tariff times, intervals)
- Feature flags and options
- Logging level

**Example user config (`/config/energymanager.yaml`):**

```yaml
# InfluxDB connection (token in Configuration tab, not here!)
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"

# Battery settings
battery:
  capacity_kwh: 10.0
  discharge_control_entity: "number.battery_maximum_discharging_power"

# Tariff schedule
tariff:
  weekday_cheap_start: "21:00"
  weekday_cheap_end: "06:00"
```

### 1.12.4 Templates and Defaults

Each add-on ships with a template/example configuration.

**Template location:** `/usr/share/<addon>/<addon>.yaml.example`

**Behavior:**

| Event | Action |
|-------|--------|
| First run (no user config) | Copy template → `/config/<addon>.yaml` |
| Every start | Copy template → `/config/<addon>.yaml.example` |
| Update/upgrade | **Never** overwrite user config |
| New options added | Handle via defaults in code, update `.example` |

**Example startup script:**

```bash
USER_CONFIG="/config/energymanager.yaml"
TEMPLATE="/usr/share/energymanager/energymanager.yaml.example"

# First run: create user config from template
if [ ! -f "$USER_CONFIG" ]; then
  cp "$TEMPLATE" "$USER_CONFIG"
  bashio::log.warning "Created $USER_CONFIG - please edit and restart"
fi

# Always refresh the example (shows new options after updates)
cp "$TEMPLATE" "/config/energymanager.yaml.example"
```

### 1.12.5 Configuration Merge Order

At runtime, configuration is assembled in this order:

```
1. Load defaults from template
   └─► /usr/share/addon/addon.yaml.example

2. Load user config (overrides defaults)
   └─► /config/addon.yaml

3. Overlay secrets from environment (overrides everything)
   └─► INFLUXDB_TOKEN, TELEGRAM_BOT_TOKEN, etc.

4. Apply code defaults for missing keys
   └─► config.get("key", default_value)

Final: Merged configuration ready for use
```

**Python implementation:**

```python
def load_config(config_path: str) -> dict:
    # 1. Load defaults
    defaults = yaml.safe_load(open("/usr/share/addon/addon.yaml.example"))

    # 2. Load user config
    user_config = yaml.safe_load(open(config_path))

    # 3. Deep merge (user wins)
    merged = deep_merge(defaults, user_config)

    # 4. Overlay secrets from environment
    if os.environ.get("INFLUXDB_TOKEN"):
        merged["influxdb"]["token"] = os.environ["INFLUXDB_TOKEN"]

    return merged
```

### 1.12.6 Add-on Configuration Files Summary

| Add-on | Secrets (Config UI) | Non-Secrets (YAML) |
|--------|--------------------|--------------------|
| **EnergyManager** | `influxdb_token`, `telegram_bot_token`, `telegram_chat_id` | `/config/energymanager.yaml` |
| **SwissSolarForecast** | `influxdb_token`, `telegram_bot_token`, `telegram_chat_id` | `/config/swisssolarforecast.yaml` |
| **LoadForecast** | `influxdb_token` | `/config/loadforecast.yaml` |

### 1.12.7 User Workflow

**Initial Setup:**

1. Install add-on from repository
2. Go to **Configuration** tab → Enter secrets (tokens)
3. Click **Save**
4. Start add-on (creates default config file)
5. Edit `/addon_configs/<slug>/<addon>.yaml` via File Editor
6. Restart add-on

**After Updates:**

1. Add-on updates automatically (if enabled)
2. User config is **never** modified
3. Check `/config/<addon>.yaml.example` for new options
4. Manually add desired new options to user config
5. Restart add-on

### 1.12.8 Best Practices Summary

| Practice | Do | Don't |
|----------|----|----- |
| **Secrets** | Store in HA Configuration UI | Put in YAML files |
| **User config** | Let user edit via File Editor | Auto-modify user files |
| **Defaults** | Apply in code for missing keys | Require all keys in user config |
| **Updates** | Refresh `.example` file | Overwrite user config |
| **Logging** | Log "token loaded" (not the value) | Log secret values |

---

## 1.13 Complete Parameter Reference

### 1.13.1 EnergyManager Parameters

**Secrets (Configuration UI):**

| Parameter | Schema Type | Description |
|-----------|-------------|-------------|
| `influxdb_token` | `password` | InfluxDB API token |
| `telegram_bot_token` | `password?` | Telegram bot token (optional) |
| `telegram_chat_id` | `str?` | Telegram chat ID (optional) |

**Non-Secrets (`/config/energymanager.yaml`):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `influxdb.host` | 192.168.0.203 | InfluxDB server IP/hostname |
| `influxdb.port` | 8087 | InfluxDB HTTP port |
| `influxdb.org` | energymanagement | InfluxDB organization |
| `influxdb.pv_bucket` | pv_forecast | PV forecast bucket |
| `influxdb.load_bucket` | load_forecast | Load forecast bucket |
| `influxdb.output_bucket` | energy_manager | Output bucket for decisions |
| `influxdb.soc_bucket` | HomeData | Bucket with actual SOC data |
| `influxdb.soc_measurement` | Energy | Measurement name for SOC |
| `influxdb.soc_field` | BATT_Level | Field name for SOC value |
| `battery.capacity_kwh` | 10.0 | Usable battery capacity |
| `battery.reserve_percent` | 10 | Minimum SOC reserve |
| `battery.charge_efficiency` | 0.95 | Charging efficiency (0-1) |
| `battery.discharge_efficiency` | 0.95 | Discharging efficiency (0-1) |
| `battery.max_charge_w` | 5000 | Max charge power (W) |
| `battery.max_discharge_w` | 5000 | Max discharge power (W) |
| `battery.soc_entity` | sensor.battery_state_of_capacity | HA entity for current SOC |
| `battery.discharge_control_entity` | number.battery_maximum_discharging_power | HA entity for discharge control |
| `tariff.weekday_cheap_start` | 21:00 | Low tariff start (HH:MM) |
| `tariff.weekday_cheap_end` | 06:00 | Low tariff end (HH:MM) |
| `tariff.weekend_all_day_cheap` | true | Weekend uses low tariff |
| `tariff.holidays` | [] | Holiday dates (low tariff) |
| `appliances.power_w` | 2500 | Deferrable appliance power |
| `appliances.energy_wh` | 1500 | Appliance energy per cycle |
| `ev_charging.min_power_w` | 4100 | Min EV charging power |
| `ev_charging.max_power_w` | 11000 | Max EV charging power |
| `schedule.update_interval_minutes` | 15 | Optimization cycle interval |
| `log_level` | info | Logging level |

**Fixed (not configurable):**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `home_assistant.url` | http://supervisor/core | HA API URL (via Supervisor) |
| `home_assistant.token` | SUPERVISOR_TOKEN env | Auto-provided by HA |

### 1.13.2 SwissSolarForecast Parameters

**Secrets (Configuration UI):**

| Parameter | Schema Type | Description |
|-----------|-------------|-------------|
| `influxdb_token` | `password` | InfluxDB API token |
| `telegram_bot_token` | `password?` | Telegram bot token (optional) |
| `telegram_chat_id` | `str?` | Telegram chat ID (optional) |

**Non-Secrets (`/config/swisssolarforecast.yaml`):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `influxdb.host` | 192.168.0.203 | InfluxDB server IP/hostname |
| `influxdb.port` | 8087 | InfluxDB HTTP port |
| `influxdb.org` | energymanagement | InfluxDB organization |
| `influxdb.bucket` | pv_forecast | Output bucket name |
| `location.latitude` | 47.475 | PV installation latitude |
| `location.longitude` | 7.767 | PV installation longitude |
| `location.altitude` | 330 | Altitude (m) |
| `location.timezone` | Europe/Zurich | Local timezone |
| `panels[]` | - | Panel definitions (id, model, pdc0, gamma_pdc) |
| `plants[]` | - | Plant definitions (inverters, strings) |
| `log_level` | info | Logging level |

### 1.13.3 LoadForecast Parameters

**Secrets (Configuration UI):**

| Parameter | Schema Type | Description |
|-----------|-------------|-------------|
| `influxdb_token` | `password` | InfluxDB API token |

**Non-Secrets (`/config/loadforecast.yaml`):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `influxdb.host` | 192.168.0.203 | InfluxDB server IP/hostname |
| `influxdb.port` | 8087 | InfluxDB HTTP port |
| `influxdb.org` | energymanagement | InfluxDB organization |
| `influxdb.source_bucket` | HomeAssistant | Bucket with historical load data |
| `influxdb.target_bucket` | load_forecast | Output bucket name |
| `load_sensor.entity_id` | sensor.house_load_power | HA entity ID for load power |
| `forecast.history_days` | 90 | Days of history for profile |
| `forecast.horizon_hours` | 120 | Forecast horizon (hours) — matches PV forecast |
| `schedule.cron` | 15 * * * * | Cron schedule for forecast runs |
| `log_level` | info | Logging level |

---

# Chapter 2: SwissSolarForecast Add-on

## 2.1 Overview

SwissSolarForecast generates probabilistic PV power forecasts using MeteoSwiss ICON ensemble weather data and the pvlib solar modeling library. It produces P10/P50/P90 percentile forecasts for each inverter and the total system.

| Property | Value |
|----------|-------|
| Name | SwissSolarForecast |
| Slug | `swisssolarforecast` |
| Architectures | aarch64, amd64, armv7 |
| Timeout | 300 seconds |
| Storage | /share/swisssolarforecast (GRIB data) |

## 2.2 Features

- **Weather Data**: MeteoSwiss ICON-CH1 (1km, 33h) and ICON-CH2 (2.1km, 120h) ensemble forecasts
- **Ensemble Members**: 11 (CH1) or 21 (CH2) members for uncertainty quantification
- **Output**: P10/P50/P90 percentiles at 15-minute resolution
- **Per-Inverter**: Separate forecasts for each inverter (EastWest, South)
- **Independent**: Writes only PV forecast data (energy balance calculated by EnergyManager)
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
│  │ 5. Write to InfluxDB pv_forecast bucket                       │  │
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

**Model Selection Strategy:**
- **Today's forecast:** Use ICON-CH1-EPS (higher resolution, sufficient horizon)
- **Tomorrow's forecast:** Use ICON-CH2-EPS (longer horizon needed)
- **Hybrid mode:** CH1 for hours 0-33, CH2 for hours 33-60

## 2.5 STAC API Integration

**Provider:** MeteoSwiss (Federal Office of Meteorology and Climatology)

**Access:** Open Government Data (OGD) via STAC API (SpatioTemporal Asset Catalog)

**API Endpoint:** `https://data.geo.admin.ch/api/stac/v1`

**Collections:**
- `ch.meteoschweiz.ogd-forecasting-icon-ch1` (ICON-CH1-EPS)
- `ch.meteoschweiz.ogd-forecasting-icon-ch2` (ICON-CH2-EPS)

### 2.5.1 STAC API Query Example

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

**Horizon format:** ISO 8601 duration `P{days}DT{hours}H{minutes}M{seconds}S`
- Hour 0: `P0DT00H00M00S`
- Hour 12: `P0DT12H00M00S`
- Hour 36: `P1DT12H00M00S`

### 2.5.2 GRIB File Naming Convention

Downloaded GRIB files follow this naming pattern:
```
icon-{model}-{YYYYMMDDHHMM}-h{HHH}-{variable}-{member}.grib2
```

**Examples:**
- `icon-ch1-202601070300-h012-asob_s-m00.grib2` (CH1, 03:00 run, hour 12, GHI, control)
- `icon-ch1-202601070300-h012-asob_s-perturbed.grib2` (CH1, all perturbed members)
- `icon-ch2-202601070600-h048-t_2m-m00.grib2` (CH2, 06:00 run, hour 48, temp, control)

**Member naming:**
- `m00` = Control member (single GRIB message)
- `perturbed` = All perturbed members (10 messages for CH1, 20 for CH2)

### 2.5.3 Grid Handling

ICON uses an unstructured triangular grid, not a regular lat/lon grid:

**Grid coordinates:**
- Stored in a separate "horizontal constants" GRIB file
- Variables: `tlat` (latitude), `tlon` (longitude) for each grid point
- Coordinates are in radians, converted to degrees

**Value extraction:**
- Find nearest grid point to target location using Euclidean distance
- Cache grid coordinates locally to avoid repeated downloads
- Grid cache location: `/tmp/meteoswiss_grib/grid_coords_{model}.npz`

### 2.5.4 Data Volume and Storage

**Lite Mode (default):**
- 2 variables only: GHI (ASOB_S) + Temperature (T_2M)
- DNI/DHI derived from GHI using Erbs decomposition model
- Skip past hours: Only download future forecast hours

| Model | Hours | Files | Approx. Size |
|-------|-------|-------|--------------|
| ICON-CH1-EPS | 0-33 | 2 vars × 34 hours × 2 files = 136 files | ~1.6 GB |
| ICON-CH2-EPS | 33-60 | 2 vars × 28 hours × 2 files = 112 files | ~0.9 GB |
| **Total** | 0-60 | 248 files | **~2.5 GB** |

**Note:** CH2 extends to hour 60 (not 48) to ensure 48h forecast coverage despite CH1/CH2 run time offsets (CH1 runs every 3h, CH2 every 6h).

**Storage Policy:** Only the latest run is kept; older runs are automatically deleted before downloading.

### 2.5.5 Fault Tolerance

**Download failures:**
- Incomplete downloads saved as `.tmp` files
- Only `.grib2` files considered complete
- Failed downloads logged but don't abort the process
- Retry logic with exponential backoff

**Parsing flexibility:**
- Filename parsing supports multiple formats (12/14 digit timestamps)
- Date/time extracted from GRIB metadata (authoritative source)
- Variable names matched case-insensitively
- Unknown files skipped with warnings

**Data availability:**
- System checks for latest available run before downloading
- Falls back to older runs if latest not yet published
- Partial data sets can still be used (with reduced ensemble size)

## 2.6 PV System Configuration

Configuration is defined in `/config/swisssolarforecast.yaml` or via HA add-on options:

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
          - name: "East"
            azimuth: 103.3
            tilt: 15
            panel: "AE445"
            count: 8
          - name: "West"
            azimuth: 283.3
            tilt: 15
            panel: "AE490"
            count: 9

      - name: "South"
        max_power: 1500
        efficiency: 0.98
        strings:
          - name: "SouthFront"
            azimuth: 193.3
            tilt: 70
            panel: "Generic425"
            count: 3
          - name: "SouthBack"
            azimuth: 193.3
            tilt: 60
            panel: "Generic425"
            count: 2
```

**Calibration note (2026-03-20):** The Pdc0 values and inverter efficiencies are **model calibration parameters**, not physical panel specifications. The actual panels are unchanged (AE Solar AC-455MH nameplate 455W, Generic 400W). The calibrated values were derived by comparing per-string actual production against the pvlib clear-sky model on sunny days (Mar 17-20, 2026) at noon when no physical shading is present:

| String | Nameplate | Calibrated Pdc0 | Reason |
|--------|-----------|----------------|--------|
| East (PV1) | 455W | 445W | Model slightly over-predicts at noon; accounts for wiring/mismatch losses |
| West (PV2) | 455W | 490W | Panels consistently produce 19% above model with nameplate value; positive power tolerance or panel count uncertainty |
| South | 400W | 425W | Panels produce 15% above model with nameplate value |

Inverter efficiency raised from 0.95/0.96 to 0.98 for both inverters, matching Huawei SUN2000 and Enphase IQ7 datasheet peak efficiency. The previous values (0.95/0.96) were conservative estimates that compounded with Pdc0 errors.

## 2.7 Configuration

### Secrets (Configuration UI)

Enter in **Settings → Add-ons → SwissSolarForecast → Configuration**:
- `influxdb_token` (required)
- `telegram_bot_token` (optional)
- `telegram_chat_id` (optional)

### Home Assistant API Access

The add-on requires access to Home Assistant entities to record battery state with each forecast.

**Required in `config.yaml`:**
```yaml
homeassistant_api: true
```

**Entities Read:**

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.battery_state_of_capacity` | sensor | Battery SOC (%) |
| `number.battery_maximum_discharging_power` | number | Max discharge power setting (W), 0 = blocked |

These values are fetched via the Supervisor REST API (`http://supervisor/core/api/states/`) and recorded with every forecast write (every 15 minutes) to provide continuous battery state tracking.

### Non-Secrets (`/config/swisssolarforecast.yaml`)

```yaml
# NOTE: Token is configured in the Configuration tab, not here!
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"
  bucket: "pv_forecast"

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

log_level: "info"
```

## 2.8 InfluxDB Output Schema

**Measurement:** `pv_forecast`

**Resolution:** 15-minute intervals (aligned to :00, :15, :30, :45)

### Tags

| Tag | Values | Description |
|-----|--------|-------------|
| `inverter` | `total`, `EastWest`, `South` | Inverter identifier |
| `model` | `ch1`, `ch2`, `hybrid` | ICON model used |

### Fields (inverter="total")

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | PV power (pessimistic, 90% chance to exceed) |
| `power_w_p50` | W | PV power (expected/median) |
| `power_w_p90` | W | PV power (optimistic, 10% chance to exceed) |
| `energy_wh_p10` | Wh | Per-period energy (pessimistic) |
| `energy_wh_p50` | Wh | Per-period energy (expected) |
| `energy_wh_p90` | Wh | Per-period energy (optimistic) |
| `ghi` | W/m² | Global horizontal irradiance |
| `temp_air` | °C | Air temperature |
| `run_time` | ISO string | When forecast was calculated |
| `battery_soc` | % | Battery state of charge at forecast time |
| `discharge_power_limit` | W | Max discharge power setting (0 = blocked) |

### Fields (inverter="EastWest" or "South")

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | Inverter power (pessimistic) |
| `power_w_p50` | W | Inverter power (expected) |
| `power_w_p90` | W | Inverter power (optimistic) |

## 2.9 Calculation Pipeline

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

## 2.10 Source Files

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

## 2.11 Dependencies

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

## 2.12 Grafana Queries

**PV Power Forecast with uncertainty band:**
```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 120h)
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
  |> range(start: now(), stop: 120h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r._field == "power_w_p50")
  |> pivot(rowKey: ["_time"], columnKey: ["inverter"], valueColumn: "_value")
```

## 2.13 Shading Correction

### 2.13.1 Problem

Each PV string (East, West, South) experiences physical shading from buildings, trees, and roof edges at certain sun positions. The pvlib PV model calculates power assuming an unobstructed view of the sky. A per-hour, per-string shading factor corrects for the actual obstructions at the installation site.

Shading depends on **sun position** (elevation + azimuth), which changes with hour of day and season. Factors calibrated in February are wrong in June — the sun is much higher and shadows fall differently.

### 2.13.2 Architecture

The shading correction has three layers:

```
Layer 1: CLEAR-SKY REFERENCE (astronomy, no weather dependency)
  pvlib.clearsky.ineichen(location, times)
  → theoretical GHI with no clouds
  → PV model with clear-sky input, no shading
  → clearsky_power_w per string per hour

Layer 2: SUNNY HOUR DETECTION (per-hour, not per-day)
  actual_ghi / clearsky_ghi > 0.85?
  → yes: this hour is suitable for shading calibration
  → no: clouds present, skip this hour

Layer 3: SHADING FACTOR CALCULATION
  On sunny hours:
  shading_factor = actual_power_w / clearsky_power_w
  → ratio < 1.0 means physical shading
  → stored per string, per hour, per date
```

This design breaks the circular dependency of the previous approach: clear-sky GHI is pure astronomy (no forecast involved), so sunny-hour detection and shading calculation are independent of forecast quality.

### 2.13.3 Clear-Sky Reference

The clear-sky reference uses `pvlib.clearsky.ineichen()` to compute theoretical maximum GHI for the installation location (47.475°N, 7.767°E, 330m altitude). The same PV model pipeline (GHI decomposition → POA → PVWatts DC → inverter efficiency) is run with clear-sky input and **no shading factors**, producing `clearsky_power_w` for each string.

This reference changes only with sun position (time of year, time of day) and atmospheric clarity (Linke turbidity). It does not depend on weather forecasts, historical data, or the current shading factors.

### 2.13.4 Sunny Hour Detection

For each hour with solar production, compare actual GHI against clear-sky GHI:

```
ghi_ratio = actual_ghi / clearsky_ghi
sunny = ghi_ratio > 0.85
```

This is a **per-hour** decision, not per-day. A partly cloudy day can still contribute sunny hours at noon. The GHI comparison uses actual measured irradiance (from HomeAssistant sensor or derived from actual PV production) against the theoretical clear-sky value.

The 0.85 threshold allows for minor atmospheric effects (haze, thin cirrus) while excluding hours with significant cloud cover that would distort the shading measurement.

### 2.13.5 Shading Factor Calculation

On sunny hours, the shading factor for each string is:

```
shading_factor[string][hour] = actual_power_w / clearsky_power_w
```

| Factor | Meaning |
|--------|---------|
| 1.00 | No shading — string sees full sky |
| 0.80 | 20% of theoretical power lost to physical shading |
| 0.50 | Heavy shading — half the sky blocked |

Factors are aggregated over recent sunny observations (last 10 sunny hours per slot) using the median to reject outliers.

### 2.13.6 Strings

Each string has different shading patterns due to orientation and surroundings:

| String | Azimuth | Tilt | Expected pattern |
|--------|---------|------|-----------------|
| East | 103.3° | 15° | Shaded in morning (buildings to east), clears by midday |
| West | 283.3° | 15° | Clear in morning, may shade in late afternoon |
| South | 193.3° | 60°/70° | Least shading (high tilt catches midday sun) |

### 2.13.7 InfluxDB Storage

All observations are stored for later accuracy evaluation:

**Measurement:** `shading_observations` (bucket: `pv_forecast`)

| Field | Type | Description |
|-------|------|-------------|
| `string` | tag | East / West / South |
| `hour` | field (int) | Local hour (0–23) |
| `date` | tag | YYYY-MM-DD |
| `clearsky_ghi` | field (float) | Theoretical clear-sky GHI (W/m²) |
| `actual_ghi` | field (float) | Measured GHI (W/m²) |
| `ghi_ratio` | field (float) | actual_ghi / clearsky_ghi |
| `clearsky_power_w` | field (float) | PV model output with clear-sky, no shading |
| `actual_power_w` | field (float) | Actual string production (W) |
| `shading_factor` | field (float) | actual_power / clearsky_power |
| `sun_elevation` | field (float) | Solar elevation angle (°) |
| `sun_azimuth` | field (float) | Solar azimuth angle (°) |
| `is_sunny` | field (bool) | ghi_ratio > 0.85 |

### 2.13.8 Shading Factor Application

The forecast pipeline applies shading factors after the PV model computes clear-sky power:

```
forecasted_power = pvlib_model_output × shading_factor[string][hour]
```

Factors are loaded from `shading_factors.yaml`, which is updated automatically after each evaluation cycle (21:15 daily). The YAML file serves as a cache — if InfluxDB is unavailable, the last-written factors are used.

### 2.13.9 Seasonal Update

Shading factors change with sun elevation throughout the year. The system uses a rolling window of recent sunny observations (last 90 days, weighted toward recent). Factors from winter (low sun, long shadows) are gradually replaced by spring observations as new sunny hours accumulate.

### 2.13.10 Accuracy Evaluation

The stored observations enable retrospective analysis:

- **Per-string shading profiles**: plot `shading_factor` vs `hour` for each string over time
- **Seasonal drift**: compare factors month-over-month
- **Model quality**: compare forecast (with shading) against actual — residual error shows non-shading model issues
- **Sunny hour detection quality**: check `ghi_ratio` distribution to validate the 0.85 threshold

### 2.13.11 Calibration History

| Date | Version | Change | Reason |
|------|---------|--------|--------|
| 2025-02-09 | v1.3.3 | Initial shading factors from 1 sunny winter day | Baseline |
| 2026-03-20 | v1.3.4 | Panel params calibrated (E:445W, W:490W, S:425W, eff 0.98) + new shading factors | Forecast under-predicted sunny day peaks by ~35%; per-string analysis showed model error, not weather error |

**Data boundary:** Forecast data in InfluxDB before 2026-03-20 was computed with the old parameters (E:455W, W:455W, S:400W, eff 0.95/0.96, Feb 2025 shading). After 2026-03-20, new parameters apply. Accuracy metrics show a step change at this boundary.

**Retrofitted data:** An approximate retrofit of the Mar 13–20 forecast is stored as `pv_forecast_retrofitted` (model=`hybrid_v1.3.4`) in the `pv_forecast` bucket. This was computed by applying a per-point correction factor (new model / old model at the stored P50 GHI). The retrofit is approximate because:
- Only the P50 GHI is stored, not per-ensemble-member GHI
- The old shading factors were baked into the P50 before percentile calculation
- Correction accuracy: ~28% error on sunny days (vs ~40% old, ~12% expected from full recalculation)

The retrofit is useful for directional comparison but not a substitute for the real new forecast starting 2026-03-21.

---

# Chapter 3: LoadForecast Add-on

## 3.1 Overview

LoadForecast generates statistical household load consumption forecasts using historical consumption patterns. It analyzes 90 days of historical data to build time-of-day profiles and produces P10/P50/P90 percentile forecasts.

| Property | Value |
|----------|-------|
| Name | LoadForecast |
| Slug | `loadforecast` |
| Architectures | aarch64, amd64, armv7 |
| Timeout | 120 seconds |
| Schedule | Hourly (cron: `15 * * * *`) |

## 3.2 Features

- **Statistical Profiling**: Time-of-day consumption profiles (96 daily slots)
- **Historical Analysis**: Uses 90 days of consumption data
- **Probabilistic Output**: P10/P50/P90 percentiles for uncertainty bands
- **15-Minute Resolution**: Aligned with EnergyManager optimization timestep
- **120-Hour (5-Day) Horizon**: Matches PV forecast range for consistent SOC simulation

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

For each future timestamp in the 120-hour (5-day) horizon:
1. Calculate the slot number from the timestamp
2. Look up P10/P50/P90 values from the profile
3. Convert power (W) to per-period energy (Wh): `power × 0.25h`

## 3.5 Configuration

### Secrets (Configuration UI)

Enter in **Settings → Add-ons → LoadForecast → Configuration**:
- `influxdb_token` (required)

### Non-Secrets (`/config/loadforecast.yaml`)

```yaml
# NOTE: Token is configured in the Configuration tab, not here!
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"
  source_bucket: "HomeAssistant"    # Where to read historical data
  target_bucket: "load_forecast"     # Where to write forecasts

load_sensor:
  entity_id: "load_power"            # HA entity to use for load

forecast:
  history_days: 90                   # Days of history to analyze
  horizon_hours: 120                 # Forecast horizon (matches PV)

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

### Fields

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | Load power (low, 90% chance to exceed) |
| `power_w_p50` | W | Load power (median/typical) |
| `power_w_p90` | W | Load power (high, 10% chance to exceed) |
| `run_time` | ISO string | When forecast was calculated |

**Note:** Values represent instantaneous power (W). To calculate energy per period: `energy_wh = power_w × 0.25` (for 15-min intervals).

## 3.7 Data Source

The add-on queries historical consumption data from the `HomeAssistant` InfluxDB bucket:

```flux
from(bucket: "HomeAssistant")
  |> range(start: -90d)
  |> filter(fn: (r) => r.entity_id == "load_power")
  |> filter(fn: (r) => r._field == "value")
  |> aggregateWindow(every: 15m, fn: mean)
```

**Important:** `sensor.house_load_power` is the Shelly 3EM 3-phase sum (direct measurement, template sensor `load_total_power`). The Huawei Solar integration also calculates a load value (`inverter_active_power - power_meter_active_power + battery_charge_discharge_power`) but the Shelly measurement is preferred for accuracy.

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
  |> range(start: now(), stop: 120h)
  |> filter(fn: (r) => r._measurement == "load_forecast")
  |> filter(fn: (r) => r._field == "power_w_p10" or
                       r._field == "power_w_p50" or
                       r._field == "power_w_p90")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

**Forecast vs Actual:**
```flux
forecast = from(bucket: "load_forecast")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._field == "power_w_p50")

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

# Chapter 4: EnergyManager Add-on

## 4.1 Prerequisites

Everything the decision logic (Sections 4.2–4.5) reads from. All consumers share these inputs; consumer-specific transforms happen inside each consumer's section.

### 4.1.1 Forecast inputs (InfluxDB)

| Source bucket | Measurement | Fields used | Unit |
|---|---|---|---|
| `pv_forecast` | `pv_forecast` (inverter=`total`, model=`hybrid`) | `power_w_p10/p50/p90` | W per 15 min |
| `load_forecast` | `load_forecast` | `energy_wh_p10/p50/p90` | Wh per 15 min |

**Why the 120 h (5-day) horizon:** Both forecasts cover 120 hours. This ensures the SOC simulation can look ahead to the next weekday's expensive hours even from a Friday evening (worst case: Fri 21:00 → Mon 21:00 = 72 hours). The extended horizon also enables 5-day Grafana visualisation of the energy balance and SOC trajectory, and gives the EV 48-h safety rule (Section 4.3) enough headroom to see two full day/night cycles ahead.

### 4.1.2 State inputs (Home Assistant)

| Source entity | Purpose | Consumer |
|---|---|---|
| `sensor.battery_state_of_capacity` | Current home-battery SOC (0–100 %) — read live every cycle | 4.2, 4.3, 4.4 |
| `sensor.smart_battery` + `sensor.smart_battery_last_known` | Current EV (Smart) SOC, with last-known fallback | 4.3 |
| `sensor.wallbox_power`, `sensor.wallbox_status`, `binary_sensor.wallbox_connected`, `binary_sensor.car_ready` | Wallbox telemetry via OCPP server | 4.3 |
| `sensor.grid_power` (M-Bus, <30 s old) / DTSU fallback | Grid power | 4.2, 4.3 |
| `sensor.surplus_power` | **PV − house_load**, rolling 30 s average — the single input signal that drives EV Rule 1/2 | 4.3 |

Starting SOC is read live every simulation cycle, not cached — the forecast trajectory shifts up/down with real SOC.

### 4.1.3 Tariff schedule

| Period | Weekdays | Weekends / holidays |
|---|---|---|
| **Cheap** | 21:00 → 06:00 | All day |
| **Expensive** | 06:00 → 21:00 | — |

`BatteryOptimizer.get_tariff_periods(now)` returns `(cheap_start, cheap_end, target, is_cheap_now)`. `target` is used only by the home-battery discharge rule; EV and washer rules do not consult it. Holidays come from a calendar integration (future: HA calendar entity).

### 4.1.4 Battery configuration

**YAML `battery`:**

| Key | Default | Used by |
|---|---|---|
| `capacity_kwh` | 10.0 | Simulator (Wh conversions), EV safety (load → SOC % conversion) |
| `reserve_percent` | 0 | Home-battery discharge floor during expensive hours (Section 4.2.2) |
| `charge_efficiency` | 0.95 | Simulator (charge branch) |
| `discharge_efficiency` | 0.95 | Simulator (discharge branch) |
| `soc_entity` | `sensor.battery_state_of_capacity` | Current SOC readback |
| `discharge_control_entity` | `number.battery_maximum_discharging_power` | Control output |

**YAML `ev_charging` — safety-rule floor (independent from `battery.reserve_percent`):**

| Key | Default | Used by |
|---|---|---|
| `reserve_percent` | 20 | EV safety rule floor (Section 4.3.6) — 48-h min-SOC threshold below which EV charging stops |

### 4.1.5 Time and unit conventions

- **Internal time**: UTC everywhere. Tariff comparisons convert to `Europe/Zurich` locally; display strings also use Swiss time.
- **Time slot**: 15 minutes. All forecasts, simulations, and the optimization cycle align to `:00/:15/:30/:45`.
- **Energy**: Wh. **Power**: W. **SOC**: % (0–100), converted from Wh via `capacity_wh`.
- **Cycle cadence**: battery optimization every 15 min; EV charging control every 10 s.

---

## 4.2 Home Battery

The home battery is the central buffer of the system. It runs **first** in the decision DAG: its SOC forecast is the input that EV safety (4.3) and washer signal (4.4) consume. Two concerns live here: producing the forecast (simulation) and deciding whether to allow discharge (discharge rule).

### 4.2.1 SOC Simulation

The SOC simulation predicts battery state over the forecast horizon. This is the base curve for all energy management decisions. Runs every 15 min via `BatteryOptimizer.simulate_soc()`, producing two trajectories written to InfluxDB with tag `scenario`:

| Scenario | Description | Used by |
|---|---|---|
| `with_strategy` | Discharge blocked during cheap hours per the 4.2.2 rule — the trajectory that will actually occur | EV safety rule (4.3), washer (4.4) |
| `without_strategy` | Raw simulation with free discharge — the "why we block" reference | Grafana only |

`simulate_soc(soc_percent, forecast, block_from, block_until)` supports optional discharge-block windows; `calculate_decision()` uses this to generate the `with_strategy` curve.

#### Basic Loop (net = PV − Load → battery flow)

```
FOR each 15-minute timestep from NOW to target (up to 120h):

  1. Get forecast values
     pv_wh = pv_forecast[t]       (Wh produced in 15 min)
     load_wh = load_forecast[t]   (Wh consumed in 15 min)

  2. Calculate net energy
     net_wh = pv_wh - load_wh

  3. Determine battery flow
     IF net_wh > 0:  battery_flow = +net_wh  (charge)
     IF net_wh < 0:  battery_flow = net_wh   (discharge)

  4. Memorize: time, pv_wh, load_wh, net_wh, battery_flow
```

#### Efficiency (battery flow → SOC change)

Efficiency loss is applied when energy flows through the battery:

```
Battery parameters:
  capacity = 10000 Wh
  charge_efficiency = 0.95
  discharge_efficiency = 0.95

IF battery_flow > 0:  (charging)
   energy_stored = battery_flow × charge_efficiency
   soc_wh = soc_wh + energy_stored

IF battery_flow < 0:  (discharging)
   energy_withdrawn = |battery_flow| ÷ discharge_efficiency
   soc_wh = soc_wh - energy_withdrawn

Convert back to percent:
   soc_percent = soc_wh / capacity × 100
```

**Example:**

- Charge 1000 Wh → 950 Wh stored (50 Wh loss)
- Discharge 1000 Wh needed → withdraw 1053 Wh (53 Wh loss)

#### Output: SOC Forecast Curve (store into InfluxDB)

The simulation writes only the SOC trajectory to InfluxDB (PV/Load already in input buckets):

**Measurement:** `soc_forecast`

| Field | Unit | Description |
|-------|------|-------------|
| `soc_percent` | % | Forecasted SOC at each 15-min timestep |

```flux
from(bucket: "energy_manager")
  |> range(start: now(), stop: 120h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")
```

### 4.2.2 Discharge Decision

#### Problem

The battery must maintain a minimum State of Charge (min_soc, default 0%) during expensive tariff hours (06:00-21:00). A higher reserve (e.g. 10%) can be configured via `reserve_percent` to provide a buffer for forecast errors and unexpected consumption spikes, but the default is 0% to allow maximum discharge flexibility.

During cheap tariff (night), SOC can drop to any level since grid electricity is inexpensive.

#### Algorithm

The discharge decision is driven by **two independent block flags** combined with OR logic:

| Flag | Set by | Meaning |
|------|--------|---------|
| `_discharge_blocked_by_protection` | `run_optimization()` (every 15 min) | SOC forecast too low to safely discharge |
| `_discharge_blocked_by_ev` | `control_ev_charging_mode()` (every 10 s) | EV actively charging in immediate/cheap mode |

**Combined decision:**
```
discharge_allowed = NOT (blocked_by_protection OR blocked_by_ev)
```

Each mechanism only touches its own flag. `control_battery()` is called with the combined result whenever either flag changes.

**Truth table:**

| Protection | EV Charging | Result |
|-----------|-------------|--------|
| off | off | discharge allowed |
| **on** | off | discharge blocked |
| off | **on** | discharge blocked |
| **on** | **on** | discharge blocked |

#### Protection flag — set every 15 minutes by `run_optimization()`

The algorithm handles 4 time-of-day cases. All times are local (Europe/Zurich).

| Case | Time window | Target | What is checked |
|------|-------------|--------|-----------------|
| 1. Weekday daytime | 06:00-21:00 | — | Nothing — always allow discharge |
| 2. Weekday evening | 21:00-23:59 | Next day 21:00 | Expensive hours only (next day 06:15-21:00) |
| 3. Weekday night | 00:00-06:00 | Today 21:00 | Expensive hours only (today 06:15-21:00) |
| 4. Weekend/holiday | Fri 21:00 → Mon 06:00 | Mon 21:00 | Expensive hours only (Mon 06:15-21:00) |

The same logic applies to cases 2, 3, and 4 — only the target time differs.
On weekends, `filter_expensive_periods()` naturally returns only Monday's
expensive hours since Saturday and Sunday are all-day cheap.

```
1. CHECK CURRENT TARIFF
   IF expensive tariff (06:00-21:00 on a weekday):
      → blocked_by_protection = False
      → Skip to step 4

2. SIMULATE SOC (cheap tariff — cases 2, 3, 4)
   - Determine target: next weekday 21:00 (see table above)
   - Simulate from NOW until target
   - Assume free discharge (no blocking)
   - Use current SOC as starting point
   - Apply PV and load forecasts

   IMPORTANT: The simulation is limited to the target time. A separate
   5-day simulation is used for Grafana visualization only — it must NOT
   feed into the discharge decision (the `discharge_allowed` boolean).
   The 5-day sim may be used to estimate the deferred-block start time
   for the Grafana chart, but this is purely visual.

   CHECK: Does SOC stay >= min_soc during expensive hours?
   - Extract minimum SOC from weekday 06:15-21:00 periods in simulation
   - Ignore SOC values during cheap hours (21:00-06:00)
   - Ignore weekend/holiday days entirely (all-day cheap → no expensive hours)
   - SOC dips during cheap hours (including entire weekends) are acceptable

   IF previously_blocked:
      threshold = min_soc + 2%   (hysteresis — require clear margin to re-allow)
   ELSE:
      threshold = min_soc

   IF min_soc_in_expensive_hours >= threshold:
      → blocked_by_protection = False
      → Skip to step 4

3. CALCULATE DISCHARGE FLOOR (cheap tariff, SOC would drop below min)
   Instead of blocking immediately, calculate a SOC floor — the level at
   which to stop discharging — so the battery can serve the house during
   the evening and only hold once it reaches the minimum needed for
   the next morning's expensive hours.

   a) Run a REFERENCE simulation from cheap_end (06:00) starting at 100%
      using the morning/daytime forecast up to the target. This measures
      the true "morning drop" — how much SOC falls from cheap_end to the
      expensive-hours minimum before PV production recovers it.

   b) soc_floor = min(min_soc + morning_drop, 100%)

      Example: min_soc=10%, morning drop=21% → floor=31%
      The battery must hold 31% at 06:00 so it doesn't go below 10%
      at the morning minimum (~08:00).

   c) Compare current SOC against floor:
      IF current_soc > soc_floor:
         → blocked_by_protection = False   (above floor, allow discharge)
      ELSE:
         → blocked_by_protection = True    (at/below floor, hold for morning)

   The 15-minute re-evaluation loop naturally catches the SOC-to-floor
   transition: discharge is allowed until SOC drops to the floor, then
   blocked for the remainder of the cheap period.

4. APPLY combined decision (see above)
```

#### EV flag — set every 10 seconds by `control_ev_charging_mode()`

```
IF charging_mode in ("immediate", "cheap") AND target_power > 0:
   → blocked_by_ev = True
ELSE:
   → blocked_by_ev = False

APPLY combined decision (see above)
```

**Why block discharge during immediate/cheap EV charging:** When the wallbox draws significant power (e.g. 5 kW), the OCPP server publishes this to the Modbus Proxy, which corrects the DTSU reading so SUN2000 sees high household load and discharges the battery to cover it — wasting stored energy that should be preserved.

#### Key Design Decisions

**Why always allow during expensive hours:**
- During expensive tariff (06:00-21:00), we're in the period we were protecting
- Battery should discharge to avoid expensive grid import
- No reason to block—this is exactly when we want battery power

**Why use a discharge floor instead of immediate blocking:**
- The old binary approach blocked ALL discharge from the moment the free-discharge simulation showed the SOC dipping below the threshold during future expensive hours — even hours before it was necessary
- Example: at 71% SOC on Sunday evening, the battery was blocked for 12 hours to prevent a brief 6% dip at 08:00 Monday, forcing unnecessary grid import all evening
- The floor approach allows the battery to serve the house until it reaches the minimum SOC needed for morning protection, then holds
- This is both economically better (less cheap-rate grid import) and user-expected behavior (battery powers the house when it has charge)

**Why re-check every 15 minutes during cheap hours:**
- Forecasts may have errors; actual conditions may differ
- If load was lower than forecast, SOC will be higher than predicted
- If PV was higher than forecast, battery may have extra charge
- Re-simulation with current SOC naturally adapts to reality
- The floor comparison (`current_soc > soc_floor?`) naturally transitions from "allow" to "block" as the battery discharges through the evening

**Why only check expensive hours (uniform rule for weekday and weekend):**
- During cheap tariff (21:00-06:00), low SOC is acceptable — grid electricity is inexpensive
- On weekends/holidays, the entire day is cheap — no expensive hours exist
- Weekend SOC dips are irrelevant: only the next weekday's expensive hours matter
- The same `filter_expensive_periods()` call works for all cases: on weekday nights it returns tomorrow's 06:15-21:00; on weekends it returns Monday's 06:15-21:00
- The battery can freely discharge on weekends; PV recharges it before Monday morning

**Why limit the decision simulation to `tariff.target`:**
- A separate 5-day simulation is used for Grafana chart visualization
- If this 5-day simulation also fed the decision logic, it would check Mon+Tue+Wed+Thu expensive hours — a 10 kWh battery cannot sustain 4 days of weekday consumption, so it would always block on weekends
- The decision must only consider the NEXT target (e.g. Monday 21:00)

**Threshold hysteresis (2% dead-band):**
- When the projected min SOC hovers near the threshold, the simulation window shrinks by 15 min each cycle, causing the projection to wobble ~0.5% — enough to flip the decision every cycle
- Once blocked (`previously_blocked=True`), the threshold rises by 2% — the projection must clearly exceed the margin before re-allowing discharge
- Without this, the discharge limit oscillates between 0W and 5000W every 15 minutes, and the battery drains all night through the "allowed" windows

**Signal hysteresis:**
- Control signal only sent when the combined decision changes (not every cycle)
- Reduces unnecessary Modbus communication with inverter
- Prevents rapid on/off cycling
- Each flag setter only calls `_update_discharge_control()` when its own flag actually changes

#### Self-Correcting Behavior

The rolling 15-minute check makes the system self-correcting:

| Scenario | Effect |
|----------|--------|
| Load lower than forecast | SOC stays higher → floor reached later or never |
| PV higher than forecast | More energy available → morning drop smaller → lower floor |
| Unexpected high load | SOC drops faster → reaches floor sooner → blocks earlier |
| Battery started fuller | More headroom above floor → serves house longer |
| SOC reaches floor | Blocks discharge, battery holds for morning expensive hours |

This eliminates the complexity of pre-calculating switch-on times while naturally adapting to real-world conditions.

#### Output: number.battery_maximum_discharging_power

Controls the battery discharge power in Home Assistant:

| Value | Meaning |
|-------|---------|
| `5000` | Discharge allowed (max power) |
| `0` | Discharge blocked |

```yaml
service: number.set_value
target:
  entity_id: number.battery_maximum_discharging_power
data:
  value: "{{ 5000 if discharge_allowed else 0 }}"
```

#### Test Cases

See [Appendix D.1 — Battery Discharge Optimizer Tests](#d1-battery-discharge-optimizer-tests). Test file: `energymanager/tests/test_battery_optimizer.py` (14 tests passing as of v1.5.0).

See [Appendix D.4 — Discharge Blocking Tests](#d4-discharge-blocking-tests). Test file: `energymanager/tests/test_discharge_blocking.py` (17 tests passing as of v1.6.19).

---

## 4.3 EV Battery

EV charging runs **second** in the DAG: the home-battery SOC forecast (Section 4.2.1) is the input its safety rule reads. This section covers everything EV-specific: wallbox architecture, mode selection, state machine, power calculation, the new 48-h safety rule, live car SOC polling, and the multi-day car SOC forecast.

### 4.3.1 Overview

EV charging optimization maximizes solar self-consumption while ensuring charging goals are met.

> **Wallbox infrastructure:** The OCPP server, phase switching, power-to-current conversion, and wallbox communication are documented in [ocpp-server-fsd.md](../ocpp-server/docs/ocpp-server-fsd.md).

**Key Features:**
- 4 modes: Off, solar, cheap tariff, immediate — user selects via dashboard
- Solar charging is the default mode
- State machine (Section 4.3.5) routes to correct mode
- EV charging power calculation (Section 4.3.6) determines solar charging power
- Two charging rules: Battery Full (grid export capture) and Solar Surplus Charging (48-h safety-gated) — both in Section 4.3.6
- `input_number.ev_min_solar_power` gates both solar paths — minimum power to start charging
- Real-time charging power adjustment every 10 seconds

### 4.3.2 Architecture

> **Wallbox communication, phase switching, MQTT power correction, and the EnergyManager ↔ OCPP Server interface contract are documented in [ocpp-server-fsd.md](../ocpp-server/docs/ocpp-server-fsd.md).**

From the EnergyManager's perspective, the wallbox is controlled through HA entities provided by the OCPP server:

| Direction | Entity | Type | Description |
|-----------|--------|------|-------------|
| **Control** | `number.wallbox_power_limit` | number | Power setpoint (W). `0` = pause, `> 0` = charge at this power. |
| **Feedback** | `sensor.wallbox_power` | sensor (W) | Actual measured charging power from wallbox MeterValues |
| **Feedback** | `sensor.wallbox_status` | sensor | OCPP status: `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`, `Faulted` |
| **Feedback** | `binary_sensor.wallbox_connected` | binary | WebSocket connection to wallbox (`on`/`off`). If `off`, EV control is skipped entirely. |
| **Feedback** | `binary_sensor.car_ready` | binary | Car plugged in and ready to charge (`on`/`off`) |
| **Feedback** | `sensor.wallbox_min_power_w` | sensor (W) | Dynamic minimum power based on current phase configuration |
| **Feedback** | `sensor.wallbox_max_power_w` | sensor (W) | Dynamic maximum power based on current phase configuration |

### 4.3.3 Power Ranges

> **Phase switching hardware and power-to-current conversion are documented in [ocpp-server-fsd.md](../ocpp-server/docs/ocpp-server-fsd.md).**

| Mode | Voltage | Current | Power Range |
|------|---------|---------|-------------|
| **1-phase** | 230V | 6-16A | 1.4 - 3.7 kW |
| **3-phase** | 400V | 6-16A | 4.1 - 11.0 kW |

**Gap:** 3.7 - 4.1 kW is not achievable (hardware limitation). The OCPP server handles phase switching automatically based on the power setpoint.

### 4.3.4 Charging Mode Selection

The user selects one of three charging modes via the kitchen dashboard (Amazon Fire tablet). The mode is persistent — it stays selected until the user changes it. The `input_select` entity is preserved by HA across add-on restarts; the EnergyManager reads (not overwrites) the current mode on startup.

| Mode | `input_select` value | Dashboard Label | Description |
|------|---------------------|----------------|-------------|
| **Auto PV Excess** | `auto_pv_excess` | *(default)* | Charge from stable PV excess only |
| **Immediate** | `immediate` | Immediate Charge | Charge now at fixed power |
| **Cheap Tariff** | `cheap_tariff` | Cheap Charge | Charge only during the cheap tariff at fixed power |

**Control entity:** The dashboard provides two buttons ("Cheap Charge" and "Charge Now") that toggle between the selected mode. If both are unselected, `auto_pv_excess` is active. Charge now starts charging immediately.

### 4.3.5 EV Charging State Machine

The state machine routes the wallbox to the correct operating mode based on user selection. It does **not** compute charging power — that is done by the EV Charging Power Calculation (Section 4.3.6).

> **Wallbox infrastructure:** Faults, disconnects, phase switching, and amp conversion are handled by the OCPP server (see [ocpp-server-fsd.md](../ocpp-server/docs/ocpp-server-fsd.md)). The state machine only makes charging decisions.

#### States

| # | State | Description | Power output |
|---|-------|-------------|--------------|
| 1 | **IDLE** | No EV charging. SUN2000 has full control of the battery. | `0` |
| 2 | **SOLAR** | Solar charging. Power from Section 4.3.6. | `ev_charging_power_w` |
| 3 | **CHEAP** | Cheap-tariff charging at user-set power. Battery discharge blocked. | `manual_power_w` when cheap, `0` when expensive |
| 4 | **IMMEDIATE** | Immediate charging at user-set power. Battery discharge blocked. | `manual_power_w` |

Initial state: **IDLE**

#### Transitions

The machine stays in its current state unless one of the listed conditions triggers a change. Conditions are evaluated in listed order — first match fires.

**IDLE** — *Stays in IDLE unless:*

| # | Condition | → New State |
|---|-----------|-------------|
| N1 | `charging_mode == "immediate" AND wallbox_available` | IMMEDIATE |
| N2 | `charging_mode == "cheap" AND wallbox_available` | CHEAP |
| N3 | `charging_mode == "solar" AND wallbox_available AND ev_charging_power_w > 0` | SOLAR |

**SOLAR** — *Stays in SOLAR unless:*

| # | Condition | → New State |
|---|-----------|-------------|
| S0 | `NOT wallbox_available` | IDLE |
| S1 | `wallbox_idle` | IDLE |
| S2 | `charging_mode == "immediate"` | IMMEDIATE |
| S3 | `charging_mode == "cheap"` | CHEAP |

**CHEAP** — *Stays in CHEAP unless:*

| # | Condition | → New State |
|---|-----------|-------------|
| C0 | `NOT wallbox_available` | IDLE |
| C1 | `wallbox_idle` | IDLE |
| C2 | `charging_mode != "cheap"` | IDLE |

Power toggles internally: `manual_power_w` when `is_cheap_tariff`, `0` when expensive. No state change on tariff toggle.

**IMMEDIATE** — *Stays in IMMEDIATE unless:*

| # | Condition | → New State |
|---|-----------|-------------|
| M0 | `NOT wallbox_available` | IDLE |
| M1 | `wallbox_idle` | IDLE |
| M2 | `charging_mode != "immediate"` | IDLE |

#### Shared Concepts

**`wallbox_available`** — Single boolean: wallbox entity exists AND WebSocket connected AND not faulted AND car plugged in (status ≠ "Available").

**`wallbox_idle`** — `True` when power = 0 and status ∈ {`Finishing`, `SuspendedEV`} for ≥ `auto_reset_timeout_min` (default 5 min). Signals the car has finished charging.

#### Inputs and Outputs

**Inputs:**

| Input | Source | Used by |
|-------|--------|---------|
| `wallbox_available` | `binary_sensor.car_ready` | IDLE → entry guard |
| `wallbox_idle` | Computed in `run.py` | All states → IDLE exit |
| `charging_mode` | `input_select.ev_charging_mode` | State routing |
| `is_cheap_tariff` | Tariff schedule (Section 4.1.3) | CHEAP power toggle |
| `ev_charging_power_w` | EV Charging Power Calculation (Section 4.3.6) | SOLAR entry + power |
| `manual_power_w` | `input_number.ev_manual_power` | CHEAP/IMMEDIATE power |

**Output:**

| Output | HA Entity | Description |
|--------|-----------|-------------|
| `target_power_w` | `number.wallbox_power_limit` | Wallbox power setpoint (W). 0 = pause. |
| `state` | `sensor.ev_charge_status` | Current state for dashboard/logging |
| `reason` | Attribute on `sensor.ev_target_power` | Human-readable reason for current decision |

### 4.3.6 EV Charging Power Calculation

When the state machine is in SOLAR mode, this calculation determines the wallbox power setpoint. It runs every 10 seconds and selects from two power sources.

#### Goals

1. **Maximize solar self-consumption** — use PV surplus for EV instead of exporting to grid
2. **Capture wasted energy** — Enphase AC-coupled production that the DC-coupled battery cannot absorb
3. **Protect home battery** — keep the forecast home-battery SOC above `reserve_percent` at all times across the next 48 h
4. **Single threshold** — `ev_min_solar_power` gates all solar charging in one place

#### Input Parameters

| Parameter | HA Entity / Source | Default | Description |
|-----------|-------------------|---------|-------------|
| `ev_min_solar_power` | `input_number.ev_min_solar_power` | 1380 W | Minimum power to start solar EV charging. Gate for both sources. |
| `surplus_power` | `sensor.surplus_power` | — | Live surplus (solar − house load, rolling 30s average). Input for both Rule 1 and Rule 2. |

**Output:** `ev_charging_power_w` — the wallbox power setpoint in watts (0 = don't charge).

#### Shared Mechanism: Amp-Step Conversion

The wallbox charges at discrete amp levels (6A–12A), each delivering a fixed power. `snap_to_power_step(available_power_w)` takes a continuous power value and returns the highest discrete amp step that fits. It is used by both rules — the function does not care where the input comes from.

Both rules are gated by `ev_min_solar_power` (threshold) — below this level, there isn't enough power to justify starting the wallbox.

#### Selection Rules

The deciding factor is **battery state**: is the battery full, or is it still charging? Both rules use the same input signal: `surplus_power` (PV − house load, rolling 30s average). This avoids the feedback loop where grid export drops when the wallbox is already consuming solar.

```
                      INPUTS
  surplus          = rolling 30s avg of (PV - load)
  threshold        = ev_min_solar_power (e.g. 3400W)
  battery_soc      = current battery %
  pv_forecast      = forecasted PV production (Section 4.1.1)
  load_forecast    = forecasted household load (Section 4.1.1)
  min_soc_floor    = ev_charging.reserve_percent (default 20%)
  horizon          = 48 hours

                      OUTPUTS
  ev_charging_power_w  = wallbox power setpoint (W), 0 = don't charge
  ev_charging_rule     = "battery_full" | "solar_surplus" | "none"

                        │
                        ▼
              ┌────────────────────┐
              │  Battery full?     │
              │  (SOC ≥ 100%)      │
              │  AND surplus ≥     │
              │  threshold?        │
              └──┬──────────────┬──┘
              YES│              │NO
                 ▼              ▼
  ┌──────────────────────┐  ┌──────────────────────────┐
  │ Rule 1: BATTERY FULL │  │ surplus ≥ threshold?     │
  │                      │  └────┬────────────────┬────┘
  │ snap_to_power_step(  │    YES│                │NO
  │   surplus)           │       ▼                ▼
  │                      │  ┌────────────────┐  power = 0
  │ No battery check.    │  │ Rule 2: SOLAR  │
  └──────────┬───────────┘  │ SURPLUS        │
             │              │                │
             │              │ SNAP-UP then   │
             │              │ STEP-DOWN with │
             │              │ 48-h safety    │
             │              │ check per step │
             │              └──┬─────────────┘
             │                 │
             │                 ▼
             │         pass → use that step
             │         none → 0 W
             │                 │
             └─────────────────┘
                         │
                         ▼
                ev_charging_power_w
                → state machine
                → wallbox
```

##### Rule 1: Battery Full

The battery is full — energy has nowhere to go and would be curtailed.

- **Input**: surplus power (PV − house load)
- **Condition**: `battery_soc >= 100 AND surplus >= threshold AND pv_power > 0`
- **Power**: `snap_to_power_step(surplus)` → charging power
- **Battery check**: none — energy would be wasted

##### `will_battery_hit_full()` — dashboard diagnostic

Lives on `EVBatteryOptimizer`. Returns whether the **peak home-battery SOC** reaches 100 % between now and end of today (midnight local), plus the **time** it first reaches 100 %.

- Queries `max(soc_percent)` from now until end of today against the `with_strategy` forecast
- If peak ≥ 99 % → battery will be full; queries `first()` where `soc_percent >= 99` to find the time
- Returns `(hits_full, peak_soc, full_time_local "HH:MM" or None, end_of_today)`

**Used by:** `full_time_local` is published as the `battery_full_time` attribute on `sensor.ev_target_power` for dashboard display. The boolean is informational only — it does **not** gate EV charging; the 48-h safety rule below is the sole gate.

##### Rule 2: Solar Surplus Charging

The battery is still charging — surplus exists (PV > house load) but the battery needs the energy too. A single safety rule decides whether the EV may draw from that surplus.

- **Input**: surplus power (PV − house load, rolling 30s average)
- **Safety rule** (Layer 2, `EVBatteryOptimizer.check_ev_safe`):

> Allow EV charging only if the forecast home-battery SOC stays
> **≥ `ev_charging.reserve_percent` (default 20 %) at every point across
> the next 48 hours**, with the candidate EV load (power × 0.25h) subtracted
> from the forecast as worst-case.

- **Horizon**: 48 hours — covers two full daily cycles (tonight's drain → tomorrow morning's low → tomorrow's recharge → second morning's low). Forecast noise beyond ~48 h would dominate and produce false blocks.
- **Self-correcting**: the rule re-runs every 15 minutes. If the forecast drops below the floor, EV charging stops immediately and the battery (now EV-free) rides the remaining forecast back up. No separate "will reach X%" target is required.

**Step iteration (snap-up then step-down):**

| Outcome | Condition | Action |
|---------|-----------|--------|
| **Allow** | A candidate step passes `check_ev_safe` | Use the first passing step |
| **Block** | No step passes | 0W — battery forecast would drop below `min_soc_percent` |

The iteration tries the step **above** the snapped surplus first (snap-up — the battery bridges the small gap for maximum solar utilisation), then steps down through lower amp levels until a step passes or the threshold is reached.

**Independence from nightly battery protection.** The nightly battery-discharge block (Section 4.2.2) uses `battery.reserve_percent`; this EV safety gate uses `ev_charging.reserve_percent`. They are **separate config values**: the discharge gate defends the last sliver of battery during expensive hours; the EV gate keeps a larger buffer for house consumption before diverting surplus to the car (default **20 %** vs typically 5–10 % for the discharge floor). The two rules evolve independently.

#### Snap-to-Power Step

The wallbox only charges at integer amp levels. The energymanager picks from a discrete set of **M-Bus calibrated power steps** — the actual power delivered at each amp level, measured via M-Bus ground truth (2026-03-04 calibration sweep):

| Amps | Delivered power (M-Bus W) |
|-----:|--------------------------:|
| 6 | 3962 |
| 7 | 4354 |
| 8 | 5117 |
| 9 | 5727 |
| 10 | 6288 |
| 11 | 7034 |
| 12 | 7624 |

These values differ from the nominal `amps × 230V × 3` because real grid voltage is ~220V, not 230V. Using M-Bus values ensures the energymanager's energy accounting (battery protection, surplus calculation) matches what the wallbox actually delivers.

**Inputs:** `surplus_power` (live measurement from `sensor.surplus_power`).

**Calculation:** `snap_to_power_step(surplus_power)` returns the highest M-Bus step ≤ surplus. If surplus is below all steps but above `ev_min_solar_power`, the minimum step (3962W) is returned — the battery covers the difference. In the step-down loop (Rule 2, battery won't be full), the loop first tries the **next step above** the snap-down result (snap-up) — if battery protection passes at the higher load, the battery covers the small gap, maximising solar utilisation.

The OCPP server converts M-Bus watts to integer amps using a calibrated divisor: `round(power_w / 637)`. The divisor 637 (safe range [612, 662]) ensures each M-Bus value maps to the correct amp level.

**Safety gating (snap-up then step-down loop):** The loop first tries the **next step above** the snap-down candidate (snap-up — battery bridges the small gap between surplus and the higher amp step). If that step fails `check_ev_safe` (48-h min-SOC rule), it steps down through lower amp levels until either: (a) a step passes the safety check, or (b) no more steps above `ev_min_solar_power` remain. Rule 1 (battery already at 100%) uses the snapped level directly — no safety check needed because SOC can only decrease from 100%.

Because `control_ev_charging()` runs every 10 seconds with live surplus, the system is self-correcting:

| Condition change | Effect |
|------------------|--------|
| PV drops (clouds) | Surplus drops → next cycle picks lower amp level |
| PV rises | Surplus rises → next cycle picks higher amp level |
| Load increases | Surplus drops → next cycle adapts down |
| Load decreases | Surplus rises → next cycle may increase power |

**Rate limiting:** The wallbox power limit (`number.wallbox_power_limit`) is only sent when it differs from the last-sent value **and** at least 30 seconds have passed since the last change. This prevents oscillation at power-step boundaries (e.g. surplus hovering near 3962/4354W causing the wallbox to flip between 6A and 7A every 10 seconds). 0W (pause) bypasses the rate limit for safety. The dashboard sensor `sensor.ev_target_power` still updates every 10 seconds to show the desired power.

#### Scenarios

| Scenario | Rule | What happens |
|----------|------|-------------|
| **Battery full, sunny** | 1 | Surplus → `snap_to_power_step(surplus)` → wallbox charges, no battery check |
| **Good morning** | 2 | Surplus rises above threshold → snap-up tries next step above surplus (battery covers gap); safety gate checks 48-h min SOC |
| **Afternoon decline** | 2 | Surplus drops each cycle → lower power step. Below threshold → 0W |
| **Bad/cloudy day** | 2 | 48-h safety rule blocks EV early if forecast SOC would dip below `reserve_percent` anywhere in the horizon |
| **Peak solar** | 1 or 2 | High surplus → higher power step. Battery charges the excess between steps |
| **Low surplus (e.g. 1000W)** | — | Below `ev_min_solar_power` → no charging. Not enough to justify starting the wallbox |

### 4.3.7 Smart Car SOC Polling

The EV battery SOC is read from the Hello Smart API and published as `sensor.smart_battery`. Polling frequency adapts to wallbox state to balance freshness against API rate limits.

#### Polling Strategy

| Trigger | Frequency | Condition |
|---------|-----------|-----------|
| Mode changed | Once, immediately | `input_select.ev_charging_mode` value differs from previous cycle (e.g. solar → immediate). Ensures fresh SOC before any charging decision. |
| Car connected | Once, immediately | Wallbox status transitions to `Preparing` from a disconnected state (`Available`, `Unknown`, or first poll) |
| Active charging | Every 60 seconds | Wallbox status = `Charging` |
| Idle / baseline | Every 60 minutes | Scheduled job (always running) |

**Priority:** Mode change > car connected > charging interval (first matching trigger wins per cycle).

**Connected states** (no re-poll on transitions between these): `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`.

#### Client Caching

The `HelloSmartClient` session is cached across polls to avoid full re-authentication on every call.

| Scenario | HTTP requests per poll |
|----------|----------------------|
| Cached client (normal) | 2 (session refresh + vehicle status) |
| After error (re-auth) | 6 (full authentication flow) |
| Hourly baseline | 6 (fresh client each hour) |

On any API exception, the cached client is cleared (`self._smart_car_client = None`). The next poll creates a fresh client with full re-authentication.

#### Implementation

- **Adaptive polling** runs inside `control_ev_charging()` (10-second loop), checking wallbox status transitions
- **Hourly baseline** is a separate APScheduler job (`id="smart_car_soc"`)
- **Monotonic timestamps** (`time.monotonic()`) track poll intervals to avoid clock-skew issues
- **Wallbox status tracking** via `_last_wallbox_status` detects connection events (transition to `Preparing`)
- **Mode tracking** via `_last_ev_charging_mode` detects charging mode changes (skips first cycle to avoid false trigger on startup)

#### Last-Known Value Fallback

The Hello Smart integration drops to `unavailable` when the car goes to sleep (minutes after last charge/drive). Downstream consumers (Car SOC Forecast, dashboard cards) still need a number. Two layers of fallback keep them working:

**Python side (`_read_car_soc_with_fallback()` in `run.py`):**
1. Read live state of `smart_car.soc_entity`; return its numeric value if present.
2. If unavailable/unknown, call `_query_last_value()` to read the last numeric value from the `HomeAssistant` InfluxDB bucket over the past 7 days.
3. If nothing found, return `None` and skip the car SOC forecast for this cycle (logged at WARN).

**HA dashboard side (trigger-based template sensor):**

A `sensor.smart_battery_last_known` sensor in `/config/templates.yaml` caches the last numeric reading of `sensor.smart_battery`:

```yaml
- trigger:
    - platform: state
      entity_id: sensor.smart_battery
      not_to: ["unavailable", "unknown", "none", ""]
  sensor:
    - name: "Smart Battery Last Known"
      unique_id: smart_battery_last_known
      unit_of_measurement: "%"
      device_class: battery
      state_class: measurement
      state: "{{ trigger.to_state.state }}"
```

Because the trigger filters invalid transitions, the sensor retains its last value across `unavailable` periods. The Lausen Amazon Fire dashboard (`lovelace-amazonfire`) references `sensor.smart_battery_last_known` in all cards and button-card JS logic instead of `sensor.smart_battery`.

### 4.3.8 Car SOC Forecast

A 15-min time-series forecast of the EV battery SOC over the next 5 days, written to InfluxDB and displayed in Grafana.

**Model:** The house battery is treated as a buffer. For each 15-min interval:

1. `surplus_kwh = (pv_energy_wh − load_energy_wh) / 1000`
2. If surplus ≥ 0 (daytime excess):
   - fill the house battery up to its usable capacity
   - any overflow × `smart_car.charge_efficiency` is added to the car
3. If surplus < 0 (night deficit):
   - drain the house battery (clamped at 0 %)
   - the car is unaffected

**Starting state:**
- `car_soc_percent` at t₀ = current `smart_car.soc_entity` value (with the fallback above)
- `house_kwh` at t₀ = current `battery.soc_entity` × `battery.capacity_kwh` / 100

**Why the house buffer matters:** on days where the house battery does not reach 100 %, the car never charges — because all surplus is absorbed by the house. On days where it does reach 100 %, the only "cost" is the initial headroom top-up and each night's refill the next morning. A sanity check: if the house reaches 100 % each day and ends where it started, `car_kwh ≈ Σ surplus × efficiency` (the cumulative-energy-balance shortcut).

**Efficiency (0.9 default):** lumps three real losses — AC→DC at the wallbox (~3 %), house-battery round-trip for the fraction of surplus that cycles through it (~5 %), and standby/phantom loads during the day (~2 %).

**What the forecast intentionally omits:** the strict `ev_min_solar_power` threshold, amp-step snapping, and the 48-h safety rule from the live EV charging logic (Section 4.3.6). Those rules operate on the 10-second decision loop; the forecast is a best-case multi-day outlook and would become overly pessimistic (≈40 % final SOC in a typical 2-day window) if it copied them.

---

## 4.4 Washer (Appliance Signal)

Independent of EV charging — runs in parallel on the 15-min optimizer cycle. Reads the home-battery simulation (Section 4.2.1) to decide whether a high-power appliance (washing machine) can run without forcing grid import.

### 4.4.1 Problem

High-power appliances (washing machine 2.5 kW) should run when there's sufficient solar surplus.

### 4.4.2 Algorithm

Uses the same SOC simulation as the home-battery discharge logic (Section 4.2.1). The simulation (`sim_no_strategy`) is already computed every 15 minutes — the appliance signal subtracts the appliance energy and checks the resulting minimum SOC.

```
Every 15 minutes:

1. GREEN: PV excess > appliance_power (2500W)
   → excess = current_pv - current_load
   → Run now with pure solar

2. ORANGE: min SOC in simulation − appliance_load_percent > 0%
   → No grid import needed until 21:00 even with appliance
   → Safe to run

3. RED: min SOC in simulation − appliance_load_percent ≤ 0%
   → Running the appliance would require grid import before 21:00
```

### 4.4.3 Output: sensor.appliance_signal

| State | Meaning |
|-------|---------|
| `green` | Pure solar available now (excess > 2500W) |
| `orange` | No grid import needed until 21:00 with appliance load |
| `red` | Running the appliance would require grid import before 21:00 |

### 4.4.4 Sensor Attributes

| Attribute | Description |
|-----------|-------------|
| `reason` | Human-readable explanation of the signal |
| `excess_power_w` | Current PV excess (pv − load) in watts |
| `min_soc_percent` | Minimum projected SOC with appliance load subtracted (%) |

---

## 4.5 InfluxDB Storage

**Bucket:** `energy_manager`

**Measurements:**

| Measurement | Purpose | Tags | Fields |
|-------------|---------|------|--------|
| `soc_forecast` | Rolling SOC trajectory (overwritten every 15 min) | `scenario` | `soc_percent` |
| `soc_forecast_snapshot` | Persistent forecast for accuracy tracking | (none) | `soc_percent` |
| `energy_balance` | Energy flow + Car SOC Forecast per timestep | (none) | `pv_power_w`, `load_power_w`, `cumulative_wh`, `car_soc_percent` |
| `discharge_decision` | Battery control decisions | (none) | `allowed`, `reason`, `min_soc_percent`, `min_soc_time`, `current_soc` |
| `appliance_signal` | Appliance signal output | (none) | `signal`, `reason`, `excess_power_w`, `final_soc_percent` |

### 4.5.1 SOC Forecast Scenarios

The `soc_forecast` measurement uses a `scenario` tag to store two curves:

| Scenario | Description | Color in Grafana |
|----------|-------------|------------------|
| `with_strategy` | What will happen with discharge blocking applied | Green (solid) |
| `without_strategy` | What would happen without any blocking | Orange (dashed) |

### 4.5.2 Forecast Snapshot for Accuracy Tracking

The `soc_forecast_snapshot` measurement provides persistent forecast storage:

- **Written every 15 minutes** with the current "with_strategy" forecast
- **Only overwrites from NOW onwards** — earlier points remain from previous writes
- **Accumulates over time** — creates continuous forecast history
- **Compare with actual SOC** from `HomeData` bucket to evaluate forecast accuracy

Example: At 21:00, forecast is written for 21:00→21:00 next day. At 23:00, only 23:00→21:00 is overwritten, preserving the 21:00→23:00 portion.

**Query examples:**

```flux
# SOC forecast - with strategy (what will happen)
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")
  |> filter(fn: (r) => r.scenario == "with_strategy")

# SOC forecast - without strategy (why we block)
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")
  |> filter(fn: (r) => r.scenario == "without_strategy")

# Forecast snapshot (for accuracy comparison)
from(bucket: "energy_manager")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._measurement == "soc_forecast_snapshot")

# Actual SOC (for comparison with forecast)
from(bucket: "HomeData")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._measurement == "Energy")
  |> filter(fn: (r) => r._field == "BATT_Level")

# Energy balance with cumulative
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "energy_balance")
  |> filter(fn: (r) => r._field == "cumulative_wh")

# Car SOC Forecast (replaces the old Cumulative Energy Balance Grafana panel)
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "energy_balance")
  |> filter(fn: (r) => r._field == "car_soc_percent")
```

## 4.6 Dashboard

### 4.6.1 Wallbox Status Display

The EV card on the kitchen dashboard maps the raw OCPP `sensor.wallbox_status` to user-friendly labels. The OCPP server publishes only raw status strings; the dashboard handles all display logic.

| OCPP Status | Label | Background color |
|-------------|-------|-----------------|
| `Available` | "Not connected" | Default (no car) or orange (SOC < target) |
| `Preparing` | "Connected" | Green |
| `Charging` | "{power} W" | Green (or red if power mismatch) |
| `SuspendedEVSE` | "0 W" | Green |
| `SuspendedEV` | "0 W" | Green |
| `Finishing` | "Finished" | Green |
| `Faulted` | Raw status | Default |
| SOC >= target | "Full" | Default |
| Wallbox disconnected | "Offline" | Default |

**SuspendedEVSE vs SuspendedEV:** EVSE = paused by charger (power limit = 0 A). EV = paused by car (car's BMS stopped drawing current).

**Error indicator:** Background turns red when power limit > 0 but actual power deviates by > 1000 W and SOC < target (wallbox not responding to setpoint), or when power limit = 0 but actual power > 100 W (wallbox not stopping).

### 4.6.2 Kitchen Dashboard (Mushroom Cards)

```yaml
type: horizontal-stack
cards:
  # Appliance Signal
  - type: custom:mushroom-template-card
    primary: Waschen
    icon: mdi:washing-machine
    icon_color: >
      {% set s = states('sensor.appliance_signal') %}
      {{ 'green' if s == 'green' else 'orange' if s == 'orange' else 'red' }}

  # EV Charging
  - type: custom:mushroom-template-card
    entity: input_select.ev_charging_mode
    primary: Auto
    secondary: >
      {% set m = states('input_select.ev_charging_mode') %}
      {% if m != 'off' %}{{ states('sensor.wallbox_power') | int }} W
      {% else %}Aus{% endif %}
    icon: mdi:car-electric
    icon_color: >
      {% set m = states('input_select.ev_charging_mode') %}
      {{ 'green' if m in ['solar','cheap','immediate'] else 'grey' }}

  # Battery
  - type: custom:mushroom-template-card
    primary: Batterie
    secondary: "{{ states('sensor.battery_state_of_capacity') }}%"
    icon: mdi:battery
    icon_color: >
      {{ 'green' if is_state('binary_sensor.battery_discharge_allowed', 'on') else 'orange' }}
```

### 4.6.3 Solar Decision Card (Amazon Fire Dashboard)

Displays EV charging decision with case evaluation and pass/fail status. Uses `sensor.ev_target_power` attributes.

**Card layout — case evaluation with live values:**

```
Surplus: 4200W  Threshold: 1380W  ✅

🟢 Battery will be full → Case 1
   Snap-to-amp: 7A → 4830W

📊 FORECAST → 4830W
```

```
Surplus: 4200W  Threshold: 1380W  ✅

⚪ Battery will NOT be full → Case 2
   Step-down: 8A → 7A → 4830W
   reaches target SOC? 🟢 yes (82%)
   battery will hit min? 🟢 no

📊 FORECAST → 4830W
```

```
Surplus: 800W  Threshold: 1380W  ❌

⏸️ NO CHARGING → 0W
```

**Sensor attributes used by card:**

| Attribute | Purpose |
|-----------|---------|
| `threshold_w` | Min solar power threshold (W) |
| `surplus_power_w` | Solar surplus = PV − house load (W) |
| `surplus_capture_w` | Capture power offered to wallbox (W) |
| `grid_export_w` | Current grid export (W) |
| `candidate_power_w` | Winning charging power from `snap_to_power_step()` (W) |
| `battery_will_be_full` | Informational: does peak SOC today reach 100%? |
| `ev_safe` | `check_ev_safe` passed — EV allowed this cycle |
| `battery_min_soc_forecast_48h` | Min forecast SOC over next 48 h, with EV load subtracted (%) |
| `battery_min_soc_floor` | Floor value used by the safety rule (= `battery.reserve_percent`) |
| `ev_charging_source` | Active source: `surplus`, `forecast`, or `none` |

**Result icons:** ⚡ Rule 1 (Battery Full), 📊 Rule 2 (Solar Surplus), ⏸️ no charging.

## 4.7 Error Handling and Notifications

### 4.7.1 Battery Control Retry Logic

When controlling the battery via Home Assistant, the system implements retry logic to handle transient communication failures:

**Retry Configuration:**
- Maximum attempts: 5
- Delay between retries: 2 seconds
- Timeout per attempt: 30 seconds

**Error Types Handled:**

| Error Type | Behavior |
|------------|----------|
| Timeout | Retry after delay |
| Connection Error | Retry after delay |
| HTTP Error | Retry after delay |
| No HA Token | Fail immediately (no retry) |

### 4.7.2 Telegram Notifications

If all retry attempts fail, a Telegram notification is sent to alert the user.

**Notification Content:**
```
Error: Battery Control Failed

Failed to [enable/block] battery discharge after 5 attempts.

Entity: number.battery_maximum_discharging_power
Target value: [0/5000]W
Error: [error details]

The battery may not be in the expected state!
```

### 4.7.3 Error Flow

```
control_battery(discharge_allowed)
    |
    +-- Attempt 1 -> Fail -> Wait 2s
    +-- Attempt 2 -> Fail -> Wait 2s
    +-- Attempt 3 -> Fail -> Wait 2s
    +-- Attempt 4 -> Fail -> Wait 2s
    +-- Attempt 5 -> Fail
           |
           v
    Send Telegram Error Notification
    Log error
    (last_discharge_allowed unchanged - will retry next cycle)
```

### 4.7.4 Underlying Communication Chains

**Battery discharge control:**
```
EnergyManager -> HA REST API -> Huawei Solar Integration -> Modbus TCP -> Inverter
```

Each layer has its own error handling:
- **HA REST API**: 30s timeout, 5 retries (our code)
- **Huawei Solar Integration**: Login verification, permission handling
- **huawei-solar-lib**: 10s timeout, 3 retries with exponential backoff
- **Modbus TCP**: tModbus connection handling (v2.0.0b2 uses tModbus, not pyModbus)

#### Huawei Solar Stability Patches (applied to v2.0.0b2)

The stock `huawei_solar` v2.0.0b2 integration has a connection recovery defect: when the
SUN2000 inverter stops responding (e.g., `ServerDeviceBusyError` followed by timeouts), the
Modbus TCP connection remains "open" (connected to the modbus-proxy) but the inverter's
application layer is unresponsive. The stock code only forces a TCP reconnect on
`ModbusConnectionError`, not on `TimeoutError`, so it retries indefinitely on the same stale
connection.

Three patches are applied locally (files in `patches/`):

| File | Location in HA container | Fix |
|------|--------------------------|-----|
| `patches/tmodbus/async_smart.py` | `/usr/local/lib/python3.13/site-packages/tmodbus/transport/async_smart.py` | **Fix 1**: After 3 consecutive `TimeoutError` responses, set `_must_reconnect=True` to force TCP teardown/reopen. **Fix 2**: Move `_communication_lock` from class-level to per-instance (prevents cross-instance serialization). |
| `patches/huawei_solar/update_coordinator.py` | `/config/custom_components/huawei_solar/update_coordinator.py` | **Fix 3**: After 3 consecutive coordinator update failures (~90s), set `_must_reconnect=True` on the transport, ensuring the next poll cycle uses a fresh TCP connection. Also catches `TimeoutError` (not just `HuaweiSolarException`). |

**Important**: These patches are overwritten when the `huawei_solar` integration or HA core
is updated. Re-apply after updates using:
```bash
# From devcontainer:
source ~/.secrets/env
cat patches/tmodbus/async_smart.py | ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@192.168.0.202 \
  "docker exec -i homeassistant tee /usr/local/lib/python3.13/site-packages/tmodbus/transport/async_smart.py > /dev/null"
cat patches/huawei_solar/update_coordinator.py | ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@192.168.0.202 \
  "docker exec -i homeassistant tee /config/custom_components/huawei_solar/update_coordinator.py > /dev/null"
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@192.168.0.202 "ha core restart"
```

**EV charging control:**
```
EnergyManager -> POST /api/states/ -> number.wallbox_power_limit -> OCPP Server -> SetChargingProfile -> Wallbox
```

Note: `number.wallbox_power_limit` is a REST API entity (not a platform entity), so EnergyManager uses `POST /api/states/` instead of `number.set_value` service.

**Wallbox power correction (SUN2000 meter compensation):**
```
Wallbox -> MeterValues (OCPP) -> OCPP Server -> MQTT topic "wallbox" (every 10s) -> ESP32 Modbus Proxy -> RS485 -> SUN2000
```

The ESP32 Modbus Proxy adds wallbox power to the DTSU meter reading so the SUN2000 sees actual grid demand including the wallbox (which is wired outside the DTSU measurement loop).

# Chapter 5: Forecast Accuracy Tracking

## 5.1 Purpose

Forecast accuracy tracking serves to **improve decision-making quality** for energy optimization. The system makes critical decisions based on forecasted values, and understanding forecast accuracy allows us to:

1. Validate that forecasts are reliable enough for automated decisions
2. Identify systematic biases (over/under-forecasting)
3. Tune optimization parameters based on observed accuracy
4. Build confidence in the system's recommendations

## 5.2 Optimization Decisions Dependent on Forecasts

| Decision | Timing | Forecast Dependency | Impact of Error |
|----------|--------|---------------------|-----------------|
| **Battery discharge blocking** | 21:00 daily | PV forecast for next day | Grid import during expensive hours |
| **Appliance signal** (washer) | Real-time | PV surplus forecast | Suboptimal timing, grid usage |
| **EV charging power** | Every 15 min | SOC simulation with PV + load forecast | Missed solar charging, sub-optimal amp level |

## 5.3 Forecast Accuracy #1: Battery Discharge Optimization

### 5.3.1 Decision Context

At **21:00** each evening, the system decides whether to block battery discharge during cheap tariff hours (21:00-06:00). This decision depends on:

- **Current SOC** at 21:00
- **PV forecast** for the next day (06:00-21:00)
- **Load forecast** for the next day

The goal: Preserve battery energy during cheap hours so it's available during expensive hours (06:00-21:00) when PV production may be insufficient.

### 5.3.2 Accuracy Measurement Approach

**Snapshot at 21:00:**

At 21:00 each day, capture and store:
- PV forecast (P10/P50/P90) for the next 24 hours until 21:00, at **15-minute resolution**
- The specific `run_time` of the forecast being used
- Current SOC at decision time

**Compare with Actuals:**

After the forecast period completes (next day 21:00), compare:
- Forecasted PV energy (Wh) vs actual PV energy produced
- Per 15-minute period comparison
- Total daily comparison

### 5.3.3 InfluxDB Storage Schema

**Bucket:** `pv_forecast`

**Measurement:** `pv_forecast_snapshot`

Stores the "frozen" forecast at decision time, at 15-minute resolution, **per string**:

| Tag | Description |
|-----|-------------|
| `snapshot_type` | `battery_21h` (identifies this as the 21:00 battery decision snapshot) |
| `snapshot_id` | Date of decision in `YYYY-MM-DD` format (e.g., `2026-01-20`) |
| `inverter` | `EastWest`, `South`, or `total` |
| `string` | `East`, `West`, `SouthFront`, `SouthBack`, or `total` |
| `forecast_run_time` | Original forecast run timestamp |

| Field | Unit | Description |
|-------|------|-------------|
| `forecast_wh_p10` | Wh | Forecasted PV energy for this 15-min period (pessimistic) |
| `forecast_wh_p50` | Wh | Forecasted PV energy for this 15-min period (expected) |
| `forecast_wh_p90` | Wh | Forecasted PV energy for this 15-min period (optimistic) |

**Strings tracked:**

| String | Inverter | Orientation | Panels |
|--------|----------|-------------|--------|
| `East` | EastWest | Azimuth 90°, Tilt 15° | 8x AE455 |
| `West` | EastWest | Azimuth 270°, Tilt 15° | 9x AE455 |
| `SouthFront` | South | Azimuth 180°, Tilt 70° | 3x Generic400 |
| `SouthBack` | South | Azimuth 180°, Tilt 60° | 2x Generic400 |
| `total` | - | - | All 22 panels |

**Measurement:** `pv_forecast_snapshot_meta`

One record per decision:

| Tag | Description |
|-----|-------------|
| `snapshot_type` | `battery_21h` |
| `snapshot_id` | Date of decision in `YYYY-MM-DD` format |

| Field | Unit | Description |
|-------|------|-------------|
| `soc_at_decision` | % | Battery SOC when decision was made |
| `decision_discharge_blocked` | bool | Whether discharge was blocked |
| `forecast_run_time` | string | Which forecast run was used |

**Measurement:** `pv_accuracy`

After actuals are available, comparison at 15-minute resolution, **per string**:

| Tag | Description |
|-----|-------------|
| `snapshot_type` | `battery_21h` |
| `snapshot_id` | Date of original decision |
| `inverter` | `EastWest`, `South`, or `total` |
| `string` | `East`, `West`, `SouthFront`, `SouthBack`, or `total` |

| Field | Unit | Description |
|-------|------|-------------|
| `forecast_wh_p10` | Wh | What was forecasted (pessimistic) |
| `forecast_wh_p50` | Wh | What was forecasted (expected) |
| `forecast_wh_p90` | Wh | What was forecasted (optimistic) |
| `actual_wh` | Wh | What was actually produced |
| `error_wh` | Wh | forecast_p50 - actual (positive = over-forecast) |

### 5.3.4 Data Storage Summary

| Measurement | Purpose | Retention |
|-------------|---------|-----------|
| `pv_forecast_snapshot` | "What did we predict?" | Long-term |
| `pv_forecast_snapshot_meta` | "What did we decide, and why?" | Long-term |
| `pv_accuracy` | "Where did we go wrong?" | Long-term |

### 5.3.5 Visualization (Grafana)

**Dashboard Variables:**
- `$snapshot_id`: Date picker (e.g., `2026-01-20`) — selects which day's forecast to view
- `$inverter`: `EastWest`, `South`, `total`

**Panel 1: Forecast vs Actual Curve**

```flux
// Forecast snapshot for selected date
forecast = from(bucket: "pv_forecast")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot")
  |> filter(fn: (r) => r.snapshot_id == "${snapshot_id}")
  |> filter(fn: (r) => r.inverter == "${inverter}")

// Actual production
actual = from(bucket: "pv_forecast")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "pv_accuracy")
  |> filter(fn: (r) => r.snapshot_id == "${snapshot_id}")
  |> filter(fn: (r) => r.inverter == "${inverter}")
  |> filter(fn: (r) => r._field == "actual_wh")

union(tables: [forecast, actual])
```

**Panel 2: Decision Context**

```flux
from(bucket: "pv_forecast")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot_meta")
  |> filter(fn: (r) => r.snapshot_id == "${snapshot_id}")
```

**Panel 3: Historical Date Picker**

```flux
from(bucket: "pv_forecast")
  |> range(start: -90d)
  |> filter(fn: (r) => r._measurement == "pv_forecast_snapshot_meta")
  |> distinct(column: "snapshot_id")
```

### 5.3.6 Derived Metrics (Calculated in Grafana)

| Metric | Calculation |
|--------|-------------|
| Total forecast energy | `sum(forecast_wh_p50)` from `pv_accuracy` |
| Total actual energy | `sum(actual_wh)` from `pv_accuracy` |
| Error (Wh) | `forecast_total - actual_total` |
| Error (%) | `error / actual_total x 100` |
| MAPE | `mean(abs(forecast - actual) / actual)` for non-zero periods |
| Within P10-P90 | `actual_total >= sum(p10) AND actual_total <= sum(p90)` |

### 5.3.7 Implementation Location

Implemented in the **SwissSolarForecast** add-on:

**Schedule:**
- **21:00 daily (local time)**: Snapshot current forecast for next 24h, per string
- **21:15 daily**: Evaluate previous day's forecast vs actuals

### 5.3.8 Success Criteria

| Metric | Target | Acceptable |
|--------|--------|------------|
| Mean Absolute Percentage Error (MAPE) | < 15% | < 25% |
| P10-P90 coverage | 75-85% | 65-90% |
| Bias (mean error) | +/-5% | +/-10% |

## 5.4 Forecast Accuracy #2: Appliance Signal (Future)

> **Status:** To be defined after Accuracy #1 is implemented and validated.

## 5.5 Forecast Accuracy #3: EV Charging (Future)

> **Status:** To be defined when EV charging optimization is validated in production.

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

## C.4 InfluxDB Delete API Performance Issues

**Symptoms:**
- Add-ons hang at "Deleting future forecasts" step
- InfluxDB container using excessive memory (>5GB)
- High CPU usage on InfluxDB server
- Timeout errors in add-on logs

**Diagnosis:**

Check InfluxDB goroutine count:
```bash
curl http://192.168.0.203:8087/debug/pprof/goroutine?debug=1 | head -1
```

Normal: 100-200 goroutines. Problem: >1000 goroutines.

**Solution:**

1. **Restart InfluxDB container:**
   ```bash
   docker restart influxdb2
   ```

2. **Verify recovery:**
   ```bash
   docker stats influxdb2 --no-stream
   ```
   Memory should drop to ~2GB.

**Prevention:**

All add-ons use `run_time` as a field instead of a tag. This allows points to overwrite on the same timestamp without needing delete operations. The delete API calls have been removed from the code.

**Technical Background:**

InfluxDB 2.x points are uniquely identified by: `measurement + tags + timestamp`

- If `run_time` is a **tag**: Each forecast run creates NEW points (duplicates accumulate)
- If `run_time` is a **field**: Points OVERWRITE on same timestamp+tags (no duplicates)

The delete API in InfluxDB 2.x can be slow with large datasets and may cause goroutine deadlocks under certain conditions.

---

# Appendix D: Test Cases

## D.1 Battery Discharge Optimizer Tests

Test file: `energymanager/tests/test_battery_optimizer.py`

#### Expensive Tariff (06:00-21:00) → Always ALLOW

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_expensive_tariff_allows_discharge` | At 12:00 (expensive), discharge should be allowed regardless of SOC forecast | Time: Monday 11:00, PV: 0W, Load: 2000W, SOC: 50% | `discharge_allowed=True`, reason contains "Expensive tariff" |
| `test_expensive_tariff_low_soc_still_allows` | Even with low SOC during expensive tariff, discharge is allowed | Time: Monday 14:00, PV: 0W, Load: 5000W, SOC: 15% | `discharge_allowed=True` |

#### Cheap Tariff + SOC OK → ALLOW

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_cheap_tariff_high_pv_allows_discharge` | At 22:00 (cheap), with good PV forecast, discharge should be allowed | Time: Monday 21:30, PV: 4000W during day, Load: 500W, SOC: 80% | `discharge_allowed=True`, reason contains "SOC stays >=" |
| `test_cheap_tariff_full_battery_allows_discharge` | With 100% SOC and good PV, should allow discharge | Time: Monday 22:00, PV: 5000W during day, Load: 400W, SOC: 100% | `discharge_allowed=True` |

#### Cheap Tariff + SOC NOT OK → BLOCK

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_cheap_tariff_low_pv_blocks_discharge` | At 22:00 (cheap), with poor PV forecast, discharge should be blocked | Time: Monday 21:30, PV: 500W (cloudy), Load: 1500W, SOC: 50% | `discharge_allowed=False`, reason contains "Block" |
| `test_cheap_tariff_low_soc_blocks_discharge` | At 22:00 (cheap), with low starting SOC, discharge should be blocked | Time: Monday 22:00, PV: 2000W, Load: 1000W, SOC: 20% | `discharge_allowed=False` |
| `test_min_soc_threshold_respected` | Custom threshold (20%) is respected | Time: Monday 22:00, threshold: 20%, SOC: 40% | If `min_soc_percent < 20%` then `discharge_allowed=False` |

#### Self-Correcting Behavior

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_block_then_allow_as_conditions_improve` | If initially blocked, later check with better SOC should allow | Same forecast, First: SOC 30%, Second: SOC 90% | `decision2.min_soc_percent > decision1.min_soc_percent`, `decision2.discharge_allowed=True` |

#### Edge Cases

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_no_forecast_data_allows_discharge` | With no forecast data, default to allowing discharge | Empty forecast DataFrame | `discharge_allowed=True`, reason: "No forecast data" |
| `test_weekend_all_day_cheap` | Weekend is all-day cheap tariff | Saturday 12:00 | `tariff.is_cheap_now=True` |
| `test_weekday_morning_is_expensive` | Weekday 08:00 should be expensive tariff | Monday 08:00 | `tariff.is_cheap_now=False` |
| `test_weekday_night_is_cheap` | Weekday 23:00 should be cheap tariff | Monday 23:00 | `tariff.is_cheap_now=True` |
| `test_holiday_is_cheap` | Configured holidays should be all-day cheap | 2026-01-01 12:00, holidays=["2026-01-01"] | `is_holiday=True`, `is_cheap_day=True` |

#### Hysteresis (prevent oscillation at soc_ok boundary)

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_previously_blocked_requires_margin_to_reallow` | When previously blocked, min_soc barely above 10% stays blocked | Cheap tariff, min_soc 10-12%, `previously_blocked=True` | `discharge_allowed=False` |
| `test_previously_blocked_allows_with_clear_margin` | When previously blocked but min_soc clearly above 12%, allow | Cheap tariff, SOC 90%, `previously_blocked=True` | `discharge_allowed=True` |
| `test_not_previously_blocked_allows_at_threshold` | When not previously blocked, min_soc at 10% allows normally | Cheap tariff, min_soc 10-12%, `previously_blocked=False` | `discharge_allowed=True` |

#### Dataclass Validation

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_decision_has_required_fields` | DischargeDecision has all required fields | Create DischargeDecision | Has `discharge_allowed`, `reason`, `min_soc_percent` fields |

**Run tests:**
```bash
cd energymanager && python -m pytest tests/test_battery_optimizer.py -v
```

**All 24 tests passing** (as of v1.6.97)

---

## D.2 Appliance Signal Tests

Test file: `energymanager/tests/test_appliance_signal.py`

#### GREEN Signal: PV excess > appliance power

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_green_when_pv_excess_above_threshold` | PV excess 3000W > 2500W appliance power | PV: 4000W, Load: 1000W, appliance_power: 2500W | `signal="green"`, excess_power=3000W |
| `test_green_ignores_soc_when_pv_sufficient` | Even with low SOC, GREEN if PV excess sufficient | PV: 5000W, Load: 2000W, SOC: 5% | `signal="green"` |
| `test_not_green_when_pv_excess_exactly_equals_threshold` | PV excess exactly 2500W (need >) | PV: 3500W, Load: 1000W | `signal != "green"` |

#### ORANGE Signal: Min SOC% >= reserve% + appliance%

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_orange_when_soc_above_threshold` | Min SOC 30% >= 25% threshold | Min SOC: 30%, reserve: 10%, appliance: 15% | `signal="orange"` |
| `test_orange_exactly_at_threshold` | Min SOC exactly at threshold (25%) | Min SOC: 25%, reserve: 10%, appliance: 15% | `signal="orange"` |
| `test_orange_threshold_calculation` | Different parameters: 20% reserve + 20% appliance = 40% | Min SOC: 45%, reserve: 20%, appliance: 2000Wh/10000Wh=20% | `signal="orange"` |
| `test_orange_with_different_battery_capacity` | 15kWh battery: 1500Wh = 10% appliance | Capacity: 15kWh, Min SOC: 25%, threshold: 20% | `signal="orange"` |

#### ORANGE Signal: Grid export >= appliance energy

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_orange_when_exporting_enough_energy` | Export 2000Wh >= 1500Wh threshold | Min SOC: 10%, Export: 2000Wh | `signal="orange"` |
| `test_orange_when_export_exactly_equals_threshold` | Export exactly 1500Wh | Min SOC: 10%, Export: 1500Wh | `signal="orange"` |
| `test_red_when_export_below_threshold` | Export 1000Wh < 1500Wh | Min SOC: 10%, Export: 1000Wh | `signal="red"` |
| `test_soc_check_takes_priority_over_export` | SOC check before export check | Min SOC: 30% (above threshold) | `signal="orange"` with SOC reason |

#### RED Signal: Min SOC% < threshold AND export < appliance

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_red_when_soc_below_threshold` | Min SOC 20% < 25% threshold | Min SOC: 20%, reserve: 10%, appliance: 15% | `signal="red"` |
| `test_red_with_zero_pv` | No PV and low SOC | PV: 0W, Min SOC: 15% | `signal="red"` |
| `test_red_just_below_threshold` | Min SOC 24% just below 25% | Min SOC: 24%, threshold: 25% | `signal="red"` |

#### Min SOC Check (dip and recover scenarios)

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_red_when_soc_dips_below_reserve` | SOC dips to 0% but recovers to 48% | Min SOC: 0%, Final SOC: 48% | `signal="red"` |
| `test_red_when_soc_dips_just_below_threshold` | SOC dips to 24% (just below 25%) | Min SOC: 24%, Final SOC: 48% | `signal="red"` |
| `test_orange_when_soc_stays_above_threshold` | SOC stays above 25% threshold | Min SOC: 30%, Final SOC: 30% | `signal="orange"` |
| `test_orange_when_min_soc_exactly_at_threshold` | SOC dips to exactly 25% | Min SOC: 25%, Final SOC: 30% | `signal="orange"` |

#### Edge Cases

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_empty_simulation_returns_red` | Empty simulation DataFrame | Empty DataFrame | `signal="red"` (safe default) |
| `test_simulation_without_soc_column` | Missing soc_percent column | DataFrame without soc_percent | `signal="red"` |
| `test_negative_pv_excess` | Load > PV (deficit) | PV: 500W, Load: 2000W, Min SOC: 30% | `signal="orange"` (checks SOC threshold) |
| `test_zero_reserve_percent` | Zero reserve, only need appliance% | reserve: 0%, appliance: 15%, Min SOC: 16% | `signal="orange"` |
| `test_high_reserve_percent` | High reserve (30%) changes threshold | reserve: 30%, appliance: 15%, Min SOC: 40% | `signal="red"` (threshold=45%) |

#### Helper Functions

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_returns_last_value` | get_final_soc_percent returns last value | Simulation ending at 42% | Returns 42% |
| `test_returns_minimum_value` | get_min_soc_percent returns min value | Simulation dipping to 5% | Returns 5% |
| `test_empty_dataframe_returns_zero` | Empty DataFrame returns 0 | Empty DataFrame | Returns 0% |
| `test_missing_column_returns_zero` | Missing column returns 0 | DataFrame without soc_percent | Returns 0% |

#### Dataclass Validation

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_dataclass_fields` | ApplianceSignal has all required fields | Create ApplianceSignal | Has `signal`, `reason`, `excess_power_w`, `final_soc_percent` |

**Run tests:**
```bash
cd energymanager && python -m pytest tests/test_appliance_signal.py -v
```

**All 26 tests passing** (as of v1.5.12)

---

## D.3 EV Charging State Machine Tests

Test file: `energymanager/tests/test_ev_state_machine.py`

**66 unit tests** organized by state, covering all transitions defined in Section 4.3.5:

### State stay tests

| Category | # Tests | Description |
|----------|---------|-------------|
| TestEVStateEnum | 3 | Enum has 4 states, str inheritance, snake_case values |
| TestInit | 1 | Initial state is IDLE |
| TestNormalStays | 4 | Stays IDLE: no wallbox, solar enters without battery protection, excess below min |
| TestSolarStays | 3 | Stays SOLAR: with excess, low excess holds min_power_w, stays regardless of battery protection |
| TestCheapStays | 2 | Stays CHEAP: expensive tariff (0W), cheap tariff (max_power_w) |
| TestMaxStays | 2 | Stays IMMEDIATE: at max_power_w, with custom max |

### State transition tests

| Category | # Tests | Transitions covered |
|----------|---------|---------------------|
| TestNormalTransitions | 7 | N1 (→ IMMEDIATE), N2 (→ CHEAP), N3 (→ SOLAR), wallbox blocks, battery protection ignored, priority |
| TestSolarTransitions | 3 | S2 (→ IMMEDIATE), S3 (→ CHEAP, cheap/expensive tariff) |
| TestCheapTransitions | 2 | C2 (mode changed to solar/immediate) |
| TestMaxTransitions | 2 | M2 (mode changed to solar/cheap) |

### CHEAP power toggle tests

| Category | # Tests | Description |
|----------|---------|-------------|
| TestCheapPowerToggle | 4 | Max when cheap, zero when expensive, toggle back-and-forth, custom max_power_w |

### Min-stay timer tests (SOLAR)

| Category | # Tests | Description |
|----------|---------|-------------|
| TestMinStayTimer | 4 | Hold min_power_w during low excess, S2 fires during min-stay, entered_at set/cleared |

### Excess calculation & power clamping tests (SOLAR)

| Category | # Tests | Description |
|----------|---------|-------------|
| TestSolarPower | 7 | Clamp to min/max, round to 100W step, excess includes wallbox_power_w, low excess holds minimum, custom min/max |
| TestRoundToStep | 4 | Round down/up/exact/midpoint |
| TestSolarTarget | 3 | Above min, below min returns min, above max clamps |

### Multi-step sequence tests

| Category | # Tests | Scenarios |
|----------|---------|-----------|
| TestMultiStep | 3 | IDLE → SOLAR → IDLE (mode change); full mode cycle (IMMEDIATE → IDLE → CHEAP → IDLE → SOLAR); SOLAR → IMMEDIATE → IDLE → SOLAR |

### wallbox_available guard tests

| Category | # Tests | Description |
|----------|---------|-------------|
| TestWallboxAvailable | 3 | Immediate/cheap/solar all blocked when wallbox_available=False |

**Run tests:**
```bash
cd energymanager && python -m pytest tests/test_ev_state_machine.py -v
```

**Integration tests (manual):**

| ID | Test | Expected |
|----|------|----------|
| EV-10 | Dashboard: tap Cheap Charge | `input_select.ev_charging_mode` = `cheap` |
| EV-11 | Dashboard: tap Charge Now | `input_select.ev_charging_mode` = `immediate` |
| EV-12 | Dashboard: tap active button | `input_select.ev_charging_mode` = `solar` (back to default) |
| EV-13 | Dashboard: car connected, charging | Card shows power in W, state = SOLAR/CHEAP/IMMEDIATE |
| EV-14 | Mode change while charging | New mode takes effect within ~60 s (OCPP throttle) |
| EV-15 | Battery safety gates charging | SOLAR mode: surplus above threshold but `check_ev_safe` fails → ev_charging_power_w=0, stays IDLE; dashboard `ev_safe=false` and `battery_min_soc_forecast_48h` below `battery_min_soc_floor` |
| EV-16 | Battery full, low excess | SOLAR with 1-phase min_power_w — captures every watt |
| EV-17 | Cheap tariff toggles | CHEAP state: charges at max during cheap, pauses during expensive, no state change |
| EV-18 | Car reaches target SOC | Returns to IDLE from any charging state |

---

## D.4 Discharge Blocking Tests

Test file: `energymanager/tests/test_discharge_blocking.py`

Tests the two-flag discharge blocking logic (Section 4.2.2) where battery protection and EV charging independently block discharge, combined with OR logic.

#### `_update_discharge_control()` — OR Truth Table

| Test | Description | Protection | EV | Expected |
|------|-------------|-----------|-----|----------|
| `test_both_off_allows_discharge` | Neither flag set | off | off | `control_battery(True)` |
| `test_protection_blocks` | Protection flag only | on | off | `control_battery(False)` |
| `test_ev_blocks` | EV flag only | off | on | `control_battery(False)` |
| `test_both_block` | Both flags set | on | on | `control_battery(False)` |
| `test_no_call_when_unchanged` | Already allowed, still allowed | off | off | No call (unchanged) |
| `test_calls_on_transition_allow_to_block` | Was allowed, now blocked | — | on | `control_battery(False)` |
| `test_calls_on_transition_block_to_allow` | Was blocked, now allowed | off | off | `control_battery(True)` |

#### EV Flag — Immediate/Cheap Mode Charging

| Test | Description | Expected |
|------|-------------|----------|
| `test_immediate_charging_blocks_discharge` | Immediate mode, power > 0 | `_discharge_blocked_by_ev = True`, discharge blocked |
| `test_immediate_zero_power_clears_flag` | Immediate mode, power = 0 | `_discharge_blocked_by_ev = False`, discharge allowed |
| `test_flag_not_toggled_when_already_set` | Flag already True, power > 0 | No redundant `control_battery` call |
| `test_flag_not_toggled_when_already_clear` | Flag already False, power = 0 | No redundant `control_battery` call |

#### Solar Mode Clears EV Flag

| Test | Description | Expected |
|------|-------------|----------|
| `test_solar_mode_clears_ev_flag` | Switch from immediate to solar | `_discharge_blocked_by_ev = False`, discharge allowed |
| `test_solar_mode_noop_when_flag_already_clear` | Solar mode, flag already False | No `control_battery` call |

#### Combined: Protection + EV Interaction

| Test | Description | Expected |
|------|-------------|----------|
| `test_ev_stops_but_protection_keeps_blocked` | EV finishes but protection active | Stays blocked (no call — unchanged) |
| `test_protection_clears_but_ev_keeps_blocked` | Protection clears but EV charging | Stays blocked (no call — unchanged) |
| `test_both_clear_allows_discharge` | Both flags cleared | `control_battery(True)` |
| `test_ev_starts_during_protection_block` | EV starts while protection blocks | Stays blocked (no call — unchanged) |

**Run tests:**
```bash
cd energymanager && python -m pytest tests/test_discharge_blocking.py -v
```

**All 19 tests passing** (as of v1.6.28)

---

## D.5 EV Charging Power Tests

Test file: `energymanager/tests/test_ev_charging.py`

Tests the `snap_to_power_step()`, `calculate_ev_power()`, and `resolve_phase_gap()` logic (Section 4.3.6).

#### `snap_to_power_step()` — Discrete M-Bus Power Steps

| Test | Surplus | Expected | Reason |
|------|---------|----------|--------|
| `test_surplus_5000_picks_4354` | 5000 W | 4354 W | Highest step ≤ 5000 (7A) |
| `test_surplus_below_steps_returns_min` | 2000 W | 3962 W | Below all steps → min (battery covers gap) |
| `test_surplus_above_max_picks_max` | 12000 W | 7624 W | Max step (12A) |
| `test_exact_step_boundary` | 6288 W | 6288 W | Exact 10A step |
| `test_custom_power_range` | 5000 W (min=5117) | 5117 W | Min valid step |
| `test_custom_max` | 12000 W (max=6288) | 6288 W | Capped at custom max |
| `test_between_steps` | 5200 W | 5117 W | Highest step ≤ 5200 (8A) |
| `test_just_at_min_step` | 3962 W | 3962 W | Exact min step (6A) |

#### `resolve_phase_gap()` — Dead Zone Handling

| Test | Input | battery_full | Expected |
|------|-------|-------------|----------|
| `test_in_gap_battery_not_full_snaps_down` | 3900 W | False | 3680 W |
| `test_in_gap_battery_full_snaps_up` | 3900 W | True | 4140 W |
| `test_at_gap_lo_no_snap` | 3680 W | False | 3680 W (boundary exclusive) |
| `test_at_gap_hi_no_snap` | 4140 W | True | 4140 W (boundary exclusive) |
| `test_below_gap_unaffected` | 2000 W | False | 2000 W |
| `test_above_gap_unaffected` | 7000 W | True | 7000 W |

#### `calculate_ev_power()` — Solar Clamp + Gap

| Test | Excess | Expected | Reason |
|------|--------|----------|--------|
| `test_below_min_pauses` | 1000 W | 0 W | Below 1400 W minimum |
| `test_excess_in_gap_snaps_down` | 3900 W | 3680 W | Gap snap (battery not full) |
| `test_excess_in_gap_battery_full_snaps_up` | 3900 W | 4140 W | Gap snap (battery full) |
| `test_at_gap_hi_stays` | 4140 W | 4140 W | At boundary (exclusive) → stays |
| `test_normal_excess_unaffected` | 7000 W | 7000 W | Normal pass-through |
| `test_clamps_to_max` | 15000 W | 11000 W | Clamped to max_power_w |

#### Phase-Gap Stability (IT-PHASE-01)

| Test | Description | Expected |
|------|-------------|----------|
| `test_cloud_fluctuation_battery_not_full` | 20 excess values oscillating in gap (3750–4130 W) | All snap to 3680 W, zero phase switches |
| `test_cloud_fluctuation_battery_full` | Same series, battery full | All snap to 4140 W, zero phase switches |

**Run tests:**
```bash
cd energymanager && python -m pytest tests/test_ev_charging.py -v
```

**All 20 tests passing** (as of v1.6.80)

---

## D.6 Integration Tests

Integration tests verify cross-module behavior — interactions between EV charging, battery optimizer, tariff boundaries, OCPP transactions, and fail-safes.

### D.6.1 Staleness & Timing (Category A)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-STALE-01 | Stale M-Bus reading falls back to DTSU | Mock M-Bus `last_updated` > 20 s ago | `_read_grid_power()` returns DTSU value | 🔮 Future — requires HA client mock |
| IT-STALE-02 | Both meters stale → safe default | Both sensors return None | EV power set to 0 (pause) | 🔮 Future — requires HA client mock |
| IT-TIME-01 | Scheduler fires at 15-min boundaries | APScheduler mock with time steps | `run_optimization` called at :00, :15, :30, :45 | 🔮 Future — requires scheduler mock |
| IT-TIME-02 | EV loop runs at 10 s interval | APScheduler mock | `control_ev_charging` called every 10 s | 🔮 Future — requires scheduler mock |

### D.6.2 Fail-Safe & Watchdog (Category B)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-FAIL-01 | InfluxDB down → discharge allowed | `forecast_reader.get_combined_forecast` raises | Decision defaults to allow | 🔮 Future — requires InfluxDB mock |
| IT-FAIL-02 | HA API down → no battery control | `ha_client.set_battery_discharge_power` fails 5× | Telegram notification sent | 🔮 Future — requires HA + Telegram mock |
| IT-FAIL-03 | Smart car API timeout → stale SOC retained | `HelloSmartClient.authenticate` raises | Previous SOC entity unchanged | 🔮 Future — requires Smart car mock |
| IT-FAIL-04 | Wallbox disconnects mid-charge → power limit reset | `wallbox_connected` transitions False | No power limit commands sent | 🔮 Future — requires OCPP mock |

### D.6.3 Phase Switching & Gap (Category C)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-PHASE-01 | Cloud fluctuation stability | 20 excess values oscillating in gap | All snap to one side, zero phase switches | ✅ `test_ev_charging.py::TestPhaseGapStability` |
| IT-PHASE-02 | Phase transition on battery-full change | Excess in gap, toggle `battery_full` | Output switches 3700↔4140 only on flag change | 🔮 Future — pure-logic (extend TestPhaseGapStability) |
| IT-PHASE-03 | Wallbox confirms phase switch | OCPP `MeterValues` after gap-snap change | Measured power matches target phase | 🔮 Future — requires OCPP mock |

### D.6.4 Battery ↔ EV Cross-Coupling (Category D)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-BATT-01 | Cheap mode blocks discharge | `ev_charging_mode = "cheap"`, power > 0 | `_discharge_blocked_by_ev = True` | ✅ `test_discharge_blocking.py::TestCheapModeBlocksDischarge` |
| IT-BATT-02 | Battery safety blocks EV | Forecast min SOC in next 48 h < `battery.reserve_percent` with candidate EV load | `ev_charging_power_w = 0`, `ev_safe = False` (dashboard) | ✅ `test_ev_battery.py::TestSafetyGate` |
| IT-BATT-03 | Tariff boundary transitions | 20:59 (expensive), 21:01 (cheap), 05:59 (cheap), 06:01 (expensive) | Correct `is_cheap_now` flag | ✅ `test_battery_optimizer.py::TestTariffBoundaryTransitions` |
| IT-BATT-04 | Wallbox idle detection exits all modes | SOLAR/CHEAP/IMMEDIATE + `wallbox_idle=True` | State machine → IDLE, 0 W | ✅ `test_ev_state_machine.py::TestIdleDetection` |

### D.6.5 Authorization & Transaction (Category E)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-OCPP-01 | RFID authorize → transaction start | OCPP `Authorize.req` with valid tag | `StartTransaction.conf` accepted, power flows | 🔮 Future — requires OCPP handler mock |
| IT-OCPP-02 | Remote stop → transaction ends | `RemoteStopTransaction.req` during charge | Power → 0, `StopTransaction.req` sent | 🔮 Future — requires OCPP handler mock |

### D.6.6 End-to-End Scenarios (Category F)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-E2E-01 | Full solar day: battery + EV + appliance | Sunny forecast, battery 50%, car connected | Battery fills, EV charges surplus, appliance GREEN | 🔮 Future — requires all mocks |
| IT-E2E-02 | Cloudy day with cheap-mode EV | Low PV forecast, cheap mode at 21:30 | Discharge blocked, EV charges at max, battery holds | 🔮 Future — requires all mocks |

**Legend:** ✅ Implemented and passing | 🔮 Future (prerequisite listed)

## D.7 Passive Integration Observer Tests

24 tests (11 normal, 13 edge) run automatically every 10 s cycle during live operation.
Results persist to `/config/ev_integration_tests.json`; Telegram notifications on status changes.

Report version: **3** (bumped when test definitions change — invalidates stale results).

### D.7.1 Normal Operation (11 tests)

| ID | Name | Preconditions | Pass condition |
|----|------|---------------|----------------|
| NO-01 | IDLE when wallbox unavailable | mode=solar, wallbox unavailable (any prev state) | state=IDLE, power=0 |
| NO-02 | IDLE→SOLAR on charging power>0 | prev=IDLE, mode=solar, available, ev_charging_power_w>0 | state=SOLAR |
| NO-05 | SOLAR power equals charging power | state=SOLAR, ev_charging_power_w>0 | target_power_w == ev_charging_power_w |
| NO-06 | IDLE→IMMEDIATE | prev=IDLE, mode=immediate, available | state=IMMEDIATE, power=max |
| NO-07 | IMMEDIATE→IDLE mode change | prev=IMMEDIATE, mode≠immediate | state=IDLE, power=0 |
| NO-08 | Immediate→solar sends 0W | prev=IMMEDIATE, mode=solar | state=IDLE, power=0 |
| NO-09 | IDLE→CHEAP | prev=IDLE, mode=cheap, available | state=CHEAP |
| NO-10 | CHEAP charges at max (cheap tariff) | state=CHEAP, cheap tariff | power=max |
| NO-11 | CHEAP pauses (expensive tariff) | state=CHEAP, expensive tariff | power=0 |
| NO-12 | IMMEDIATE blocks discharge | state=IMMEDIATE, power>0 | discharge_blocked=True |
| NO-13 | SOLAR exits when strategy returns 0 | prev output=SOLAR, strategy≤0, not idle, mode=solar | state=IDLE, power=0 |

### D.7.2 Edge Cases (13 tests)

| ID | Name | Preconditions | Pass condition |
|----|------|---------------|----------------|
| EC-02 | SOLAR does NOT block discharge | state=SOLAR | discharge_blocked=False |
| EC-03 | CHEAP blocks discharge when charging | state=CHEAP, cheap tariff, power>0 | discharge_blocked=True |
| EC-04 | CHEAP unblocks at expensive tariff | state=CHEAP, expensive tariff, power=0 | discharge_blocked=False |
| EC-05 | Battery protection blocks SOLAR entry | prev=IDLE, mode=solar, available, surplus≥min_power, ev_charging_power_w=0 | state=IDLE |
| EC-06 | Battery protection exits SOLAR | prev output=SOLAR, surplus≥min_power, ev_charging_power_w=0, not idle, mode=solar | state=IDLE, power=0 |
| EC-07 | Battery protection grace holds SOLAR | state=SOLAR, protection=False | power>0 |
| EC-08 | SOLAR→IMMEDIATE | prev=SOLAR, mode=immediate | state=IMMEDIATE, power=max |
| EC-09 | SOLAR→CHEAP | prev=SOLAR, mode=cheap | state=CHEAP |
| EC-12 | Power limit sent only on change | prev exists, power unchanged | last_sent unchanged |
| EC-13 | Auto-revert: mode resets to solar | prev mode=immediate/cheap, curr mode=solar, idle≥5min | state=IDLE |
| EC-14 | Faulted/Unknown → IDLE | wallbox Faulted/Unknown | state=IDLE, power=0 |
| EC-15 | CHEAP→IDLE clears discharge | prev=CHEAP, mode≠cheap | state=IDLE, power=0, blocked=False |
| EC-16 | Idle detection exits to IDLE | prev=SOLAR/CHEAP/IMMEDIATE, idle=True | state=IDLE, power=0 |

---

# Appendix E: EnergyManager Configuration

See **Section 1.10** for the full configuration architecture.

Secrets (InfluxDB token, Telegram credentials) are entered in the HA add-on Configuration UI — see Section 1.11.2. They are **not** stored in the YAML file below.

## E.1 Non-Secrets (`/config/energymanager.yaml`)

Editable via File Editor at `/addon_configs/energymanager/energymanager.yaml`:

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"
  pv_bucket: "pv_forecast"
  load_bucket: "load_forecast"
  output_bucket: "energy_manager"

battery:
  capacity_kwh: 10.0
  charge_efficiency: 0.95
  discharge_efficiency: 0.95
  max_charge_w: 5000
  max_discharge_w: 5000
  soc_entity: "sensor.battery_state_of_capacity"
  discharge_control_entity: "number.battery_maximum_discharging_power"

tariff:
  weekday_cheap_start: "21:00"
  weekday_cheap_end: "06:00"
  weekend_all_day_cheap: true
  holidays: []

appliances:
  power_w: 2500
  energy_wh: 1500

sensors:
  pv_power: "sensor.solar_pv_total_ac_power"
  load_power: "sensor.house_load_power"
  surplus_power: "sensor.surplus_power"
  mbus_grid_power: "sensor.grid_power"            # EBL smart meter via gPlug M-Bus (preferred, < 20s)
  dtsu_grid_power: "sensor.power_meter_active_power"  # Huawei DTSU666-H at inverter (fallback)

ev_charging:
  enabled: true
  mode_entity: "input_select.ev_charging_mode"
  min_solar_power_w: 3500                         # Minimum energy budget for solar charging (battery buffers gap)
  min_current_a: 6                                # Wallbox hardware minimum (amps)
  max_current_a: 16                               # Wallbox hardware maximum (amps)
  max_power_w: 11000
  protection_soc_percent: 80                      # Target SOC at 21:00 on good days

smart_car:
  enabled: true
  soc_entity: "sensor.smart_battery"              # EV SOC sensor (with InfluxDB fallback when unavailable)
  capacity_kwh: 100.0                             # EV usable battery capacity, used by Car SOC Forecast (Section 4.3.8)
  charge_efficiency: 0.9                          # End-to-end surplus → car efficiency (wallbox + house-battery cycle + standby)

schedule:
  update_interval_minutes: 15

log_level: "info"
```

---

# Appendix F: Smart Car API — Raw Data & HA Entity Mapping

## F.1 API Endpoint

```
GET /remote-control/vehicle/status/{vin}?latest=true&target=&userId={userId}
Host: api.ecloudeu.com  (Smart #1/#3)  or  apiv2.ecloudeu.com  (Smart #5)
```

Requires HMAC-signed request with app token (see `smart_car.py` for auth flow).

## F.2 Raw `electricVehicleStatus` Object

The status response is nested under `data.vehicleStatus.additionalVehicleStatus.electricVehicleStatus`.
Below is a representative snapshot (Smart #5, February 2026):

```json
{
  "chargeLevel": 85,
  "chargerState": 2,
  "statusOfChargerConnection": 2,
  "chargeSts": 0,
  "dcChargeSts": 0,
  "chargeIAct": 15.5,
  "chargeUAct": 402.0,
  "dcChargeIAct": 0.0,
  "timeToFullyCharged": 110,
  "timeToTargetDisCharged": 2047,
  "distanceToEmptyOnBatteryOnly": 459,
  "distanceToEmptyOnBattery100Soc": 429,
  "distanceToEmptyOnBattery20Soc": 85,
  "batteryTemperature": 22,
  "chargeMode": 0,
  "chargePHV": 0,
  "chargeLidAcStatus": 2,
  "chargeLidDcAcStatus": 1,
  "disChargeUAct": 0.0,
  "disChargeIAct": 0.0,
  "disChargeSts": 0,
  "disChargeConnectStatus": 0,
  "dcDcActvd": 1,
  "dcDcConnectStatus": 0,
  "bookChargeSts": 0,
  "wptFineAlignt": 0,
  "ptReady": 0,
  "averPowerConsumption": -86.3,
  "indPowerConsumption": 0.0,
  "energyConsumed": 0,
  "energyRegenerated": 0
}
```

### Field Reference — Charging

| API Field | Type | Description |
|-----------|------|-------------|
| `chargeLevel` | int | Battery SOC in % (0–100) |
| `chargerState` | int | High-level charging state machine (see F.2.1) |
| `statusOfChargerConnection` | int | Physical cable connection state (see F.2.2) |
| `chargeSts` | int | AC charge status flag: 0 = not AC charging, 3 = AC charging active |
| `dcChargeSts` | int | DC charge status flag: 0 = not DC charging |
| `chargeIAct` | float | AC charging current (A). 0 when not AC charging |
| `chargeUAct` | float | Charging voltage (V). Battery-side DC voltage during AC charging (e.g. 402V), not AC mains. pySmartHashtag uses < 260V to detect single-phase |
| `dcChargeIAct` | float | DC fast charging current (A). Negative = charging (e.g. −102.6A). 0 when not DC charging |
| `timeToFullyCharged` | int | Minutes to full charge; 2047 = N/A |
| `timeToTargetDisCharged` | int | Minutes to V2L discharge target; 2047 = N/A |
| `chargeMode` | int | Charge mode (0 = normal/auto) |
| `bookChargeSts` | int | Scheduled/booked charge status: 0 = none active |

### Field Reference — Physical / Lids

| API Field | Type | Description |
|-----------|------|-------------|
| `chargeLidAcStatus` | int | AC charge port lid: 1 = open, 2 = closed |
| `chargeLidDcAcStatus` | int | DC charge port lid: 1 = open, 2 = closed |
| `dcDcActvd` | int | 12V DC-DC converter: 0 = inactive, 1 = active |
| `dcDcConnectStatus` | int | DC-DC connector: 0 = not connected, 3 = connected |
| `wptFineAlignt` | int | Wireless charging alignment (0 = N/A, not equipped) |
| `ptReady` | int | Powertrain ready: 0 = off |

### Field Reference — V2L (Vehicle-to-Load)

| API Field | Type | Description |
|-----------|------|-------------|
| `disChargeUAct` | float | V2L discharge voltage (V). 0 when inactive |
| `disChargeIAct` | float | V2L discharge current (A). 0 when inactive |
| `disChargeSts` | int | V2L status: 0 = not discharging |
| `disChargeConnectStatus` | int | V2L connection: 0 = not connected, 1/3 = connected |

### Field Reference — Range & Energy

| API Field | Type | Description |
|-----------|------|-------------|
| `distanceToEmptyOnBatteryOnly` | int | Remaining range (km) at current SOC |
| `distanceToEmptyOnBattery100Soc` | int | Estimated range (km) at 100% SOC |
| `distanceToEmptyOnBattery20Soc` | int | Estimated range (km) at 20% SOC |
| `batteryTemperature` | int | HV battery pack temperature (°C) |
| `averPowerConsumption` | float | Average power consumption (Wh/km, negative convention) |
| `indPowerConsumption` | float | Instantaneous power consumption |
| `energyConsumed` | int | Trip energy consumed |
| `energyRegenerated` | int | Trip energy regenerated (regen braking) |
| `chargePHV` | int | Plug-in hybrid voltage (0 for BEV) |

### F.2.1 `chargerState` Values

Full enum from pySmartHashtag (`ChargingState` array, indexed by value):

| Value | Name | Meaning |
|-------|------|---------|
| 0 | `NOT_CHARGING` | Not charging, no charger activity |
| 1 | `DEFAULT` | Default/idle state |
| 2 | `CHARGING` | AC charging active |
| 3 | `ERROR` | Charging error |
| 4 | `COMPLETE` | Charge complete |
| 5 | `FULLY_CHARGED` | Fully charged |
| 6 | `FINISHED_FULLY_CHARGED` | Finished, fully charged |
| 7 | `FINISHED_NOT_FULL` | Finished, not fully charged (target SOC reached or user stopped) |
| 8 | `INVALID` | Invalid state |
| 9 | `PLUGGED_IN` | Plugged in but not charging |
| 10 | `WAITING_FOR_CHARGING` | Waiting for scheduled charge |
| 11 | `TARGET_REACHED` | Target SOC reached |
| 12–14 | `UNKNOWN` | Reserved / unknown |
| 15 | `DC_CHARGING` | DC fast charging active |

**Observed in our system:** 0 (NOT_CHARGING), 2 (CHARGING), 4 (COMPLETE).

**Idle detection relevance:** Values 4, 5, 6, 7, 9, 11 all indicate "plugged in, not charging" — any of these could signal that the car has finished and the energy manager should exit to IDLE.

Sources: [pySmartHashtag](https://github.com/DasBasti/pySmartHashtag) `vehicle/battery.py`, [evcc](https://github.com/evcc-io/evcc) `vehicle/smart/hello/provider.go`, [ioBroker.smart-eq](https://github.com/TA2k/ioBroker.smart-eq).

### F.2.2 `statusOfChargerConnection` Values

Physical cable connection state (used by evcc for charge detection):

| Value | evcc Mapping | Meaning |
|-------|-------------|---------|
| 0 | Status A | No cable connected (disconnected) |
| 1 | Status B | Cable connected, not charging |
| 2 | Status C | Cable connected, actively charging |
| 3 | Status B | Cable connected, not charging |

Note: During DC fast charging (`chargerState=15`), `statusOfChargerConnection` is `1` (not `2`) because the AC connection sensor doesn't detect DC charging.

## F.3 Mapping to HA Entity

All fields are stored on a single entity: **`sensor.smart_battery`**

| API Field | HA Entity / Attribute | Transform |
|-----------|----------------------|-----------|
| `chargeLevel` | `sensor.smart_battery` (state) | `int(value)` — SOC % |
| `chargerState` | attr: `charger_state` | Mapped via `CHARGER_STATE_LABELS` to human-readable string |
| `chargeIAct` | attr: `charge_current_a` | `float(value)` — Amps |
| `timeToFullyCharged` | attr: `time_to_full_min` | `int(value)`, set to `null` if 2047 (N/A) |
| `distanceToEmptyOnBatteryOnly` | attr: `range_km` | `int(value)` — km |
| `statusOfChargerConnection` | — | Not stored (could complement `chargerState` for cable detection) |
| `chargeSts` | — | Not stored (low-level AC flag, redundant with `chargerState`) |
| `dcChargeSts` | — | Not stored |
| `dcChargeIAct` | — | Not stored (relevant only for DC fast charging) |
| `chargeUAct` | — | Not stored (battery-side voltage, not AC mains) |
| `batteryTemperature` | — | Not stored (potential future use for cold-weather charging limits) |
| `chargeMode` | — | Not stored |
| `chargePHV` | — | Not stored (always 0 for BEV) |
| `energyConsumed` | — | Not stored |
| `energyRegenerated` | — | Not stored |
| `distanceToEmptyOnBattery100Soc` | — | Not stored |

### Entity Attributes (full example)

```json
{
  "state": "85",
  "attributes": {
    "state_class": "measurement",
    "unit_of_measurement": "%",
    "device_class": "battery",
    "icon": "mdi:car-battery",
    "friendly_name": "Smart Battery",
    "attribution": "Data provided by Hello Smart API",
    "charger_state": "charging",
    "charge_current_a": 15.5,
    "time_to_full_min": 110,
    "range_km": 459
  }
}
```

## F.4 Other `vehicleStatus` Sections

Beyond `electricVehicleStatus`, the full API response contains:

| Section | Path | Key Fields |
|---------|------|------------|
| **Basic** | `vehicleStatus.basicVehicleStatus` | `engineStatus`, `position` (lat/lon/alt), `speed`, `direction`, `distanceToEmpty`, `usageMode` |
| **Maintenance** | `additionalVehicleStatus.maintenanceStatus` | `odometer`, `daysToService`, `distanceToService`, `mainBatteryStatus` (12V: voltage, SOC, health), tyre pressures & temps |
| **Climate** | `additionalVehicleStatus.climateStatus` | `preClimateActive`, `interiorTemp`, `exteriorTemp`, window positions, sunroof, seat heating/ventilation per seat |
| **Safety** | `additionalVehicleStatus.drivingSafetyStatus` | Door locks & positions, `centralLockingStatus`, `trunkLockStatus`/`OpenStatus`, `engineHoodOpenStatus`, `electricParkBrakeStatus`, seat belts, alarm |
| **Running** | `additionalVehicleStatus.runningStatus` | All exterior lights (hi/lo beam, fog, DRL, indicators), `tripMeter1`/`2`, `avgSpeed` |
| **Pollution** | `additionalVehicleStatus.pollutionStatus` | `interiorPM25`, `exteriorPM25Level`, `relHumSts` (humidity %) |
| **Driving** | `additionalVehicleStatus.drivingBehaviourStatus` | `gearAutoStatus`, `engineSpeed` |
| **HV Status** | `additionalVehicleStatus.chargeHvSts` | Top-level int (1 = HV system available) |

These sections are **not currently used** by the energy manager but are available for future features (e.g. pre-conditioning before cheap-tariff charging, GPS-based home detection).

## F.5 Poll Frequency

| Condition | Interval | Rationale |
|-----------|----------|-----------|
| Car charging (`charger_state` = charging) | 1 min | Track SOC progress for dashboard |
| Car just connected (wallbox `Preparing`) | Immediate | Show SOC on dashboard quickly |
| Baseline (idle / disconnected) | 60 min | Avoid unnecessary API calls |

See Section 4.3.7 for adaptive polling logic.

---

**End of Document**

*Version 2.48 - April 2026*

**Changelog:**
- v2.48: Operational polish for the EV control loop. The 10-s loop was spamming ~34k INFO lines/day (apscheduler heartbeats + EV safety line + EV decision line) and firing two InfluxDB queries every cycle purely for dashboard diagnostics. Fixes: (a) silenced `apscheduler.executors.default` and `apscheduler.scheduler` at WARNING — removes the per-cycle "Running job…" chatter and its UTC timestamps; (b) `EVBatteryOptimizer.check_ev_safe` log dropped to DEBUG; (c) short-circuit in `control_ev_charging` — when no EV candidate (surplus < threshold, mode not solar, or wallbox unavailable), skip `check_ev_safe`/`will_battery_hit_full` and read cached values instead (~17k Flux queries/day saved); (d) cache primed in `run_optimization` after the SOC forecast is written, so the dashboard always reflects a real value from the most recent 15-min cycle; (e) single dense INFO log per EV cycle (`EV [state] power  mode=… surplus=…W±threshold  batt=…% [min48h=…%±floor]  src=…`) with dedup — DEBUG on no-change, INFO on state/power/source change, 60-s INFO heartbeat while idle; (f) stray mislabeled `DEBUG:` messages at INFO demoted to actual DEBUG; (g) startup timestamps rendered in Swiss local time via `swiss_datetime()` throughout (`run.py`, `forecast_reader.py`, scheduler next-run line). No FSD-content changes — this is operational only. (v1.8.3 → v1.8.5)
- v2.47: Split the EV safety floor from `battery.reserve_percent`. Added **`ev_charging.reserve_percent`** (default **20 %**) — an independent config option for the 48-h EV safety rule. Rationale: `battery.reserve_percent` = 0 (site default) makes the old shared rule toothless because the simulator clamps SOC at 0, so `min_soc >= 0` is always true. The two floors now answer different questions and evolve independently. Updated Section 4.1.4 (Battery configuration), Section 4.3.6 (Safety rule + "Independence from nightly battery protection"), example YAML, and README. (v1.8.2)
- v2.46: Moved `will_battery_hit_full()` from the main runtime class into `EVBatteryOptimizer` (its only caller). Dropped the misnamed "Shared Forecast Helpers" section — it had no shared helpers left. Folded the `will_battery_hit_full` description into Section 4.3.6 as a dashboard-diagnostic subsection. Renumbered 4.6 → 4.5, 4.7 → 4.6, 4.8 → 4.7 for InfluxDB Storage / Dashboard / Error Handling. Also: `_extra_load_percent` remains a private helper on each consumer class (one line of `wh / capacity_wh × 100`) — not worth extracting. 2 new unit tests covering the moved method. (v1.8.1)
- v2.45: Restructured Chapter 4 into a consumer-oriented layout — each decision block (home battery, EV battery, washer) is now a self-contained section covering its forecasts and rules. New **Section 4.1 Prerequisites** consolidates all shared inputs (PV forecast, load forecast, live SOC, tariff schedule, battery configuration, time/unit conventions) previously scattered across the chapter. Former separate sections collapsed into one per consumer: 4.2 Home Battery (sim + discharge rule), 4.3 EV Battery (state machine + power calc + 48-h safety rule + car SOC polling + car SOC forecast — merges the previous duplicate "## 4.6" numbering bug), 4.4 Washer (Appliance Signal), 4.5 Shared Forecast Helpers. Renumbered downstream: 4.6 InfluxDB Storage, 4.7 Dashboard, 4.8 Error Handling. Documentation-only; no code changes.
- v2.44: Replaced the EV battery-protection gate with a self-correcting 48-h min-SOC rule (Section 4.3.6). Root cause: the old rule targeted `tariff.target`, which during daytime (06:00–21:00) resolved to **tomorrow's** 21:00 — giving the forecast a full extra sun-day of headroom, so `reaches_target` stayed true all day even as the actual battery never climbed past ~48%. New rule: EV is allowed only while the home-battery SOC forecast stays ≥ `battery.reserve_percent` at every point across the next 48 h, with one 15-min slot of the candidate EV load subtracted as worst case. Re-evaluated every 15 min — if the forecast drops below the floor, EV stops and the battery (now EV-free) rides the remaining forecast back up. New module `src/ev_battery.py` (`EVBatteryOptimizer.check_ev_safe`); deleted `check_battery_protection`, `will_battery_hit_minimum`, `get_forecast_soc_at_target`; removed `ev_charging.battery_protection_soc` config (no per-EV target needed). Sensor attrs: `reaches_target`/`battery_will_hit_min`/`battery_forecast_soc` → `ev_safe`/`battery_min_soc_forecast_48h`/`battery_min_soc_floor`. 8 new unit tests (`test_ev_battery.py`); `test_power_calculation.py` `battery_check_fn` signature collapsed from `(bool,bool)` to `bool` (v1.8.0)
- v2.43: Car SOC Forecast (new Sections 4.6.4, 4.6.5) — multi-day prediction of EV SOC written to `energy_balance.car_soc_percent` every 15 min. House battery modelled as buffer: surplus first refills the house, overflow × efficiency goes to the car. New `smart_car.capacity_kwh` and `smart_car.charge_efficiency` config. Last-known-value fallback for `sensor.smart_battery` via InfluxDB (Python side) and `sensor.smart_battery_last_known` trigger-based template sensor (HA side). Grafana BatteryForecast panel 4 "Cumulative Energy Balance (Wh)" replaced by "Car SOC Forecast". Amazon Fire dashboard updated to reference the cached template sensor (v1.7.6)
- v2.42: Added 2% hysteresis to discharge soc_ok threshold (Section 4.3.2) — once blocked, projected min SOC must reach 12% (not 10%) to re-allow; prevents oscillation where shrinking simulation window causes min_soc to wobble ~0.5% around threshold every 15 min, flip-flopping discharge limit between 0W/5000W all night; 3 new tests (Appendix D.1) (v1.6.97)
- v2.41: Both rules use surplus_power (PV − house load) as input (Section 4.6.6) — eliminates grid_export feedback loop; snap-up tries next amp step above surplus if battery protected; surplus smoothing 60s→30s, rate limit 60s→30s; removed stale power floor clamp; updated flowchart, scenarios, and snap description (v1.6.93–v1.6.96)
- v2.40: Added S0/C0/M0 wallbox-unavailable guards to all active state transitions (Section 4.6.5.2); `will_battery_hit_full()` now returns `full_time_local` HH:MM (Section 4.4.2); updated NO-01 test scope (Appendix D.7.1) (v1.6.92)
- v2.39: Added Section 2.13.11 (Calibration History) — documents 2026-03-20 parameter change boundary, retrofitted data in InfluxDB (pv_forecast_retrofitted), and retrofit limitations
- v2.38: Updated Section 2.6 PV System Configuration — calibrated Pdc0 values (E:445W, W:490W, S:425W) and inverter efficiency (0.98) from per-string actual vs clear-sky model on sunny days; these are model calibration parameters, not changes to physical hardware; added calibration note explaining methodology
- v2.37: Added Section 2.13 (Shading Correction) — clear-sky reference model using pvlib.clearsky.ineichen(), per-hour sunny detection (actual GHI / clearsky GHI > 0.85), per-string shading factor calculation, InfluxDB storage schema for accuracy evaluation; replaces previous weather-factor-based approach that suffered from circular dependency
- v2.36: Restructured EV Charging Power Calculation (Section 4.6.6) — two rules based on battery state: Rule 1 (Battery Full) captures grid export, Rule 2 (Solar Surplus Charging) uses surplus with battery protection gate; `snap_to_power_step(available_power_w)` as shared amp-step conversion; decision flowchart; fixed missing power computation when battery full + forecast path (v1.6.85)
- v2.35: Discrete M-Bus power steps (Section 4.6.6) — replaced nominal `amps × 230 × 3` power steps with M-Bus calibrated values from 2026-03-04 sweep; `snap_to_power_step()` replaces `snap_to_amp_step()`; energymanager works in real-world watts; OCPP server demand calibration converts M-Bus watts to integer amps via `round(W/637)` (v1.6.81, v0.9.47)
- v2.34: Added step-down loop for Rule 2 battery protection (Section 4.6.6) — when snapped candidate amp level fails battery checks, step down one amp at a time until checks pass or power drops below `ev_min_solar_power`; `will_battery_hit_full()` checked once outside loop; prevents all-or-nothing blocking when a lower amp level would be sustainable (v1.6.75)
- v2.33: Removed EVChargingStrategy class — EV Rule 2 now uses inline `snap_to_amp_step()` (every 10 s) instead of stale 15-min forecast strategy; removed `ev_forecasted_power_w` and `battery_protection_passed` fields from EVInputs; replaced `forecast_power_w` sensor attribute with `candidate_power_w`; removed `battery_protection` sensor attribute (redundant with `reaches_target`); deleted `ev_strategy.py`; updated observer EC-05/EC-06 detectors (v1.6.74)
- v2.33: Rate-limit wallbox power limit changes to 60s minimum interval (Section 4.6.6) — prevents oscillation at amp-step boundaries; 0W bypass for safety; ev_target_power still updates every 10s for dashboard
- v2.33: Appliance signal now uses battery protection logic — orange = no grid import needed until 21:00 (uses sim_no_strategy min SOC), red = would need grid import; charging mode preserved across add-on restarts (Section 4.6.4)
- v2.32: Restructured sections 4.4–4.6 — new Section 4.4 (Battery Forecast Functions) extracts `get_forecast_soc_at_target()`, `will_battery_hit_full()`, `will_battery_hit_minimum()` as reusable functions with `extra_load_wh` parameter; simplified Section 4.5 (Appliance Signal) to use Section 4.4 functions; renumbered EV Charging from 4.5 → 4.6; EV Rule 2 now uses three battery checks (`reaches_target`, `battery_will_be_full`, `battery_will_hit_min`); updated all cross-references
- v2.31: Solar Decision card shows rule-by-rule evaluation with actual numbers and pass/fail (Section 4.8.3) — each rule displayed with live sensor values, ✅/❌ per sub-check; added `threshold_w` and `reaches_target` sensor attributes (v1.6.61)
- v2.30: Solar Decision dashboard card (Section 4.8.3) — live EV charging decision inputs, reasoning, and source; replaced static markdown explanation
- v2.29: Appliance signal uses appliance-load simulation (Section 4.5.2.1) — subtracts appliance energy from SOC trajectory and checks min SOC ≥ reserve%; grid export is now contextual info, not a separate ORANGE path; renamed `final_soc_percent` → `min_soc_percent` attribute
- v2.28: Fix grid power sign convention in surplus capture formula (Section 1.9.1) — grid sensor uses positive=export, code was negating it; corrected sanity invariant (Section 1.9.2); skip peak-SOC override query in battery protection when past cheap_start (Section 4.6.6)
- v2.27: Surplus-based EV forecast strategy (Section 4.6.6) — forecast path now snaps current `sensor.surplus_power` to next wallbox amp step instead of bottom-up search from min to max; entry gate changed from `ev_forecasted_power_w >= threshold` to `surplus_power >= ev_min_solar_power` (live surplus must exceed configured minimum); battery protection check steps down from candidate amp level; updated Selection Rules table, Input Parameters, and Scenarios
- v2.26: Passive integration observer test revision (Appendix D.7) — replaced 5 obsolete surplus-tracking tests (NO-03, NO-04, EC-01, EC-10, EC-11) with forecast-strategy-aligned tests (NO-05, NO-13, EC-05, EC-06, EC-07); updated NO-02 preconditions for strategy-based entry; report version bumped to 3; evidence includes `strategy` field
- v2.25: Forecast-based EV solar charging strategy (Section 4.6.7) — replaces instantaneous open-loop/closed-loop excess with SOC simulation; battery acts as buffer for coarse amp steps (690W on 3-phase); bottom-up search from min to max amps; dynamic protection target adapts to bad days; `min_solar_power_w` config for early charging below wallbox minimum; `sensor.surplus_power` for entry decision; renamed `sensor.load_power` → `sensor.house_load_power`; added `sensor.total_load_power` (house + wallbox for Fire display)
- v2.24: Appendix F — Comprehensive Smart Car API reference: full `electricVehicleStatus` field catalogue with all 16 `chargerState` values (from pySmartHashtag/evcc/ioBroker), `statusOfChargerConnection` physical cable states, V2L fields, charging lids, DC charging fields; HA entity mapping table; other `vehicleStatus` sections (climate, doors, maintenance, GPS, 12V battery); poll frequency table
- v2.23: Wallbox idle detection exits all EV modes — added `wallbox_idle` input (Section 4.6.6); S1/C1/M1 transitions exit SOLAR/CHEAP/IMMEDIATE to IDLE when car finishes charging (wallbox idle ≥ 5 min); idle timer extended from immediate/cheap to all modes; dashboard shows `idle_minutes` and `wallbox_idle` attributes; EC-16 passive integration test; IT-BATT-04 test catalogue entry
- v2.22: Integration test catalogue (Appendix D.5/D.6) — 22 tests across 6 categories; 3 implemented (IT-PHASE-01, IT-BATT-01, IT-BATT-03), 19 documented as future; EV charging power tests documented (Appendix D.5)
- v2.21: Added wallbox status display mapping table for dashboard (Section 4.8.1) — documents how raw OCPP status is shown to the user
- v2.20: SOC poll on charging mode change — switching modes (e.g. solar → immediate) triggers immediate SOC refresh for dashboard accuracy (Section 4.6.1)
- v2.19: Adaptive Smart car SOC polling — 1-minute during charging, immediate on car connection, hourly baseline; cached Hello Smart client reduces API calls from 6 to 2 per poll (Section 4.6)
- v2.18: Removed battery protection gate from solar EV charging — solar mode always active when excess available; battery protection is now informational (dashboard only); removed S4 transition from SOLAR state; updated N3 condition (Section 4.6.6)
- v2.17: Two-flag battery discharge blocking — EV charging in immediate/cheap mode now independently blocks battery discharge (Section 4.3.2); prevents SUN2000 from draining battery to cover wallbox load via DTSU correction; 17 new tests (Appendix D.4)
- v2.16: Added sections 4.7 (InfluxDB Storage), 4.8 (Dashboard Examples), 4.9 (Error Handling and Notifications), Chapter 5 (Forecast Accuracy Tracking), Appendix E (EnergyManager Configuration). Updated EV config for 4-state machine (phase-based min power, phase_threshold_kwh). Updated EV decision table to include EV charging forecast dependency.
- v2.15: FSD improvements — signal conventions box; weekend battery guard as explicit policy (`battery_guard_on_weekends`); `allow_1p_auto` flag for 1φ vs 3φ minimum; `effective_min_power_w` derived threshold; S06 forecast contract (measurement, field, staleness, missing=conservative); `battery_guard_margin_pct` (2% safety margin); smoothing defined (rolling median, 4 samples); rate limiting formalized (`setpoint_min_interval_s`, `setpoint_max_step_w`, `setpoint_step_w`); import tolerance (`import_tolerance_w`, `import_tolerance_cycles`); auto-revert trigger refined (only when setpoint > 0 recently); mode reset write-back semantics (one-shot, retry, idempotency); fault required-signals-per-mode; hard fault test-setpoint procedure; anti-flap table; S02 renamed EV_UNAVAILABLE; state priority order; scenario-based test table; edge-case worked examples (H: stale SoC, I: tariff boundary, J: phase gap)
- v2.14: Redesigned EV state machine — states represent charging behavior, not device status (Section 4.6.6); 12 states in 3 groups (base/policy/PV-excess); debounce (S21), hysteresis (200W), cooldown (S24) prevent oscillation; soft/hard fault classification with recovery dwell and anti-flap; battery reserve guard with configurable scope policy; mode renamed to auto_pv_excess/immediate/deferred_tariff; auto-revert on EV finish
- v2.13: Refactored EV charging to transition-based state machine with hysteresis (Section 4.6.6); 200W dead band prevents PAUSED↔SOLAR oscillation; 111 unit tests
- v2.12: Moved all test cases to Appendix D with references from main chapters; dashboard button feedback (orange/green); car status card redesign
- v2.11: Appliance signal ORANGE now also triggers on grid export >= 1.5kWh before evening (Section 4.5.2.2)
- v2.10: Expensive hours check now excludes weekend/holiday days (Section 4.3.2, 4.3.3); fixes incorrect discharge blocking on Friday nights
- v2.9: Appliance signal uses min SOC instead of final SOC for ORANGE check (Section 4.5); ensures SOC never dips below threshold at any point in simulation
- v2.8: Dual SOC forecast scenarios (with/without strategy); forecast snapshot for accuracy tracking; updated InfluxDB storage schema (Section 4.7)
- v2.7: Comprehensive EV Charging Optimization specification (Section 4.6) - OCPP 1.6j, phase switching, goal mode
- v2.6: Simplified battery discharge algorithm - rolling 15-minute threshold check; added test cases (Section 4.3.6); appliance signal test cases (Section 4.5.5)
- v2.5: Added Home Assistant API access documentation (homeassistant_api: true, battery entity reading)
- v2.4: Added Chapter 5 - Forecast Accuracy Tracking (Accuracy #1: Battery Discharge Optimization)
