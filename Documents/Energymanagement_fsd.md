# Energy Management System
## Functional Specification Document (FSD)

**Project:** Intelligent energy management with PV, battery, EV, and tariffs
**Location:** Lausen (BL), Switzerland
**Version:** 2.3
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

| Add-on | Version | Purpose | Update Frequency |
|--------|---------|---------|------------------|
| **SwissSolarForecast** | 1.1.1 | PV power forecasting using MeteoSwiss ICON ensemble data | Every 15 min (calculator) |
| **LoadForecast** | 1.2.0 | Statistical load power forecasting | Every hour |
| **EnergyManager** | 1.4.2 | Battery/EV/appliance optimization signals | Every 15 min |

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

## 1.8 Home Assistant Entities

### 1.8.1 Power Measurements (W) - Real-time

**Solar Production:**

| Entity ID | Description | MPC Use |
|-----------|-------------|---------|
| `sensor.inverter_input_power` | DC input (both strings) | PV production |
| `sensor.inverter_pv_1_power` | String 1 power | Per-string monitoring |
| `sensor.inverter_pv_2_power` | String 2 power | Per-string monitoring |
| `sensor.inverter_active_power` | Huawei inverter AC output | Huawei only |
| `sensor.solar_pv_total_ac_power` | Total AC output (Huawei + Enphase) | **Primary PV input** |
| `sensor.enphase_energy_power` | Enphase microinverter power | Secondary PV |

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
| `sensor.load_power` | House consumption (calculated) | **Critical: Load input** |

**Note:** `sensor.load_power` is calculated by the Huawei Solar integration:
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

| Entity ID | Description | Unit | MPC Use |
|-----------|-------------|------|---------|
| `sensor.battery_state_of_capacity` | State of charge | % | **Critical: SOC for MPC** |
| `sensor.battery_bus_voltage` | Battery voltage | V | Health monitoring |

**Control (Outputs):**

| Entity ID | Description | Unit | MPC Use |
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
  Load = sensor.load_power (calculated) or sum of Shelly 3EM phases (measured)
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

## 1.9 Design Principles

1. **Deterministic Core Logic** - All numerical calculations produce identical results for identical inputs
2. **Probabilistic Uncertainty** - P10/P50/P90 percentiles quantify forecast uncertainty
3. **InfluxDB as Single Source of Truth** - All data stored as time series
4. **Rolling Horizon** - Decisions recalculated every 5-15 minutes
5. **Decoupled Components** - Each add-on operates independently with clear interfaces
6. **Power for Storage, Energy for Calculations** - Forecasts stored as Power (W), converted to Energy (Wh) only when needed

## 1.10 Data Units and Flow

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

## 1.11 Home Assistant Add-on Architecture

This section describes the canonical Home Assistant add-on configuration architecture used by all add-ons in this project.

### 1.11.1 Configuration Philosophy

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

### 1.11.2 Secrets (HA Configuration UI)

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

### 1.11.3 Non-Secrets (Public Add-on Config)

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

### 1.11.4 Templates and Defaults

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

### 1.11.5 Configuration Merge Order

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

### 1.11.6 Add-on Configuration Files Summary

| Add-on | Secrets (Config UI) | Non-Secrets (YAML) |
|--------|--------------------|--------------------|
| **EnergyManager** | `influxdb_token`, `telegram_bot_token`, `telegram_chat_id` | `/config/energymanager.yaml` |
| **SwissSolarForecast** | `influxdb_token`, `telegram_bot_token`, `telegram_chat_id` | `/config/swisssolarforecast.yaml` |
| **LoadForecast** | `influxdb_token` | `/config/loadforecast.yaml` |

### 1.11.7 User Workflow

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

### 1.11.8 Best Practices Summary

| Practice | Do | Don't |
|----------|----|----- |
| **Secrets** | Store in HA Configuration UI | Put in YAML files |
| **User config** | Let user edit via File Editor | Auto-modify user files |
| **Defaults** | Apply in code for missing keys | Require all keys in user config |
| **Updates** | Refresh `.example` file | Overwrite user config |
| **Logging** | Log "token loaded" (not the value) | Log secret values |

---

## 1.12 Complete Parameter Reference

### 1.12.1 EnergyManager Parameters

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
| `influxdb.soc_bucket` | HuaweiNew | Bucket with actual SOC data |
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

### 1.12.2 SwissSolarForecast Parameters

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

### 1.12.3 LoadForecast Parameters

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
| `load_sensor.entity_id` | sensor.load_power | HA entity ID for load power |
| `forecast.history_days` | 90 | Days of history for profile |
| `forecast.horizon_hours` | 48 | Forecast horizon (hours) |
| `schedule.cron` | 15 * * * * | Cron schedule for forecast runs |
| `log_level` | info | Logging level |

---

# Chapter 2: SwissSolarForecast Add-on

## 2.1 Overview

SwissSolarForecast generates probabilistic PV power forecasts using MeteoSwiss ICON ensemble weather data and the pvlib solar modeling library. It produces P10/P50/P90 percentile forecasts for each inverter and the total system.

| Property | Value |
|----------|-------|
| Name | SwissSolarForecast |
| Version | 1.1.1 |
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

## 2.7 Configuration

### Secrets (Configuration UI)

Enter in **Settings → Add-ons → SwissSolarForecast → Configuration**:
- `influxdb_token` (required)
- `telegram_bot_token` (optional)
- `telegram_chat_id` (optional)

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
| `run_time` | ISO string | When forecast was calculated (v1.1.1+) |

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

---

# Chapter 3: LoadForecast Add-on

## 3.1 Overview

LoadForecast generates statistical household load consumption forecasts using historical consumption patterns. It analyzes 90 days of historical data to build time-of-day profiles and produces P10/P50/P90 percentile forecasts.

| Property | Value |
|----------|-------|
| Name | LoadForecast |
| Version | 1.2.0 |
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

## 4.1 Inputs

The EnergyManager requires three inputs to calculate energy decisions:

### 4.1.1 PV Forecast (from InfluxDB)

```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r._field == "power_w_p50")
```

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | Conservative estimate (90% confidence) |
| `power_w_p50` | W | Most likely estimate (median) |
| `power_w_p90` | W | Optimistic estimate (10% confidence) |

### 4.1.2 Load Forecast (from InfluxDB)

```flux
from(bucket: "load_forecast")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "load_forecast")
  |> filter(fn: (r) => r._field == "energy_wh_p50")
```

| Field | Unit | Description |
|-------|------|-------------|
| `energy_wh_p10` | Wh | Energy per 15-min period (low estimate) |
| `energy_wh_p50` | Wh | Energy per 15-min period (most likely) |
| `energy_wh_p90` | Wh | Energy per 15-min period (high estimate) |

### 4.1.3 Current SOC (from Home Assistant)

```
sensor.battery_state_of_capacity → soc_percent (0-100%)
```

The current SOC is **always read live** at the start of each simulation cycle. This is critical because the starting SOC shifts the entire forecast trajectory up or down.

### 4.1.4 Tariff Schedule

| Period | Weekdays | Weekends/Holidays |
|--------|----------|-------------------|
| **Cheap** | 21:00 - 06:00 | All day |
| **Expensive** | 06:00 - 21:00 | - |

Holidays: Read from calendar integration (future: HA calendar entity).

**Why 48h forecast horizon:** The MPC must always see until tomorrow's 21:00 cheap tariff start. Worst case: at 06:00 (expensive tariff starts), we need to see until 21:00 the next day = 39 hours. The 48h horizon provides buffer for forecast update delays and ensures visibility across a full expensive→cheap→expensive cycle.

---

## 4.2 SOC Simulation

The SOC simulation predicts battery state over the forecast horizon. This is the base curve for all energy management decisions.

### 4.2.1 Basic Loop (net = PV - Load → battery flow)

```
FOR each 15-minute timestep from NOW to target (48h):

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

### 4.2.2 Efficiency (battery flow → SOC change)

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

### 4.2.3 Output: SOC Forecast Curve (store into InfluxDB)

The simulation writes only the SOC trajectory to InfluxDB (PV/Load already in input buckets):

**Measurement:** `soc_forecast`

| Field | Unit | Description |
|-------|------|-------------|
| `soc_percent` | % | Forecasted SOC at each 15-min timestep |

```flux
from(bucket: "energy_manager")
  |> range(start: now(), stop: 48h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")
```

---

## 4.3 Battery Discharge Optimization

### 4.3.1 Problem

During cheap tariff (night), we want to preserve battery energy for the expensive period (day). Goal: SOC = 0% at next 21:00.

### 4.3.2 Algorithm

```
Every 15 minutes:

1. RUN SOC SIMULATION (§4.2) until next 21:00
   → Get soc_at_target (can be negative = deficit)

2. CALCULATE DEFICIT
   IF soc_at_target >= 0%:
      → Battery ON, no blocking needed
   ELSE:
      deficit_wh = |soc_at_target|/100 × capacity

3. FIND SWITCH-ON TIME (only during cheap tariff)
   saved_wh = 0
   FOR each 15-min period during cheap tariff:
       IF net_wh < 0:
           saved_wh += |net_wh| ÷ discharge_efficiency
       IF saved_wh >= deficit_wh:
           switch_on_time = current_period
           BREAK

4. DETERMINE DISCHARGE STATE
   IF expensive tariff (06:00-21:00):
      → Battery always ON
   ELSE IF cheap tariff AND now < switch_on_time:
      → Battery OFF (block discharge)
   ELSE:
      → Battery ON

5. RUN SOC SIMULATION WITH BLOCKING
   → Simulate with block_discharge_from=cheap_start, block_discharge_until=switch_on_time
   → Store both curves (with/without blocking) for dashboard visualization
```

### 4.3.3 Blocking Mode in SocSimulator

The SocSimulator accepts blocking parameters to simulate the effect of discharge blocking:

```
Parameters:
  block_discharge_from: datetime
  block_discharge_until: datetime

IF battery_flow < 0 AND time >= block_from AND time < block_until:
   battery_flow = 0  (discharge blocked, load served by grid)
   soc_wh unchanged
```

### 4.3.4 Output: number.battery_maximum_discharging_power

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

---

## 4.4 Appliance Signal

### 4.4.1 Problem

High-power appliances (washing machine 2.5 kW) should run when there's sufficient solar surplus.

### 4.4.2 Algorithm

```
Every 15 minutes:

1. GREEN: Current excess > 2500W
   → Run now with pure solar

2. ORANGE: Run SOC simulation
   IF soc_at_target >= appliance_consumption (1500 Wh):
   → Safe to run, battery will recover

3. RED: Otherwise
   → Would require grid import
```

### 4.4.3 Output: sensor.appliance_signal

| State | Meaning |
|-------|---------|
| `green` | Pure solar available now |
| `orange` | Safe to run, will recover |
| `red` | Insufficient surplus |

---

## 4.5 EV Charging Signal

> **Status: NOT YET IMPLEMENTED** - This section describes planned functionality.

### 4.5.1 Problem

EV charging requires minimum 4.1 kW. Should only charge with excess PV via EVCC.

### 4.5.2 Algorithm

```
excess_w = pv_power - load_power

IF excess_w >= 4100:
   charging_power = min(excess_w, 11000)
ELSE:
   charging_power = 0
```

### 4.5.3 Output: sensor.ev_charging_power

| Value | Meaning |
|-------|---------|
| `0` | No charging |
| `4100-11000` | Charging power for EVCC (W) |

---

## 4.6 Configuration

See **Section 1.10** for the full configuration architecture.

### Secrets (Configuration UI)

Enter in **Settings → Add-ons → EnergyManager → Configuration**:
- `influxdb_token` (required)
- `telegram_bot_token` (optional - for error alerts)
- `telegram_chat_id` (optional - for error alerts)

### Non-Secrets (`/config/energymanager.yaml`)

Editable via File Editor at `/addon_configs/energymanager/energymanager.yaml`:

```yaml
# NOTE: Token is configured in the Configuration tab, not here!
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

ev_charging:
  min_power_w: 4100
  max_power_w: 11000

schedule:
  update_interval_minutes: 15

log_level: "info"
```

---

## 4.7 InfluxDB Storage

**Bucket:** `energy_manager`

**Measurements:**

| Measurement | Purpose | Fields |
|-------------|---------|--------|
| `soc_forecast` | SOC trajectory from §4.2 | `soc_percent` |
| `soc_comparison` | With/without strategy curves | `soc_percent` (tag: `scenario`) |
| `discharge_decision` | Battery control decisions | `allowed`, `reason`, `deficit_wh`, `saved_wh`, `current_soc`, `switch_on_time` |
| `appliance_signal` | Appliance signal output | `signal`, `reason`, `excess_power_w`, `forecast_surplus_wh` |

**Query examples:**

```flux
# SOC forecast curve
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 48h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")

# Compare with/without strategy
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 48h)
  |> filter(fn: (r) => r._measurement == "soc_comparison")
  |> filter(fn: (r) => r.scenario == "no_strategy" or r.scenario == "with_strategy")
```

---

## 4.8 Dashboard Examples

### Kitchen Dashboard (Mushroom Cards)

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
    primary: Auto
    secondary: "{{ states('sensor.ev_charging_power') | int }} W"
    icon: mdi:car-electric
    icon_color: >
      {{ 'green' if states('sensor.ev_charging_power') | int > 0 else 'grey' }}

  # Battery
  - type: custom:mushroom-template-card
    primary: Batterie
    secondary: "{{ states('sensor.battery_state_of_capacity') }}%"
    icon: mdi:battery
    icon_color: >
      {{ 'green' if is_state('binary_sensor.battery_discharge_allowed', 'on') else 'orange' }}
```

---

## 4.9 Error Handling and Notifications

### 4.9.1 Battery Control Retry Logic

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

### 4.9.2 Telegram Notifications

If all retry attempts fail, a Telegram notification is sent to alert the user.

**Configuration (in user YAML):**
```yaml
telegram:
  bot_token: "your-bot-token-from-botfather"
  chat_id: "your-chat-id"
```

**Notification Content:**
```
Error: Battery Control Failed

Failed to [enable/block] battery discharge after 5 attempts.

Entity: number.battery_maximum_discharging_power
Target value: [0/5000]W
Error: [error details]

The battery may not be in the expected state!
```

### 4.9.3 Error Flow

```
control_battery(discharge_allowed)
    │
    ├── Attempt 1 → Fail → Wait 2s
    ├── Attempt 2 → Fail → Wait 2s
    ├── Attempt 3 → Fail → Wait 2s
    ├── Attempt 4 → Fail → Wait 2s
    └── Attempt 5 → Fail
           │
           ▼
    Send Telegram Error Notification
    Log error
    (last_discharge_allowed unchanged - will retry next cycle)
```

### 4.9.4 Underlying Communication Chain

The battery control command passes through multiple layers:

```
EnergyManager → HA REST API → Huawei Solar Integration → Modbus TCP → Inverter
```

Each layer has its own error handling:
- **HA REST API**: 30s timeout, 5 retries (our code)
- **Huawei Solar Integration**: Login verification, permission handling
- **huawei-solar-lib**: 10s timeout, 3 retries with exponential backoff
- **Modbus TCP**: pymodbus connection handling

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

**Prevention (v1.1.1+):**

Starting with v1.1.1, all add-ons use `run_time` as a field instead of a tag. This allows points to overwrite on the same timestamp without needing delete operations. The delete API calls have been removed from the code.

**Technical Background:**

InfluxDB 2.x points are uniquely identified by: `measurement + tags + timestamp`

- If `run_time` is a **tag**: Each forecast run creates NEW points (duplicates accumulate)
- If `run_time` is a **field**: Points OVERWRITE on same timestamp+tags (no duplicates)

The delete API in InfluxDB 2.x can be slow with large datasets and may cause goroutine deadlocks under certain conditions.

---

**End of Document**

*Version 2.3 - January 2026*
