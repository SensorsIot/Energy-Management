# EnergyManager Add-on — Functional Specification

EnergyManager is the optimization brain of the Energy-Management suite: it consumes the PV and load
forecasts plus live Home Assistant state and tariff windows, and controls the home battery, EV
wallbox, and high-power appliances. This is the complete, self-contained FSD for the add-on.

For the suite-level overview — the four add-ons, the architecture diagram, the data flow, and the
InfluxDB bucket map — see the [repository README](../../README.md).

## System context

EnergyManager is one of four independent Home Assistant add-ons; they cooperate only through
InfluxDB buckets and Home Assistant entities.

| Add-on | Role | Produces | Consumes |
|--------|------|----------|----------|
| SwissSolarForecast | PV production forecast | `pv_forecast` bucket | HA battery SOC (context) |
| LoadForecast | household load forecast | `load_forecast` bucket | HA load history |
| **EnergyManager** (this add-on) | battery / EV / appliance optimization | HA control entities, `energy_manager` bucket | `pv_forecast` + `load_forecast` |
| OCPP Server | OCPP 1.6j wallbox bridge | wallbox HA entities | EnergyManager power setpoint |

**Interfaces:** consumes the `pv_forecast` (SwissSolarForecast) and `load_forecast` (LoadForecast)
InfluxDB buckets and live HA state; produces HA control entities (home-battery discharge power,
wallbox power setpoint, appliance signal) and the `energy_manager` bucket. The wallbox link is
bridged by the OCPP Server add-on, which turns the setpoint into OCPP commands.

# Chapter 1: Installation, Entities & Configuration

> The suite overview (purpose, architecture, data flow, bucket map) is in the
> [README](../../README.md); the forecast add-ons and OCPP Server have their own FSDs. The sections
> below retain the master-spec numbering, so they start at 1.7 (§§1.1–1.6 are the suite overview).

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

**Modbus Proxy Power Correction:** The wallbox is wired between the grid and the DTSU meter, so the SUN2000 doesn't see wallbox consumption. Without correction, the SUN2000 would see grid export when the wallbox is actually importing from the grid. The ESP32 Modbus Proxy sits on the RS485 bus between the DTSU and SUN2000, intercepts meter responses, and adds the wallbox power: `corrected = dtsu_power + wallbox_power`. The wallbox power arrives via MQTT (topic `wallbox`) published by the OCPP Server add-on every 10 seconds. The Modbus Proxy itself is a separate ESP32 component (no in-repo FSD).

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
| `sensor.battery_charge_discharge_power` | Charge/discharge power (+ charge / − discharge) | **Battery flow** |

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
  Battery = sensor.battery_charge_discharge_power (+ = charge, - = discharge)
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
    entity: sensor.house_load_power
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
| Battery power | `sensor.battery_charge_discharge_power` | Charge (from PV/grid) | Discharge (to house) | −5 000 … +5 000 W |
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

The following invariants should hold under normal operation. Runtime sanity checks in `energy-manager/src/sanity.py` validate these and log warnings on violation (but never block control):

1. **PV ≥ 0, Load ≥ 0, Wallbox ≥ 0** — always; negative values indicate sensor fault
2. **At midday with PV > 2 000 W and no wallbox load** — grid should typically be positive (exporting)
3. **|grid| should not exceed ~15 000 W** — above this suggests sensor fault (PV peak + battery max ≈ 17 kW)

## 1.10 Design Principles

Project-wide design principles are HOW — see the Harness:
[`Harness/project/design-principles.md`](../../Harness/project/design-principles.md).

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

The Home Assistant add-on configuration architecture (secrets vs non-secrets, templates, merge
order, best practices) is shared by all add-ons and is HOW — see the Harness:
[`Harness/project/addon-architecture.md`](../../Harness/project/addon-architecture.md). The operator
setup/update workflow is in [`Handbook.md` → Installation](../../Handbook.md#installation).

---

## 1.13 Complete Parameter Reference

### 1.13.1 EnergyManager Parameters

**Secrets (Configuration UI):**

| Parameter | Schema Type | Description |
|-----------|-------------|-------------|
| `influxdb_token` | `password` | InfluxDB API token |
| `telegram_bot_token` | `password?` | Telegram bot token (optional) |
| `telegram_chat_id` | `str?` | Telegram chat ID (optional) |

**Non-Secrets (`/config/energy-manager.yaml`):**

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
| _(holidays)_ | computed | The 8 EBL cheap holidays computed in-add-on (Section 4.1.3); no config key |
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

# Chapter 4: EnergyManager Add-on

## 4.0 Optimization Overview

EnergyManager runs a set of **independent optimizations, each owning one
controllable resource (entity)**. They share the same forecast/state inputs
(Section 4.1) but make separate decisions. This section is the map: *what*
is optimized, *for which entity*, on *what criteria*, with *what output*.

### Controlled entities and their optimizations

| Entity | Optimization | Goal | Decides | Control output | Cadence | Section |
|--------|--------------|------|---------|----------------|---------|---------|
| **Home battery** | Discharge blocking (battery protection) | Keep enough SOC to cover the expensive tariff window (and the EV) instead of dumping it early | Allow / block discharge | `number.battery_maximum_discharging_power` (`max`/`0`) | 15 min | 4.2.2 |
| **Home battery** | Export-peak-shaving charge control | Defer PV charging so the battery's headroom absorbs the midday **export** peak at a gentle, capped rate (less clipping, longer battery life) | Allow / defer charging | `number.battery_maximum_charging_power` (`charge_shaving_power_w`/`0`) | 15 min | 4.2.3 |
| **Home battery** | Dynamic charge target (longevity) | Charge only to the SOC needed to survive the next days (worst-case PV), not 100% — less LFP dwell at high SOC; full charge for BMS calibration 7 days after the last >= 99% (rolling) | Cap at target SOC | `number.battery_end_of_charge_soc` (= target, hard cap) + `number.battery_maximum_charging_power` (`0` at/above target) | 15 min | 4.2.4 |
| **EV (car)** | Solar-surplus charging | Maximize solar self-consumption into the car without draining the home battery | Wallbox charge power (amp step) | `number.wallbox_power_limit` (via REST `set_sensor_state`) | 10 s | 4.3.6-4.3.7 |
| **EV (car)** | Cheap / immediate charging (manual modes) | Reach the user's target SOC by a kWh budget + SOC stop | Wallbox power + discharge block | `number.wallbox_power_limit` & `_discharge_blocked_by_ev` | 10 s | 4.3.4–4.3.6 |
| **Appliance (washer)** | Run-now signal | Advise when a high-power appliance can run on solar without forcing grid import | green / orange / red | `sensor.appliance_signal` (**advisory — no actuation**) | 15 min | 4.4 |

### Execution order (decision DAG, Directed Acyclic Graph)

The home battery runs **first**: its SOC forecast (4.2.1) is the shared
input the others read. Order and interactions:

```
1. Home battery   → SOC simulation (4.2.1)
                  → discharge blocking (4.2.2)
                  → charge shaving (4.2.3)
2. EV (car)       → uses the home-battery SOC forecast for strategy and step-up decisions (4.3.6)
3. Appliance      → reads the same SOC simulation to grade the signal (4.4)
```

Key cross-entity interactions (criteria that make one optimization yield to
another):

- **The 48-hour SOC floor is not a may-charge veto** — it constrains only forecast-based upward
  power steps. Solar charging still yields when the live forecast says the home battery cannot
  reach its computed daily target (4.3.6–4.3.7).
- **Charge shaving yields to the EV** — when the car is connected and not
  full, charge shaving releases the battery charge limit so the EV owns the
  surplus (use case A, 4.2.3).
- **EV manual charging blocks home-battery discharge** — immediate/cheap
  modes set `_discharge_blocked_by_ev` so the house battery is not drained
  into the car (4.2.2 truth table).
- **Washer is advisory only** — it never actuates; it informs the user (or
  an HA automation) whether to start the appliance (4.4).

### Topics and priority tiers

The optimizations are grouped into three topic groups, by the resource they manage:

| Group | Topics |
|-------|--------|
| **EV Charging** | T1 — EV Charge Decision (4.3.6); T2 — EV Charge Power (4.3.7) |
| **Battery Management** | T3 — Charge Ceiling / Longevity (4.2.4); T4 — Discharge Strategy and Protection (4.2.2); T5 — Export-Peak-Shaving Charge Control (4.2.3) |
| **Appliances** | T6 — Appliance Signal (4.4) |

Priority between topics matters only when two of them act on the same control entity at the same time. Organized by tier:

**Tier 0 — Invariant (not a topic).** The no-buy floor: the home battery stays `>= battery.no_buy_floor_percent`. It underlies the T2 Rule 3 step-up gate (instantaneous SOC) and T3's charge target (sized so the 48 h worst case stays above it), and is never overridden.

**Tier 1 — Independent topics.** Each owns a distinct control entity (or is advisory), so they never contend and always execute, in any order:

| Topic | Control entity | Action |
|-------|----------------|--------|
| **T3** — Charge Ceiling | `number.battery_end_of_charge_soc` + `number.battery_maximum_charging_power` | SOC ceiling = `battery_target_soc` (hard cap); charge power 0 when SOC >= target |
| **T4** — Discharge | `number.battery_maximum_discharging_power` | block discharge during cheap hours when it cuts expensive-hours import |
| **T6** — Appliance Signal | `sensor.appliance_signal` | advisory only |

**Tier 2 — Battery charge-timing vs EV (mutually exclusive, day mode).** EV charging and shaving both want the PV surplus, so the choice is made **once per day** — a snapshot of the car at a fixed local hour (`shaving_decision_hour`, default 08:00), latched until the next midnight (FSD 4.2.3):

- **Car day** — EV below target (or SOC/target unknown) at the decision hour: the EV owns the surplus, the home battery charges greedily, **T5 does not run**.
- **Shaving day** — EV at/above target (full) at the decision hour: **T5 runs**, holding the battery's headroom to shave the midday export peak. Fullness alone decides — the car need not be plugged in.

**Departure trigger:** a shaving day downgrades to a car day (one-way) the moment the EV drops below target ("below full") — see §4.2.3. The mode does **not** flip *into* shaving if the car merely refills later in the afternoon (the peak is past by then). When it is a shaving day, T5 still applies its own B0 abundant-day gate per cycle.

### Control vs. advisory

| Type | Optimizations | Effect |
|------|---------------|--------|
| **Actuating** (writes an HA control entity) | battery discharge, battery charge shaving, all EV charging | Directly changes battery/wallbox behaviour |
| **Advisory** (publishes a state for the user/automations) | washer signal | No direct actuation |

---

## 4.1 Prerequisites

Everything the decision logic (Sections 4.2–4.5) reads from. All consumers share these inputs; consumer-specific transforms happen inside each consumer's section.

### 4.1.1 Forecast inputs (InfluxDB)

| Source bucket | Measurement | Fields used | Unit |
|---|---|---|---|
| `pv_forecast` | `pv_forecast` (inverter=`total`, model=`hybrid`) | `power_w_p10/p50/p90` | W per 15 min |
| `load_forecast` | `load_forecast` | `energy_wh_p10/p50/p90` | Wh per 15 min |

**Why the 120 h (5-day) horizon:** Both forecasts cover 120 hours. This ensures the SOC simulation can look ahead to the next weekday's expensive hours even from a Friday evening (worst case: Fri 21:00 → Mon 21:00 = 72 hours). The extended horizon also enables 5-day Grafana visualisation of the energy balance and gives the EV step-up check enough headroom to inspect two full day/night cycles.

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

`BatteryOptimizer.get_tariff_periods(now)` returns `(cheap_start, cheap_end, target, is_cheap_now)`. `target` is used only by the home-battery discharge rule; EV and washer rules do not consult it. The exact cheap/expensive slot labeling, including the EBL holiday calendar, is defined in Section 4.2.2 (Topic 4) — its only consumer.

### 4.1.4 Battery configuration

**YAML `battery`:**

| Key | Default | Used by |
|---|---|---|
| `capacity_kwh` | 10.0 | Simulator and EV forecast conversions (Wh → SOC %) |
| `reserve_percent` | 10 | Discharge-sim floor / forecast-error buffer for the protection (Section 4.2.2); 0 = pure SOC=0 must-buy trigger |
| `charge_efficiency` | 0.95 | Simulator (charge branch) |
| `discharge_efficiency` | 0.95 | Simulator (discharge branch) |
| `soc_entity` | `sensor.battery_state_of_capacity` | Current SOC readback |
| `discharge_control_entity` | `number.battery_maximum_discharging_power` | Discharge control output |
| `charge_shaving_power_w` | `2500` | Charge power while shaving the export peak (Section 4.2.3) |
| `charge_control_entity` | `number.battery_maximum_charging_power` | Charge control output (Section 4.2.3) |
| `end_of_charge_soc_entity` | `number.battery_end_of_charge_soc` | SOC-ceiling output — the Topic 3 hard cap (Section 4.2.4) |
| `no_buy_floor_percent` | 20 | Shared floor for EV step-up and battery charge-target protection (Sections 4.2.4, 4.3.7) |

The former `ev_charging.reserve_percent` key is accepted as a compatibility fallback for
`battery.no_buy_floor_percent`; new configurations should use the battery key.

### 4.1.5 Time and unit conventions

- **Internal time**: UTC everywhere. Tariff comparisons convert to `Europe/Zurich` locally; display strings also use Swiss time.
- **Time slot**: 15 minutes. All forecasts, simulations, and the optimization cycle align to `:00/:15/:30/:45`.
- **Energy**: Wh. **Power**: W. **SOC**: % (0–100), converted from Wh via `capacity_wh`.
- **Cycle cadence**: battery optimization every 15 min; EV charging control every 10 s.

---

## 4.2 Home Battery

The home battery is the central buffer of the system. It runs **first** in the decision DAG: its SOC forecast feeds the EV strategy (4.3) and washer signal (4.4). Two concerns live here: producing the forecast (simulation) and deciding whether to allow discharge (discharge rule).

### 4.2.1 SOC Simulation

The SOC simulation predicts battery state over the forecast horizon. This is the base curve for all energy management decisions. Runs every 15 min via `BatteryOptimizer.simulate_soc()`, producing three trajectories written to InfluxDB with tag `scenario`:

| Scenario | Description | Used by |
|---|---|---|
| `battery_on` | Free discharge — the implication of allowing discharge every deficit | Grafana, washer (4.4) |
| `battery_off` | Discharge held during cheap hours per the 4.2.2 rule — the implication of holding | Grafana |
| `planned` | Whichever of the two the 4.2.2 decision selects each cycle — the trajectory that actually occurs | EV strategy inputs and Grafana (4.3) |

`battery_on` and `battery_off` are the two candidate options the decision chooses between; `planned` is the chosen one. `simulate_soc(soc_percent, forecast, block_from, block_until)` supports optional discharge-block windows; `calculate_decision()` uses this to generate all three curves.

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

### 4.2.2 Discharge Strategy and Protection Signal (Topic 4)

Decides whether the home battery may discharge. Acts on `number.battery_maximum_discharging_power`. Re-evaluated every 15 min over a 48 h horizon.

#### Principle

Only **expensive**-tariff purchases cost real money; cheap-hour purchases are acceptable. Discharge the battery unless doing so increases expensive-hours grid import.

#### Cheap/expensive labeling (EBL calendar)

EBL (Elektra Baselland) double-tariff. A 15-min slot is **cheap (Niedertarif)** if it is a weekday 21:00-06:00 window, a Saturday/Sunday, or one of the 8 holidays below; otherwise **expensive (Hochtarif** -- weekday 06:00-21:00).

**Cheap holidays -- exactly these 8**, computed in-add-on per year (4 fixed + 4 from Gregorian Easter `E`, via `dateutil.easter`):

| Holiday | Date | | Holiday | Date |
|---------|------|-|---------|------|
| Neujahr | 1 Jan | | Karfreitag | `E - 2` |
| 1. August | 1 Aug | | Ostermontag | `E + 1` |
| Weihnachten | 25 Dec | | Auffahrt | `E + 39` |
| Stephanstag | 26 Dec | | Pfingstmontag | `E + 50` |

This is the **complete** EBL low-tariff holiday list; other canton-BL holidays (Berchtoldstag 2 Jan, Tag der Arbeit 1 May, Fronleichnam, Maria Himmelfahrt, Allerheiligen) stay Hochtarif. Computed in the add-on -- no external list or calendar.

#### Process

1. **Simulate SOC twice** (`simulate_soc`, from current SOC, on `solar - house_load`):
   - **battery_on** -- discharge on every deficit (free discharge).
   - **battery_off** -- skip discharge during **cheap** slots (that load is bought from the grid); discharge normally during **expensive** slots.
2. **Label** every 15-min slot cheap/expensive (*Cheap/expensive labeling* above).
3. **Sum expensive-hours import** for each option: total unserved load **energy (Wh)** over all **expensive** slots where the battery is empty (SOC = 0) -- the energy bought at the high price.
4. **Compare** the two sums.
5. **Lower wins. On a tie, battery_on wins** (don't buy cheap energy or hold SOC for no expensive-hours benefit -- the Topic 3 longevity win). The winner is published as the `planned` trajectory.

#### Outputs

- **Discharge actuation:**
  - battery_on wins -> discharge allowed (5000 W).
  - battery_off wins -> discharge blocked (0 W) during cheap slots, allowed during expensive slots.
- **`expensive_import_wh`** = the winning sum: `== 0` means the battery covers every expensive hour without buying; `> 0` means an expensive purchase is unavoidable.

#### EV-charging override

Discharge is additionally blocked while the EV charges in immediate/cheap mode:

```
blocked_by_ev = charging_mode in {immediate, cheap} AND target_power > 0
discharge_allowed = (winning strategy allows this slot) AND NOT blocked_by_ev
```

When the wallbox draws power, the Modbus proxy raises the household load the inverter sees, which would otherwise discharge the home battery into the car. The control signal is sent only when the combined decision changes.

#### Safety margin

The "empty" trigger in the simulation is `battery.reserve_percent` (**default 10 %**) — the `floor_wh` the discharge sim never drains below. Raising it from 0 is a deliberate **forecast-error buffer**: with floor 0 the free-discharge sim can spend the battery's last few % to cover a small *forecast* morning deficit (so it ties and discharges overnight); with floor 10 that bottom slice is treated as unavailable, so free-discharge shows the morning as unserved → **hold wins → the battery is kept high overnight**, leaving a real ~10 % buffer for the expensive morning even when the load/PV forecast is optimistic (observed 2026-06-24: median load forecast ~280 W vs ~750 W actual → battery drained to ~1 % → expensive morning import; a 10 % floor would have held it). Set to 0 for pure SOC=0 must-buy economics.

#### Self-correction

Re-run every 15 min from the live SOC; binary strategy, no hysteresis -- the metric is a stable energy cost, not an SOC-vs-threshold comparison.

#### Output entity

`number.battery_maximum_discharging_power`: 5000 = discharge allowed, 0 = blocked.

#### Test Cases

Test files: `energy-manager/tests/test_battery_optimizer.py`, `energy-manager/tests/test_discharge_blocking.py`.

### 4.2.3 Export-Peak-Shaving Charge Control

This controls **when the home battery is allowed to charge from PV**. It is
separate from, and runs *after*, the discharge decision (4.2.2): discharge
controls `number.battery_maximum_discharging_power`, this controls
`number.battery_maximum_charging_power`. The two never conflict — a `0`
charge limit never blocks discharge, so house load is always covered.

> The home battery is **PV-only**; it is never grid-charged. Setting the
> charge limit to `0` therefore only ever defers PV charging, never grid
> import.

#### Problem

On a clear day the battery charges greedily at sunrise and reaches its
target well before solar noon. The midday production peak is then exported
to the grid — wasting the highest-power part of the day and pushing grid
export toward the feed-in limit (clipping). If instead the battery's
headroom is **held for the peak**, the surplus is absorbed into the battery
during the highest part of the day, **shaving the maximum of the export
curve**.

The charge is applied at a **reduced power** (`charge_shaving_power_w`,
default 2500 W — below `max_charge_w`) not the full inverter rate.
Two reasons: (1) a gentler C-rate is easier on the battery (less heat,
longer life); (2) absorbing at a lower power spreads the charge across more
15-min intervals, so the battery fills over a wider midday window instead of
in one short burst — a flatter, gentler feed-in profile. Surplus above the
shaving power in any interval is still exported (the cap is deliberate, not
a regulation failure).

#### Day mode — decided once per day

The shave-vs-charge choice is a **whole-day mode**, not a per-tick flip: it is
decided **once**, at a fixed local hour (`shaving_decision_hour`, default
08:00, Europe/Zurich), and latched until the next local midnight
(`_update_shaving_day_mode()`). The decision is a single snapshot of the car:

| Car at the decision hour | Day mode |
|--------------------------|----------|
| EV **full** — `smart_battery_last_known >= smart_charging_max_last_known` | **shaving day** |
| EV below target, or car SOC/target unavailable | **car day** |

> **Fullness is the sole criterion — connection is deliberately NOT checked.**
> The EV can come and go at any time, so only its charge level decides: a full
> car (parked here *or* away) will not need the surplus, whereas a car below
> target will (now, or on its return), so the battery should bank the morning
> surplus greedily. The last-known SOC (`smart_battery_last_known`) makes the
> test valid whether or not the car is plugged in.
>
> In particular the premise does **not** read `binary_sensor.car_ready` (a full
> car reports wallbox status `SuspendedEV`/`Finishing`, for which `car_ready` is
> "off" — it would read a full car as absent and never arm a shaving day) nor
> `binary_sensor.wallbox_connected` (the wallbox↔server WebSocket link, ≈always
> on). Only `smart_battery_last_known` vs `smart_charging_max_last_known`.

Before the decision hour the mode defaults to **car day**. The snapshot is
taken once (the first 15-min tick at/after the decision hour) and then held,
with **one one-way downgrade — the departure trigger** (`_car_departed()`): a
shaving day reverts to a **car day** the moment its premise breaks — the EV
**drops below target** ("below full",
`smart_battery_last_known < smart_charging_max_last_known`). It then
stays a car day for the rest of the day and never re-arms shaving (a later
refill does not restore it). Rationale: a car that was full at 08:00 but then
drives off and drains will return needing energy. Continuing to shave
**exports the morning surplus** (sold) and bets on refilling the battery from
the midday peak — but a returning depleted car then **competes for that same
peak surplus**, so whenever the combined demand (battery headroom + the car's
deficit) exceeds the post-morning surplus, the exported morning energy is lost
to the car for good. Charging greedily banks that surplus into the battery
while the car is away and nothing competes, so it is available on the car's
return (a real energy loss, not only a forecast bet). Because the departure
trigger reads the **last-known** SOC — which tracks the car down as it drives,
whether or not it is plugged in — it catches the drained car without any
connection check, and a brief unplug/replug never ends the day. A car with
**unknown** SOC/target is **not** treated as departed (the cached last-known
SOC is held, Home-Installation §7.7, so a stale read keeps the shaving day
rather than cancelling on missing data). The mode does **not** re-flip *into*
shaving if the car merely refills later in the afternoon (the midday peak is
past by then).

The two top-level use cases follow from the day mode (`_charge_gate_active()`
returns true only on a shaving day):

| # | Use case | Day mode | Behaviour | Charge limit |
|---|----------|----------|-----------|-------------|
| **A** | **EV owns the surplus** | car day | Charge **released** — the EV claims the surplus (now or on a later top-up); the battery charges greedily so a late charge never starves it. Shaving stays out of the way. | `max_charge_w` |
| **B** | **Export-peak shaving** | shaving day | Defer/allow charging per the water-fill below, so the battery's headroom absorbs the export peak at a gentle, capped rate. | `0` or `charge_shaving_power_w` |

The feature is **always on** — there is no enable/disable switch. The gate
logic itself guarantees charging is never left stuck off: use case A and the
B0 marginal-day gate both release to `max_charge_w`, and within use case B
the limit is only ever `0` (deferring) or `charge_shaving_power_w` (charging).

#### Use case B0 — marginal-day gate (run *before* the water-fill)

Shaving is only worthwhile on an **abundant** day — one with a real midday
export peak to clip. On a marginal shoulder-season day the battery fills
late or not at all, so deferring its headroom risks under-filling for little
benefit. Before running the water-fill, `control_battery_charge()` therefore
asks `_will_fill_today()`: **would the battery, charging greedily, reach ≥99%
SOC at some point today — even under a conservative, low-production forecast
(p10 PV vs p50 load)?**

- **No** (would not fill today under the pessimistic estimate) → **marginal
  day**: skip shaving entirely and **charge greedily at `max_charge_w`** to
  capture the scarce surplus. Sets `use case = B`, `action = charging`, reason
  `marginal day — battery not forecast to fill today (conservative p10 PV) …`.
- **Yes** → abundant day: fall through to the water-fill below.

**Why p10 PV.** The gate takes its safety margin from the **forecast
uncertainty band** rather than a tuning constant: requiring the fill under
**p10 PV** (production exceeded ~90 % of the time) means a marginal day cannot
trip shaving and then fail to fill. There is no clock or hour parameter.

The fill check comes from `BatteryOptimizer.simulate_soc()`, which charges
**greedily** (no deferral). The prediction is therefore independent of the
shaving decision, so this gate **cannot create a feedback loop** (deferring
never pushes the predicted fill later). A battery already full now counts as
"fills today" (the abundant case → water-fill, which releases at B2). An
empty/unavailable conservative forecast is treated as marginal (charge
greedily — never defer blindly).

#### Use case B — sub-cases (water-fill decision)

Reached only on an abundant day (B0 = yes). `should_charge_now()` decides
ON/OFF each tick. It is a
**water-fill**: take the highest-surplus 15-min intervals of the rest of
today until their *absorbed* energy fills the battery headroom; the surplus
of the lowest selected interval is the water level **L**. Each interval
absorbs at most `charge_shaving_power_w × 0.25 h` (the per-interval cap, 625
Wh at 2500 W); surplus above that is exported even while charging. The cap
is why more intervals are selected than at full power — a wider, gentler
band. Inputs each tick:

```
headroom_wh       = (100 − current_soc) / 100 × capacity_wh
remaining_surplus = net_energy_wh per 15-min for the rest of today (Europe/Zurich)
current_surplus   = net_energy_wh of the current interval
cap_wh            = charge_shaving_power_w × 0.25   # max absorbed per interval
absorbed(s)       = min(s, cap_wh)
```

| Sub-case | Criteria | Behaviour | Charge limit |
|----------|----------|-----------|-------------|
| B1 No surplus now | `current_surplus ≤ 0` | Nothing to defer → release | `charge_shaving_power_w` |
| B2 Battery full | `headroom_wh ≤ 0` | No benefit deferring → release | `charge_shaving_power_w` |
| B3 Cannot fill today | `Σ absorbed(remaining_surplus) ≤ headroom_wh` | Capped absorption can't fill the battery → charge ASAP (e.g. cloudy day) | `charge_shaving_power_w` |
| B4 **In the peak band** | `current_surplus ≥ L` | This interval is one of the top-surplus intervals → **charge** at the shaving power; surplus above the cap is exported | `charge_shaving_power_w` |
| B5 **Below the peak band** | `current_surplus < L` | Off-peak surplus → **defer**; surplus is exported now, headroom held for the peak | `0` |

Evaluated top-to-bottom; first matching row wins. (B1–B3 nominally
"release" but stay capped at the shaving power; in those cases the surplus
is ≤ 0, the battery is full, or the day is too poor to exceed 2500 W for
long, so the cap is not a practical limit — use case A is the only path that
releases to full `max_charge_w`.)

**Self-correcting & self-terminating:** `headroom_wh` is re-read from the
*actual* SOC each tick, so as the battery fills, `L` rises, the peak band
narrows, and charging stops once full (B2). No start-time or latch is
stored, so cloudy days (B3) and double-peak days are handled naturally.

> Capping per-interval absorption also fixes a latent over-optimism in the
> original water-fill, which assumed each interval could absorb its *whole*
> surplus — ignoring that the battery itself has a maximum charge power. The
> cap makes the headroom accounting physical, so the band is sized correctly
> and the battery reliably reaches its target SOC.

#### Worked example

Capacity 10 kWh, SOC 50% → headroom 5 kWh. This is an abundant day: charging
greedily it would still reach 100% today even on the conservative p10-PV
forecast, so the B0 gate passes and the water-fill runs. Rest-of-day forecast
surplus
(kWh/15 min): morning 0.3–0.6, midday peak 1.0–1.4, afternoon 0.4–0.7. At
2500 W each interval absorbs ≤ 0.625 kWh, so the water-fill picks the
**broader** midday band whose *capped* absorption sums to 5 kWh → `L`
settles lower (≈ 0.6) than it would at full power. Result: a wide midday
band (`≥ L`) **charges** at 2500 W (B4) — surplus above 0.625 kWh/interval
is exported — while the morning/afternoon tails (`< L`) **defer** (B5). The
export curve is clipped flat across the peak, the battery fills gently over
~2 h instead of one short burst, and it still reaches 100%.

#### Mechanism

In use case B energy-manager writes the limit `0` (defer) or
`charge_shaving_power_w` (charge gently); use case A releases to
`max_charge_w`. The inverter's native zero-export regulation modulates the
*actual* charge power up to that limit — so surplus below the limit is fully
absorbed (zero export), and surplus above it is exported. `_apply_charge_control()`
tracks the last-applied watts and writes only when the value changes (logged
as `Battery charge allowed/deferred`), so a use-case A→B transition (max →
shaving power) is still detected even though charging stays on.

#### Configuration

| Key | Default | Meaning |
|-----|---------|---------|
| `battery.charge_control_entity` | `number.battery_maximum_charging_power` | Control output (use case A & B) |
| `battery.charge_shaving_power_w` | `2500` | Charge limit while shaving the peak (use case B) — gentle C-rate |
| `battery.max_charge_w` | `5000` | Charge limit when use case A releases, or on a marginal day (B0) |
| `battery.charge_shaving_fill_margin` | `1.2` | B0 fill-margin: shave only if the day's surplus exceeds headroom by this factor |
| `battery.shaving_decision_hour` | `8` | Local hour (Europe/Zurich) at which the day mode (car day vs shaving day) is decided and latched |
| `battery.forecast_max_age_minutes` | `120` | Fail-safe: shave only if the PV forecast heartbeat is fresher than this |

The marginal-day gate (B0) gates on whether the battery fills today under the
conservative p10-PV forecast, with the p10/p50 uncertainty band as the safety
margin. Two additional safeguards make it robust to a bad forecast:

- **Fill-margin** (`charge_shaving_fill_margin`): the rest-of-today surplus
  must exceed the headroom by this factor (default 1.2 = 20 %), not merely
  reach it. A day that only just fills, or fills near sunset, has no real
  export peak to clip → it routes to greedy charging instead.
- **Stale-forecast fail-safe** (`forecast_max_age_minutes`): SwissSolar
  forecast writes a `forecast_heartbeat` only after publishing a forecast
  built from complete, fresh weather data (it keeps the last-good forecast when
  the weather download is partial/stale, validated via the fetcher's per-run
  `metadata.json` — `files_failed`/run-age, not the output curve). If the
  heartbeat is older than this, energy-manager treats the forecast as untrusted
  and charges greedily rather than shave on garbage. Garbage-in fails safe.

#### Test Cases

Test files: `energy-manager/tests/test_battery_optimizer.py`
(`TestShouldChargeNow`) — sub-cases B1–B5 and the per-interval absorption
cap; `energy-manager/tests/test_charge_gate.py` (`TestMarginalDayGate`) — the
B0 marginal-day gate (fills-today, never-fills, empty-forecast-is-marginal,
and end-to-end greedy-vs-shaving routing); `test_charge_gate.py`
(`TestDayModeDecision`) — the once-daily shave-vs-car-day latch and departure
trigger. **Fullness (`smart_battery_last_known >= smart_charging_max_last_known`)
is the sole criterion — connection is not checked**, so a full car arms a
shaving day whether or not it is plugged in
(`test_full_car_at_decision_is_shaving_day`); a car below target or with unknown
SOC is a car day (`test_car_below_target_at_decision_is_car_day`,
`test_car_full_soc_unknown_is_car_day`); the departure trigger fires only on the
SOC dropping below target (`test_departure_below_target_flips_to_car_day`) and
an unknown SOC is held, not a departure (`test_stale_car_soc_is_not_a_departure`).

---

### 4.2.4 Charge Ceiling -- Longevity (Topic 3)

Limits how high the home battery charges, sparing the LFP from high-SOC dwell. Enforced on two inverter outputs: the native end-of-charge SOC ceiling (`battery.end_of_charge_soc_entity`, `number.battery_end_of_charge_soc`) is the real-time hard cap, backed by the charge-power limit (`battery.charge_control_entity`, `number.battery_maximum_charging_power`). Re-evaluated every 15 min. Off by default (`charge_target_enabled`).

| # | Rule | Condition | Action |
|---|------|-----------|--------|
| **1** | **Charge to target, then hold** | current SOC >= `battery_target_soc` | set charge power to 0 -- hold (surplus exports; discharge unaffected) |
| **2** | **Calibration charge** | > `charge_target_full_interval_days` (7) since SOC last reached >= 99 % (rolling — restarts at each >= 99 %) | `battery_target_soc` = 100 % |
| **3** | **Fail-safe** | forecast missing / stale | `battery_target_soc` = 100 % |

**`battery_target_soc`** = the lowest SOC ceiling C in [`no_buy_floor_percent`, 100] such that simulating the next `charge_target_horizon_h` (48 h) at worst-case PV (p10) / load (p50), capped at C, keeps the home-battery minimum >= `no_buy_floor_percent`, plus `charge_target_margin`, then **floored to `charge_target_min`** (`BatteryOptimizer.compute_charge_target`). The minimum is taken only from the end of today's charging window onward — the **last interval today (searched back from local 23:59) where PV exceeds load**, i.e. the battery's daily peak / start of overnight discharge. This excludes today's transient pre-charge low SOC, so a battery currently below the floor (e.g. drained overnight) does not by itself force a 100 % target; the deficit-→100 % result reflects a genuine forward shortfall. If no surplus remains today, the anchor is now. The floor means the battery always charges to at least `charge_target_min` (default 90 %) even when the survival need is lower — LFP-safe and banking more headroom for the car/house instead of exporting it. The survival math itself still uses `no_buy_floor_percent`; the floor only raises the final target.

`battery_target_soc` is enforced **only** by the charge control. It is **not** threaded into the discharge/EV SOC forecast -- those read the natural charge-to-100 trajectory (the EV's wallbox-off question, Section 4.3.6). Because the target is sized on worst-case p10 PV, the held battery still stays >= `no_buy_floor_percent` (above the EV floor and the discharge reserve), so the natural forecast the other topics read remains safe.

The "last full" timestamp is read from SOC history (InfluxDB: most recent point with SOC >= 99) -- no persisted state.

**Enforcement.** Every 15 min the target is mirrored onto the inverter's native end-of-charge SOC register (`battery.end_of_charge_soc_entity`), which hard-stops charging at that SOC in real time — no 15-min control lag, and PV surplus cannot trickle the battery past it. The register accepts only 90-100 %, exactly the range of `battery_target_soc` (floored to `charge_target_min` >= 90), so it always fits; a 100 % target means "no cap". The charge-power limit (`0` at/above target, Rule 1) backs it and drives the dashboard `charge_action`. When `charge_target_enabled` is off EM does **not** own the SOC register — it leaves it untouched, and releases it to 100 % only when its own last-written ceiling is below 100 % (a cap left in place by the feature is cleared; a register EM never wrote is not clobbered).

#### Configuration

| Key | Default | Meaning |
|-----|---------|---------|
| `battery.charge_target_enabled` | `false` | Master switch (off pending live validation) |
| `battery.charge_target_margin` | `10` | Extra % of capacity above the worst-case need |
| `battery.charge_target_horizon_h` | `48` | Survival look-ahead |
| `battery.charge_target_full_interval_days` | `7` | Days after the last >= 99 % SOC to force a 100 % calibration charge |
| `battery.charge_target_min` | `90` | Floor on the target — always charge to at least this SOC, even when the survival need is lower |
| `battery.end_of_charge_soc_entity` | `number.battery_end_of_charge_soc` | Inverter's native end-of-charge SOC ceiling — the real-time hard cap |

Survival floor = `battery.no_buy_floor_percent` (shared, 20 %); target floor = `battery.charge_target_min` (90 %).

#### Test Cases

Test file: `energy-manager/tests/test_charge_target.py`.

---

## 4.3 EV Battery

EV charging runs **second** in the DAG: it uses the home-battery forecast (Section 4.2.1) for the daily-target permission check and the 48-hour step-up check. This section covers everything EV-specific: wallbox architecture, mode selection, state machine, power calculation, live car SOC polling, and the multi-day car SOC forecast.

### 4.3.1 Overview

EV charging optimization maximizes solar self-consumption while ensuring charging goals are met.

> **Wallbox infrastructure:** The OCPP server, phase switching, power-to-current conversion, and wallbox communication are documented in [ocpp-server-fsd.md](../../ocpp-server/Documents/ocpp-server-fsd.md).

**Key Features:**
- 4 modes: Off, solar, cheap tariff, immediate — user selects via dashboard
- Solar charging is the default mode
- State machine (Section 4.3.5) routes to correct mode
- EV charging power calculation (Section 4.3.6) determines solar charging power
- Solar permission is based on connection, EV demand, available surplus, and whether the home battery can still reach its daily target (Section 4.3.6)
- `input_number.ev_min_solar_power` gates both solar paths — minimum power to start charging
- Real-time charging power adjustment every 10 seconds

### 4.3.2 Architecture

> **Wallbox communication, phase switching, MQTT power correction, and the EnergyManager ↔ OCPP Server interface contract are documented in [ocpp-server-fsd.md](../../ocpp-server/Documents/ocpp-server-fsd.md).**

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

> **Phase switching hardware and power-to-current conversion are documented in [ocpp-server-fsd.md](../../ocpp-server/Documents/ocpp-server-fsd.md).**

| Mode | Voltage | Current | Power Range |
|------|---------|---------|-------------|
| **1-phase** | 230V | 6-16A | 1.4 - 3.7 kW |
| **3-phase** | 400V | 6-16A | 4.1 - 11.0 kW |

**Gap:** 3.7 - 4.1 kW is not achievable (hardware limitation). The OCPP server handles phase switching automatically based on the power setpoint.

### 4.3.4 Charging Mode Selection

The user selects one of three charging modes via the kitchen dashboard (Amazon Fire tablet). The mode is persistent — it stays selected until the user changes it. The `input_select` entity is preserved by HA across add-on restarts; the EnergyManager reads (not overwrites) the current mode on startup.

| Mode | `input_select` value | Dashboard Label | Description |
|------|---------------------|----------------|-------------|
| **Off** | `off` | Off | Charging disabled. No wallbox charging even when PV surplus is available. Sticky — never auto-reverts. |
| **Solar** | `solar` | *(default — button-card greyed)* | Follow PV surplus while the home battery can reach its daily target; the 48-hour floor governs step-up only |
| **Immediate** | `immediate` | Charge Now | Charge at `manual_power_w` regardless of tariff or surplus |
| **Cheap** | `cheap` | Cheap Charge | Charge at `manual_power_w` during cheap tariff, 0 W during expensive |

**Control entity:** Two custom button-cards on `lovelace-amazonfire/test` (Cheap Charge / Charge Now). Each `tap_action` calls `script.ev_toggle_manual_charge` with the mode value. The script branches:

- If `input_select.ev_charging_mode` already equals the pressed mode → revert to `solar` (Stop).
- Otherwise → call `script.ev_start_manual_charge`, which clamps `input_number.ev_target_soc` against `sensor.smart_charging_max_last_known` and sets `input_select.ev_charging_mode` to the pressed mode.

**Off** is a distinct hard-stop mode: while `input_select.ev_charging_mode` is `off`, the state machine holds the wallbox at 0 W from any state, ignoring PV surplus and tariff, and gives the home battery full control (as in IDLE). It is **sticky** — unlike Immediate/Cheap, it never auto-reverts to `solar`; it stays `off` until the user selects another mode. `solar` remains the default resting state that charges automatically on surplus; `off` is the way to suppress that. The target-SOC slider lives on `lovelace-amazonfire/energy-manager` inside the **Car** card; a server-side automation (`clamp_ev_target_soc_to_car_max`) snaps the slider back if the user drags it above the car's reported max.

**Target-already-met feedback:** if the user presses Charge Now / Cheap Charge while the car is already at/above `input_number.ev_target_soc`, the state machine bounces straight back to IDLE on entry (the SOC stop, Section 4.3.5) and the controller reverts the mode to `solar`. To avoid this looking like a no-op, that same-tick entry bounce raises a Home Assistant **persistent notification** (*"EV charge: target already reached — … Raise the EV target SOC to charge."*; fixed `notification_id`, so repeats replace). Reverts that follow an actual charge (budget/SOC reached after charging, idle timeout, unplug) do not notify.

### 4.3.5 EV Charging State Machine

The state machine routes the wallbox to the correct operating mode based on user selection. It does **not** compute charging power — that is done by the EV Charging Power Calculation (Section 4.3.6).

> **Wallbox infrastructure:** Faults, disconnects, phase switching, and amp conversion are handled by the OCPP server (see [ocpp-server-fsd.md](../../ocpp-server/Documents/ocpp-server-fsd.md)). The state machine only makes charging decisions.

#### States

| # | State | Description | Power output |
|---|-------|-------------|--------------|
| 0 | **OFF** | Charging disabled by user. SUN2000 has full control of the battery. Sticky (never auto-reverts). | `0` |
| 1 | **IDLE** | No EV charging. SUN2000 has full control of the battery. | `0` |
| 2 | **SOLAR** | Solar charging. Power from Section 4.3.6. | `ev_charging_power_w` |
| 3 | **CHEAP** | Cheap-tariff charging at user-set power. Battery discharge blocked. | `manual_power_w` when cheap, `0` when expensive |
| 4 | **IMMEDIATE** | Immediate charging at user-set power. Battery discharge blocked. | `manual_power_w` |

Initial state: **IDLE**

#### Transitions

The machine stays in its current state unless one of the listed conditions triggers a change. Conditions are evaluated in listed order — first match fires.

**OFF override (highest priority, evaluated before all per-state transitions):**

| # | Condition | → New State |
|---|-----------|-------------|
| X0 | `charging_mode == "off"` (from **any** state) | OFF (0 W; on entry the manual-charge budget is cleared) |
| X1 | In OFF **and** `charging_mode != "off"` | IDLE (normal transitions then resume this cycle) |

OFF is a hard, user-set stop: it is honoured from any state regardless of `wallbox_available`, surplus, or tariff, and it never triggers the `solar` auto-revert.

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
| C3 | Manual-charge SOC stop or kWh budget reached (Section 4.3.5.1) | IDLE |

Power toggles internally: `manual_power_w` when `is_cheap_tariff`, `0` when expensive. No state change on tariff toggle.

**IMMEDIATE** — *Stays in IMMEDIATE unless:*

| # | Condition | → New State |
|---|-----------|-------------|
| M0 | `NOT wallbox_available` | IDLE |
| M1 | `wallbox_idle` | IDLE |
| M2 | `charging_mode != "immediate"` | IDLE |
| M3 | Manual-charge SOC stop or kWh budget reached (Section 4.3.5.1) | IDLE |

#### 4.3.5.1 Manual-Charge SOC Stop and kWh Budget

The CHEAP and IMMEDIATE modes are manual charging sessions where the user specifies a **target SOC** via `input_number.ev_target_soc` (clamped at input time to `sensor.smart_charging_max_last_known`). Because `sensor.smart_battery` updates only every few minutes and can be stale, the state machine does not rely on car SOC alone — it tracks delivered energy via the wallbox session counter.

**On entry** to IMMEDIATE or CHEAP (from IDLE or SOLAR), the state machine snapshots:

| Field | Source |
|-------|--------|
| `start_soc` | `sensor.smart_battery_last_known` (may be `None` if unavailable) |
| `start_session_wh` | `sensor.wallbox_energy` (OCPP cumulative session energy) |
| `target_soc` | `input_number.ev_target_soc` |
| `capacity_kwh` | `smart_car.capacity_kwh` (config) |
| `efficiency` | `smart_car.charge_efficiency` (config, default **0.88**) |

**Each tick** while in CHEAP/IMMEDIATE:

1. **Session reset detection** — if `session_energy_wh` dropped below the last observed value (OCPP transaction restarted on unplug/replug), re-snapshot and continue.
2. **SOC stop** — if `car_soc ≥ target_soc`, exit to IDLE. Symmetric, no buffer: if we stopped too early because the kWh budget was off, the user re-presses the button and a fresh budget is computed from the new lower `start_soc`. If too late we've already overshot — no buffer would have helped.
3. **kWh budget** — compute `budget_wh = (target_soc − start_soc) / 100 × capacity_kwh × 1000 / efficiency`. If `delivered_wh = session_energy_wh − start_session_wh ≥ budget_wh`, exit to IDLE.

Freshness of `car_soc` (age of `sensor.smart_battery.last_updated`) is logged in the SOC-stop reason for diagnosis but does not affect the threshold.

**Fallback** — if `car_soc` is `None` at entry (smart-car API stale), the kWh budget is **not** enforced (no `start_soc` to anchor against). Charging stops only via the wallbox-idle path or the SOC stop once `car_soc` becomes available.

**Auto-revert** — when CHEAP/IMMEDIATE → IDLE for any reason while `input_select.ev_charging_mode` is still `cheap`/`immediate`, `run.py` sets the mode back to `solar` so the dashboard reflects the stop. **OFF is exempt** — it is a deliberate user choice, so it is never auto-reverted; it persists until the user picks another mode.

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
| `ev_charging_power_w` | EV Charge Power (Section 4.3.7) | SOLAR entry + power |
| `manual_power_w` | `input_number.ev_manual_power` | CHEAP/IMMEDIATE power |
| `target_soc` | `input_number.ev_target_soc` | CHEAP/IMMEDIATE budget |
| `car_soc` | `sensor.smart_battery_last_known` | CHEAP/IMMEDIATE SOC stop |
| `car_soc_age_s` | `now − sensor.smart_battery.last_updated` | Logging only |
| `session_energy_wh` | `sensor.wallbox_energy` | CHEAP/IMMEDIATE delivered kWh |
| `capacity_kwh` | `smart_car.capacity_kwh` (config) | CHEAP/IMMEDIATE budget |
| `efficiency` | `smart_car.charge_efficiency` (config, default 0.88) | CHEAP/IMMEDIATE budget |

**Output:**

| Output | HA Entity | Description |
|--------|-----------|-------------|
| `target_power_w` | `number.wallbox_power_limit` | Wallbox power setpoint (W). 0 = pause. |
| `state` | `sensor.ev_charge_status` | Current state for dashboard/logging |
| `reason` | Attribute on `sensor.ev_target_power` | Human-readable reason for current decision |

### 4.3.6 EV Charge Decision (Topic 1)

> **Question answered:** *may the wallbox charge at all?* — a power-independent yes/no, re-evaluated every 15 min. *How much* to charge is Topic 2 (Section 4.3.7).

#### The shared protection check -- `battery_min_soc_48h`

A single home-battery signal consumed by **Topic 2** (step-up gate) and **Topic 3** (charge target), plus the dashboard:

> `battery_min_soc_48h` = the **minimum** of the home-battery SOC simulation across the next **48 h**, starting from the current SOC, driven by **solar forecast - load forecast only** -- **the wallbox is excluded**.

The wallbox is excluded -- the load forecast is the Shelly-3EM house load, which already excludes the wallbox draw. Re-computed every 15 min from the live SOC. **Power-independent**: no EV load is subtracted and no charge-target cap is applied.

The floor it is compared against is **`battery.no_buy_floor_percent`** (default **20 %**), separate from `battery.reserve_percent` (the Section 4.2.2 discharge floor, default 10 %).

- Horizon: 48 h.
- No hysteresis -- the same bar for every consumer.
- **Not a Topic 1 gate.** It is not a may-charge veto; Topic 1 publishes it for the dashboard only. The may-charge decision is Rule 4 below.

#### Rules

The wallbox may charge **iff all four hold**; the first that fails stops it.

| # | Rule | Condition |
|---|------|-----------|
| **1** | **Mode & connection** | EV charging enabled **and** mode = `solar` **and** `binary_sensor.car_ready` = on |
| **2** | **Car needs charge** | `sensor.smart_battery` < `sensor.smart_charging_max_last_known` (car SOC below its target) |
| **3** | **Surplus present** | `(PV - house_load)` >= start threshold: `input_number.ev_min_solar_power` in 3φ, the **wallbox minimum (6 A ≈ 1380 W)** in 1φ (see note) |
| **4** | **Home battery reaches its target** | `reaches_target_today(live SOC, target)` — the SOC simulation, re-anchored to the **live** SOC, still peaks at/above the charge target (Section 4.2.4) today **OR** home battery already full (SOC = 100 %) |

**Notes**

- Rule 3's start threshold is **phase-aware** (`solar_start_threshold`): in **3φ** it is the manual `input_number.ev_min_solar_power`; in **1φ** that gate is **not honored** and the threshold is the wallbox minimum (6 A ≈ 1380 W). Single-phase power is inherently small (max 3680 W / 16 A), so `ev_min_solar_power` — sized for 3-phase, where the minimum step is already 3962 W — would strand most of the 1φ range and force charging only in the top band. The connected-phase count comes from `sensor.wallbox_phases` (ocpp-server §3.6.4.1); phases also select the Topic 2 step table (Section 4.3.7).
- Rule 4 gives the **home battery priority** over the car: the same `compute_charge_target` survival model (Section 4.2.4) drives both the battery's target *and* the car's permission. The reachability forecast is **car-excluded**, so it reads as *"if the car stops now and the battery gets all the surplus from here on, does it still reach the target today?"* When that turns false, the car yields all surplus to the battery. It is **self-correcting**: while the car charges it steals surplus, so each cycle the sim is re-anchored to a lower (car-suppressed) live SOC; the moment the battery cannot reach the target, the car stops, the battery then receives 100 % of the surplus and lands at (nearly) the target. Full-battery exception: at 100 % SOC the battery has already reached the target, so the check is skipped (the Rule-1 grid-export-capture path applies).
- **Evaluated on the live 10-s loop, re-anchored to the live SOC.** `reaches_target_today` re-runs `simulate_soc` from the current SOC over the cached net-energy forecast every EV cycle. The earlier implementation read the `soc_forecast` curve from InfluxDB — but that curve is regenerated only on the 15-min cycle and anchored to the SOC *at that cycle*, so while the car drained the battery the gate stayed optimistic and let the car run ~one forecast period too long (≈ `car_power × 15 min` of overshoot, e.g. observed 79 % vs a 90 % target on 2026-06-25). Re-anchoring to live SOC closes that gap to one 10-s step. The 15-min `soc_forecast` write to InfluxDB remains, now purely for the dashboard. (`will_battery_hit_full` still backs the dashboard `battery_full_time`/`battery_peak_soc` attributes on `sensor.battery_decision`, published on the 15-min cycle.)
- **Why no 48 h no-buy-floor veto?** A previous rule also blocked charging when `battery_min_soc_48h` fell below `no_buy_floor_percent`. Rule 4 supersedes it: charging *at or below* surplus never drains the battery (the remainder still charges it), the *only* draining step (Topic 2 step-up) is already gated by the instantaneous SOC floor (Section 4.3.7), and any multi-day trough is driven by future PV/load — not by the car spending *today's* surplus, which Rule 4 already protects. So the 48 h veto only ever produced false positives (when Rule 4 passed) or fired redundantly (when Rule 4 had already stopped the car).

### 4.3.7 EV Charge Power (Topic 2)

> **Question answered:** *how much?* -- runs only after Topic 1 (Section 4.3.6) returned yes. Output: the wallbox amp step. Re-evaluated on the 10-s live loop.

The EV can sit either side of the live surplus:

- a step **below** surplus -> the EV draws less than surplus -> the leftover **charges the home battery** (battery-positive, always safe);
- a step **above** surplus -> the EV draws more than surplus -> the gap is **pulled from the home battery** (battery-negative, needs permission).

#### Rules

| # | Rule | Condition | Result |
|---|------|-----------|--------|
| **1** | **Available steps** | phase config (1-phase / 3-phase) | the discrete amp ladder; the 3680-4140 W phase gap is a dead zone. *Plumbing -- not a decision.* |
| **2** | **Default: step at/below surplus** | always | the **highest step <= surplus** (`PV - house_load`). Remainder charges the home battery or is exported. Never pulls from the battery. |
| **3** | **Step up** | home battery **full** **OR** (`battery_min_soc_48h` >= `battery.no_buy_floor_percent` **AND** current SOC >= `battery.no_buy_floor_percent`) | use the **next step above surplus**; the home battery covers the small gap. |
| **4** | **No-gain suppression** | the **p10** forecast reaches **both** targets by end of today: home-battery peak SOC >= `battery_target_soc` **AND** car end-of-day SOC >= its car-side target | **veto Rule 3** — stay at/below surplus. |

Power = Rule 2's step, bumped one step by Rule 3 when allowed and Rule 4 does not veto. We never match surplus exactly -- Rule 2 always lands on a discrete step *under* surplus; Rule 3 optionally bumps to the one *over*.

**Notes**

- Rule 3 gates the **only** step that drains the home battery (one amp level above surplus). It requires `battery_min_soc_48h` >= `no_buy_floor_percent` **and** the **instantaneous** SOC >= `no_buy_floor_percent`. The 48 h forecast alone reads optimistically high while the car is actively draining the real battery (observed 2026-06-23: SOC 12 %, forecast 29 %, step-up still firing), so the instantaneous condition is what actually stops step-up from draining the battery below the floor. Steps *at or below* surplus never drain the battery and need no such guard.
- **Rule 4** asks whether step-up *buys* anything. When the day fills the home battery **and** the car by evening either way, the step-up step changes only the **route**: the same kWh reaches the car either directly from PV or via a charge/discharge cycle of the home battery, which costs the round-trip loss. Both end states are identical, so the lossy route is vetoed. Rule 4 only ever *removes* the draining step, so it can never endanger the battery.
- Rule 4 reads the **p10 PV / p50 load** forecast — the same conservative pair as the charge target (Section 4.2.4) and the shaving fill check (Section 4.2.3). Suppression must hold on a *low*-PV outcome, not merely a median one: a p50 day that under-delivers would leave the car short with the faster step already forgone.
- The battery side of Rule 4 tests the simulated **peak** SOC, not the end-of-day value — the battery legitimately discharges into the evening after reaching its target. The car side tests the **end-of-day** value; the car curve is monotonic non-decreasing, so that is also its peak.
- Rule 4 **fails open** (no suppression — Rule 3 governs alone) whenever the signal cannot be computed: no car SOC, no car-side target, smart-car integration disabled, or a stale/empty forecast. Suppressing on an unreliable signal would slow the car on a day that needed the extra step.
- Rule 4 is evaluated on the 15-min cycle and cached for the 10-s EV loop, published as `step_up_suppressed` / `step_up_suppressed_reason` on `sensor.ev_target_power`. It shares one allocation model with the car SOC forecast curve (Section 4.3.9) — house battery first up to `battery_target_soc`, overflow to the car at `charge_efficiency`, deficits drain the house only — run on p10 for the gate and on p50 for the published curve.
- The chosen step's offset from the surplus-snapped level is published as the `ev_step_offset` attribute on `sensor.ev_target_power` (+n stepped up / -n stepped down / null when not solar-charging).

#### `will_battery_hit_full()` -- dashboard (15-min)

Lives on `EVBatteryOptimizer`. Returns whether the **peak** home-battery SOC reaches its `full_threshold` (passed as `battery_target_soc`, Section 4.2.4) between now and end of today (midnight local), plus the time it first does, by reading the 15-min `soc_forecast` curve from InfluxDB. Backs the `battery_will_be_full` / `battery_full_time` / `battery_peak_soc` dashboard attributes on `sensor.battery_decision` (published on the 15-min cycle). **It does not gate the car** — the Topic 1 Rule 4 gate uses `reaches_target_today` re-anchored to the live SOC on the 10-s loop (Section 4.3.6); `sensor.ev_target_power`'s `battery_will_be_full` / `battery_full_time` come from that live path.

#### Amp-step conversion (plumbing)

The wallbox only charges at integer amp levels. The energy-manager picks from a discrete set of **M-Bus calibrated power steps** -- the actual power delivered at each amp level. **The step table is phase-specific** and chosen from the OCPP server's detected cable phase count (`sensor.wallbox_phases`, ocpp-server FSD §3.6.4.1), because the wallbox draws only the connected phases:

| Amps | 3-phase (M-Bus W) | 1-phase (M-Bus W) |
|-----:|------------------:|------------------:|
| 6 | 3962 | 1380 |
| 7 | 4354 | 1610 |
| 8 | 5117 | 1840 |
| 9 | 5727 | 2070 |
| 10 | 6288 | 2300 |
| 11 | 7034 | 2530 |
| 12 | 7624 | 2760 |
| 13 | — | 2990 |
| 14 | — | 3220 |
| 15 | — | 3450 |
| 16 | — | 3680 |

`POWER_STEPS_3P` is the 2026-03-04 M-Bus sweep (6–12 A); `POWER_STEPS_1P` is 230 W/A from live single-phase MeterValues (2026-07-09, 6–16 A). Using the wrong table breaks single-phase charging: the 3-phase steps all start at 3962 W, above the 1φ maximum (3680 W), so `snap_to_power_step` finds no valid step and the car cannot modulate. `power_steps_for_phases(phases)` selects the table; `snap_to_power_step(surplus, steps=…)` returns the highest step <= surplus (Rule 2), Rule 3's step-up tries the next step above. The OCPP server converts watts -> integer amps with a phase-specific divisor (`round(power_w / 637)` 3φ, `round(power_w / 230)` 1φ), capped at `max_current_a` (ocpp-server FSD §7.2).

#### Self-correction and rate limiting (plumbing)

`control_ev_charging()` runs every 10 s with live surplus, so the step tracks conditions automatically:

| Condition change | Effect |
|------------------|--------|
| PV drops (clouds) / load rises | surplus drops -> next cycle picks a lower amp level |
| PV rises / load drops | surplus rises -> next cycle may pick a higher level |

**Rate limit:** `number.wallbox_power_limit` is sent only when it differs from the last-sent value **and** >= 30 s have passed since the last change -- preventing oscillation at step boundaries (e.g. surplus hovering near 3962/4354 W flipping 6 A <-> 7 A). 0 W (pause) bypasses the rate limit for safety. `sensor.ev_target_power` still updates every 10 s for the dashboard.

### 4.3.8 Smart Car SOC Polling

The EV battery SOC is read from the Hello Smart API and published as `sensor.smart_battery`. Polling frequency adapts to wallbox state to balance freshness against API rate limits.

> **Interface:** the `smarthashtag` HACS integration, the local `pysmarthashtag` patch, the rate-limit behavior (HTTP `403048`, adaptive backoff, `MAX_TRANSIENT_FAILURES = 10`), the `HelloSmartClient` session caching strategy (2 vs 6 requests/poll), the `sensor.smart_battery_last_known` template sensor, and debug-logging configuration are all documented in `Home-Installation-fsd.md §7.7` "Smart car interface (smarthashtag)". This section only covers how the energy-manager *consumes* that interface.

#### Polling Strategy

| Trigger | Frequency | Condition |
|---------|-----------|-----------|
| Mode changed | Once, immediately | `input_select.ev_charging_mode` value differs from previous cycle (e.g. solar → immediate). Ensures fresh SOC before any charging decision. |
| Car connected | Once, immediately | Wallbox status transitions to `Preparing` from a disconnected state (`Available`, `Unknown`, or first poll) |
| Active charging | Every 60 seconds | Wallbox status = `Charging` |
| Idle / baseline | Every 60 minutes | Scheduled job (always running) |

**Priority:** Mode change > car connected > charging interval (first matching trigger wins per cycle).

**Connected states** (no re-poll on transitions between these): `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`.

#### Implementation

- **Adaptive polling** runs inside `control_ev_charging()` (10-second loop), checking wallbox status transitions
- **Hourly baseline** is a separate APScheduler job (`id="smart_car_soc"`)
- **Monotonic timestamps** (`time.monotonic()`) track poll intervals to avoid clock-skew issues
- **Wallbox status tracking** via `_last_wallbox_status` detects connection events (transition to `Preparing`)
- **Mode tracking** via `_last_ev_charging_mode` detects charging mode changes (skips first cycle to avoid false trigger on startup)

#### Python-side Last-Known Fallback

The Hello Smart integration drops to `unavailable` when the car goes to sleep (minutes after last charge/drive) or when the API is rate-limited beyond the 10-failure grace window. Downstream consumers (Car SOC Forecast, dashboard cards) still need a number. The energy-manager's `_read_car_soc_with_fallback()` in `run.py`:

1. Read live state of `smart_car.soc_entity`; return its numeric value if present.
2. If unavailable/unknown, call `_query_last_value()` to read the last numeric value from the `HomeAssistant` InfluxDB bucket over the past 7 days.
3. If nothing found, return `None` and skip the car SOC forecast for this cycle (logged at WARN).

This is independent of — and complementary to — the HA-side `sensor.smart_battery_last_known` trigger template (used by dashboards), which is defined in `Home-Installation-fsd.md §7.8`.

### 4.3.9 Car SOC Forecast

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

**What the forecast omits:** the strict `ev_min_solar_power` threshold, amp-step snapping, the live daily-target permission check, and the 48-hour/current-SOC step-up guard (Sections 4.3.6–4.3.7), which operate on the 10-second decision loop. The forecast is a best-case multi-day outlook.

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

The `soc_forecast` measurement uses a `scenario` tag to store three curves:

| Scenario | Description | Color in Grafana |
|----------|-------------|------------------|
| `battery_off` | The implication of holding discharge during cheap hours | Green (solid) |
| `battery_on` | The implication of free discharge on every deficit | Orange (dashed) |
| `planned` | Whichever option the decision selects each cycle (what runs) | (internal — EV gate) |

### 4.5.2a Daily Flows Summary (long-term reporting)

**Measurement `flows_daily`, bucket `energy_longterm` (infinite retention)** — one point per day
(timestamp = local midnight of the summarized day), written at 23:58 local. Owns the household
energy flows and money; PV physics lives in swiss-solar-forecast's `pv_daily` (its FSD §8.1).

| Field | Unit | Description |
|-------|------|-------------|
| `car_kwh` | kWh | Wallbox charging energy (OCPP counter, reset-safe delta) |
| `lab_kwh` | kWh | Lab (Shelly 2PM Desk + Bench) |
| `house_rest_kwh` | kWh | House (3EM) minus lab |
| `house_kwh` | kWh | House total (3EM; excludes car) |
| `import_kwh` / `export_kwh` | kWh | Whole-site grid exchange, integrated from the M-Bus power signal `grid_power` (includes the wallbox branch — matches what the utility meters) |
| `import_cost_chf` | CHF | Hourly import × HT/NT rate via the EBL calendar (§4.2.2 `expensive_mask`) |
| `export_revenue_chf` | CHF | Export × feed-in rate |
| `net_cost_chf` | CHF | Cost − revenue |
| `production_kwh` | kWh | Total PV production (both inverters) |
| `consumption_kwh` | kWh | metered load (house + car); grid-balance fallback if load meters missing |
| `autarky` | — | 1 − import/consumption, where consumption = metered load (house + car); clamped 0–1 |
| `battery_min_soc` | — | Daily minimum battery SOC (0–1); near the reserve floor = battery emptied → import → lower autarky |
| `battery_max_soc` | — | Daily maximum battery SOC (0–1); short of full = battery never fully charged (weak-production day) |
| `self_consumption` | — | (load − battery_discharge − import + battery_charge)/production (battery is PV-only charged); clamped 0–1 |

Tariff rates are configuration (`reporting.import_ht_chf_kwh` 0.3202, `reporting.import_nt_chf_kwh`
0.2434, `reporting.feed_in_chf_kwh` 0.09 — EBL 2026 incl. VAT; feed-in follows the quarterly
reference market price and is updated in config when EBL statements change).

### 4.5.2 Forecast Snapshot for Accuracy Tracking

The `soc_forecast_snapshot` measurement provides persistent forecast storage:

- **Written every 15 minutes** with the current `planned` forecast
- **Only overwrites from NOW onwards** — earlier points remain from previous writes
- **Accumulates over time** — creates continuous forecast history
- **Compare with actual SOC** from the `HomeAssistant` bucket (`battery_state_of_capacity`) to evaluate forecast accuracy

Example: At 21:00, forecast is written for 21:00→21:00 next day. At 23:00, only 23:00→21:00 is overwritten, preserving the 21:00→23:00 portion.

**Query examples:**

```flux
# SOC forecast - battery off (the hold implication)
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")
  |> filter(fn: (r) => r.scenario == "battery_off")

# SOC forecast - battery on (the free-discharge implication)
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "soc_forecast")
  |> filter(fn: (r) => r.scenario == "battery_on")

# Forecast snapshot (for accuracy comparison)
from(bucket: "energy_manager")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._measurement == "soc_forecast_snapshot")

# Actual SOC (for comparison with forecast)
from(bucket: "HomeAssistant")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.entity_id == "battery_state_of_capacity")
  |> filter(fn: (r) => r._field == "value")

# Energy balance with cumulative
from(bucket: "energy_manager")
  |> range(start: -1h, stop: 120h)
  |> filter(fn: (r) => r._measurement == "energy_balance")
  |> filter(fn: (r) => r._field == "cumulative_wh")

# Car SOC Forecast
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

The dashboard derives all display state from `sensor.wallbox_status` (and
power/SOC) only. It does **not** read `binary_sensor.wallbox_connected`:
a dropped wallbox↔server WebSocket link is an error condition owned by the
OCPP server (recovery/alerting), not a dashboard display state.

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

Plain-language **EV charging** status. A `custom:button-card` whose `label`
template builds human sentences from the structured `sensor.ev_target_power`
attributes (it does **not** print the raw `reason` string — that stays in
the logs). It also reads `binary_sensor.car_ready` for the "no car"
state (NOT `wallbox_connected`, which is only the server WebSocket link). Design goal: a headline status anyone can read at a glance, plus 1–2
plain supporting lines.

**Card layout — live examples (Balanced, English):**

The day-mode line is shown **first** (most important context). The power is **one
line** that always shows the car power split into two components that sum to the
total — `X kW (Y surplus + Z battery)`: the **battery** part is the live
contribution-to-the-car (`sensor.battery_charge_discharge_power` discharge capped
at the gap surplus doesn't cover; 0 while it charges); **surplus** is shown as the
remainder (`total − battery`). The grid is **never shown** — a brief grid import
during a cloud (before the target ramps down) is only very short-term and is
folded into surplus. Lines are left-aligned blocks.

```
🚗  Charging the car
    Car day · car has first call on the solar surplus
    5.1 kW (4.8 kW surplus + 305 W battery)
    Car reaches 100% at 17:42 (in 2h 30m)
```
```
🚗  Car on hold
    Shaving day · battery prioritises peak-clipping
    Protecting the home battery
    It would drop to 11% (keeps at least 20%)
```
```
🚗  Waiting for sun
    Car day · car has first call on the solar surplus
    Solar surplus 0.8 kW — needs 3.0 kW
    Car reaches 80% tomorrow at 11:15
```
```
🚗  No car connected
```

**Status mapping** (technical → human):

| Condition | Headline | Supporting lines (day-mode line first) |
|-----------|----------|------------------|
| `car_ready` ≠ on | No car connected | — |
| `snap_power_w` > 0 | Charging the car | day mode (`shaving_day_mode`); one power line `X kW (Y surplus + Z battery)` — battery = live contribution-to-car (`sensor.battery_charge_discharge_power`), surplus = remainder (`total − battery`); grid never shown (transient, folded into surplus); ETA to car target (forecast `car_target_time` / `sensor.smart_charging_max_last_known`) |
| `surplus_power_w` < `threshold_w` | Waiting for sun | day mode; surplus vs needed; ETA to car target |
| `ev_safe` = false | Car on hold | day mode; protecting battery; forecast dip vs floor |
| otherwise | Not charging | day mode |
| (car connected) | — | the day-mode line is **prepended** as the first supporting line |

**Card YAML** (`custom:button-card`, `entity: sensor.ev_target_power`):

```yaml
type: custom:button-card
entity: sensor.ev_target_power
show_name: false
show_state: false
show_label: true
show_icon: false
label: >-
  [[[
  const a = entity.attributes;
  const kw = (w) => (w == null ? '?' : (Math.abs(w) >= 1000 ? (w / 1000).toFixed(1) + ' kW' : Math.round(w) + ' W'));
  const num = (id) => { const s = states[id]; return s && !isNaN(parseFloat(s.state)) ? parseFloat(s.state) : null; };
  // Forecast-based ETA line (solar-aware, from energy-manager's car SOC forecast).
  // Target SOC is sensor.smart_charging_max_last_known. Returns null when the
  // forecast does not reach target within the horizon. Day-aware because
  // solar-only ETAs are often several days out.
  const etaLine = () => {
    const targetTime = a.car_target_time;
    if (!targetTime) return null;
    const tgt = num('sensor.smart_charging_max_last_known');
    const label = tgt != null ? Math.round(tgt) + '%' : 'target';
    const eta = new Date(targetTime);
    const now = new Date();
    const trMin = (eta.getTime() - now.getTime()) / 60000;
    if (trMin <= 0) return null;
    const hh = ('0' + eta.getHours()).slice(-2);
    const mm = ('0' + eta.getMinutes()).slice(-2);
    const d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const d1 = new Date(eta.getFullYear(), eta.getMonth(), eta.getDate());
    const dayDiff = Math.round((d1.getTime() - d0.getTime()) / 86400000);
    if (dayDiff === 0) {
      const h = Math.floor(trMin / 60);
      const m = Math.round(trMin % 60);
      const dur = (h > 0 ? h + 'h ' : '') + m + 'm';
      return 'Car reaches ' + label + ' at ' + hh + ':' + mm + ' (in ' + dur + ')';
    } else if (dayDiff === 1) {
      return 'Car reaches ' + label + ' tomorrow at ' + hh + ':' + mm;
    }
    const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    return 'Car reaches ' + label + ' ' + days[eta.getDay()] + ' at ' + hh + ':' + mm + ' (in ' + dayDiff + ' days)';
  };
  // Tier-2 day mode (Topic 5 latch, FSD 4.2.3) — shown first as the day's context.
  const dayLine = () => {
    const dm = a.shaving_day_mode;
    if (!dm) return null;
    if (dm === 'shaving_day') return 'Shaving day · battery prioritises peak-clipping';
    return 'Car day · car has first call on the solar surplus';
  };
  const rule = a.ev_charging_rule || 'none';
  const power = a.snap_power_w || 0;
  const surplus = a.surplus_power_w;
  const threshold = a.threshold_w;
  const min48 = a.battery_min_soc_forecast_48h != null ? Math.round(a.battery_min_soc_forecast_48h) : '?';
  const floor = a.battery_min_soc_floor != null ? Math.round(a.battery_min_soc_floor) : '?';
  const evSafe = a.ev_safe;
  const wc = states['binary_sensor.car_ready'];
  const connected = wc ? wc.state === 'on' : true;
  let title;
  let lines = [];
  if (!connected) {
    title = '🚗  No car connected';
  } else if (power > 0) {
    title = '🚗  Charging the car';
    // One power line. When the home battery is discharging to help, split the car
    // power into surplus + battery; the surplus part is the remainder (total −
    // battery) so the two always sum to the displayed total even if the surplus
    // and battery sensors sample a moment apart. (bp sign: + charge / − discharge.)
    // Always show the car power split into surplus + battery (two components that
    // always sum to the total). The battery part is the live contribution-to-the-car
    // (its discharge capped at the gap surplus doesn't cover; 0 while it charges);
    // surplus is shown as the remainder (total − battery), so a brief grid import
    // during a cloud — before the target ramps down — is folded into surplus rather
    // than surfaced (it is only very short-term). Sign: + charge / − discharge.
    const bp = num('sensor.battery_charge_discharge_power');
    let pl;
    if (rule === 'battery_full') {
      pl = kw(power) + ' (home battery full)';
    } else {
      const surpActual = (surplus != null) ? Math.max(0, Math.min(surplus, power)) : power;
      const gap = Math.max(0, power - surpActual);
      const batt = (bp != null && bp < -50) ? Math.min(-bp, gap) : 0;
      const surpShown = power - batt;
      pl = kw(power) + ' (' + kw(surpShown) + ' surplus + ' + kw(batt) + ' battery)';
    }
    lines.push(pl);
    const e = etaLine();
    if (e) lines.push(e);
  } else if (surplus != null && threshold != null && surplus < threshold) {
    title = '🚗  Waiting for sun';
    lines.push('Solar surplus ' + kw(surplus) + ' — needs ' + kw(threshold));
    const e = etaLine();
    if (e) lines.push(e);
  } else if (evSafe === false) {
    title = '🚗  Car on hold';
    lines.push('Protecting the home battery');
    lines.push('It would drop to ' + min48 + '% (keeps at least ' + floor + '%)');
  } else {
    title = '🚗  Not charging';
  }
  // Day mode is the most important context → first supporting line.
  if (connected) { const d = dayLine(); if (d) lines.unshift(d); }
  let out = '<div style="font-weight:600">' + title + '</div>';
  for (const l of lines) { out += '<div style="opacity:0.75">' + l + '</div>'; }
  return out;
  ]]]
tap_action:
  action: none
styles:
  card:
    - padding: 16px
    - background-color: var(--card-background-color)
  label:
    - font-size: 15px
    - justify-self: start
    - white-space: normal
    - line-height: "1.6"
    - text-align: left
```

**Attributes published on `sensor.ev_target_power`** (run.py). The card uses
the structured fields below; `reason` is kept for logs, not rendered:

| Attribute | Purpose |
|-----------|---------|
| `reason` | Full decision string incl. EOD snap-up gate (logs/debug; not shown on card) |
| `ev_charging_rule` | Active rule: `battery_full`, `solar_surplus`, or `none` |
| `threshold_w` | Min solar power threshold (W) |
| `surplus_power_w` | Solar surplus = PV − house load (W) |
| `grid_export_w` | Current grid export (W) |
| `snap_power_w` | Winning charging power from `snap_to_power_step()` (W); 0 = no charging |
| `ev_step_offset` | Chosen amp step's offset from the surplus-snapped level: `+n` snapped up (home battery bridges the gap), `-n` stepped down (battery preserved), `0` matched, `null` not solar-charging — published for diagnostics; not shown on the card (the home-battery line shows only the contribution) |
| `battery_soc` | Current home-battery SOC (%) |
| `battery_will_be_full` | Informational: does peak SOC today reach 100%? |
| `battery_full_time` | Forecast time the home battery reaches 100% (if any) |
| `battery_target_soc` | Topic 3 dynamic charge ceiling (%) — see 4.2.4 |
| `shaving_day_mode` | Topic 5 day-mode latch: `shaving_day` or `car_day` (4.2.3) — drives the day-mode context line |
| `car_target_time` | Forecast time the car reaches its target SOC (ISO-UTC, else `null`) — drives the ETA line |
| `car_target_soc` | Car's target SOC from `sensor.smart_charging_max_last_known` (%) |
| `car_eod_soc_forecast` | Forecast car SOC at end of today on the p50 curve (%) |
| `step_up_suppressed` | Topic 2 Rule 4 active — step-up vetoed because p10 fills both battery and car by evening (4.3.7) |
| `step_up_suppressed_reason` | The p10 battery-peak and car-EOD figures behind `step_up_suppressed` |
| `ev_safe` | `check_ev_safe` passed — EV allowed this cycle |
| `battery_min_soc_forecast_48h` | Min forecast home-battery SOC over next 48 h, EV load subtracted (%) |
| `battery_min_soc_floor` | Floor used by the EV step-up guard (= `battery.no_buy_floor_percent`) |

**Result icons:** ⚡ Rule 1 (Battery Full), ☀️ Rule 2 (Solar Surplus), ⏸️ no charging.

### 4.6.4 Battery Decision Card

Companion to 4.6.3 for the **home battery**. Surfaces the home-battery
decisions that otherwise live only in the logs — the discharge decision
(4.2.2), the charge-shaving decision and its once-daily day-mode latch (4.2.3),
and the dynamic charge ceiling (4.2.4). Advisory display only; the card never
actuates.

`publish_battery_decision()` writes `sensor.battery_decision` every 15-min
cycle. State string: `discharge=on|off charge=<action>`.

**Sensor attributes used by card** (published on `sensor.battery_decision`, run.py):

| Attribute | Purpose |
|-----------|---------|
| `battery_soc` | Current home-battery SOC (%) |
| `discharge_allowed` | Combined discharge decision (4.2.2) |
| `discharge_reason` | Human-readable discharge reason |
| `discharge_blocked_by_protection` | SOC-forecast protection flag |
| `discharge_blocked_by_ev` | EV manual-charge block flag |
| `discharge_min_soc_percent` | Min forecast SOC over the protection window (%) |
| `charge_use_case` | `A` (EV owns surplus) or `B` (shaving) |
| `charge_action` | `charging`, `deferred`, or `released` |
| `charge_reason` | Human-readable charge-shaving reason |
| `charge_limit_w` | Charge limit being applied (W); `0` = deferred, `null` = not managed |
| `battery_target_soc` | Topic 3 dynamic charge ceiling (%) — see 4.2.4; drives the ceiling line |
| `charge_target_enabled` | Whether Topic 3 charge-target control is active (gates the ceiling line) |
| `battery_target_reason` | Human-readable target explanation (e.g. `floored to 80% (survival need only 59%)`; logs/debug, not rendered) |
| `shaving_day_mode` | Topic 5 day-mode latch: `shaving_day` or `car_day` (4.2.3) |
| `shaving_decision_hour` | Local hour the day-mode latch is decided (default 8) |
| `battery_will_be_full` | Forecast: does peak SOC reach the target today? (`null` if forecast unavailable) |
| `battery_full_time` | Forecast time the home battery first reaches the target today (`HH:MM` local, else `null`) |
| `battery_peak_soc` | Forecast peak home-battery SOC today (%) |

The card builds plain sentences from the structured attributes (it does
**not** print the raw `*_reason` strings — those stay in the logs). It also
reads `sensor.surplus_power` (to say "idle" when there is no sun),
`sensor.battery_charge_discharge_power` (+ charge / − discharge) for the **live
flow** — line 1 shows what the battery is actually doing, because `charge_action`
= `released` only means charging is *allowed*, not that it is happening (on a
car day the battery often discharges to support the car) — and `sensor.wallbox_power`
plus the EV sensor's `snap_power_w` / `surplus_power_w` to say "Helping charge the
car · N kW" (vs "Powering the house") while the car draws, where **N is the same
battery-to-car value the EV card shows** (the two cards agree). The
`battery_will_be_full` / `battery_full_time` / `battery_peak_soc` attributes
come from `will_battery_hit_full()` (today's `planned` SOC forecast,
against the dynamic target) and are published independently of EV state, so the
card can show "Reaches N% by HH:MM" even with no car plugged in.

**Card layout — live examples (Balanced, English):**

Car day, battery discharging to help the car (low SOC, step-up):
```
🔋  Home battery 19%
    Helping charge the car · 724 W
    Longevity cap 90%
    Reaches 90% by 13:30
```
Shaving day, deferring to clip the midday export peak:
```
🔋  Home battery 55%
    Shaving day · holding for the midday peak
    Longevity cap 90% + shaving headroom
    Powers the house when needed
```
Evening, holding for the expensive hours:
```
🔋  Home battery 90%
    Idle — no solar surplus
    Holding charge for tonight
```

**Status mapping** (technical → human). **Line 1 reflects the *live* battery
flow** (`sensor.battery_charge_discharge_power`, + charge / − discharge),
**not** `charge_action` — "released" means charging is *allowed*, not that the
battery is charging (on a car day it often discharges to support the car):

| Field | Value | Phrase |
|-------|-------|--------|
| `bp` (flow) | discharging (< −50 W), car drawing (`wallbox_power` > 100) | Helping charge the car · *battery-to-car* (same value as the EV card — discharge capped at the gap surplus doesn't cover) |
| `bp` (flow) | discharging (< −50 W), car idle (or trivial car share) | Powering the house · `\|bp\|` (total discharge) |
| `bp` (flow) | charging (> 50 W), car day | Charging from solar · `bp` |
| `bp` (flow) | charging (> 50 W), shaving day | Shaving the peak · charging `bp` |
| `battery_soc` ≥ 100 | charging + surplus | Full — surplus exported |
| `charge_action` | `deferred` + `shaving_day`, idle | Shaving day · holding for the midday peak |
| `charge_action` | `deferred` + `car_day`, idle | Saving room for the midday peak |
| flow idle | no surplus | Idle — no solar surplus |
| `charge_target_enabled` | true (+ SOC < 100) | Longevity cap `battery_target_soc`% (`+ shaving headroom` only on a shaving day; `Calibration charge · 100%` at 100) |
| `battery_will_be_full` | true (+ SOC < 100) | Reaches `battery_target_soc`% by `battery_full_time` (or `Full by …` when target = 100) |
| `battery_will_be_full` | false (+ peak > SOC, surplus) | Peaks at ~`battery_peak_soc`% today |
| `discharge_blocked_by_ev` | true | Reserved for the car |
| `discharge_blocked_by_protection` | true | Holding charge for tonight |
| `discharge_allowed` | true (and not already discharging) | Powers the house when needed |

**Card YAML** (`custom:button-card`, `entity: sensor.battery_decision`):

```yaml
type: custom:button-card
entity: sensor.battery_decision
show_name: false
show_state: false
show_label: true
show_icon: false
label: >-
  [[[
  const a = entity.attributes;
  const kw = (w) => (w == null ? '?' : (Math.abs(w) >= 1000 ? (w / 1000).toFixed(1) + ' kW' : Math.round(w) + ' W'));
  const num = (id) => { const s = states[id]; return s && !isNaN(parseFloat(s.state)) ? parseFloat(s.state) : null; };
  const soc = a.battery_soc != null ? Math.round(a.battery_soc) : '?';
  const action = a.charge_action || '';
  // Topic 5 day-mode latch (decided once at 08:00). Falls back to the use-case
  // flag (A = car day, B = shaving day) when the attribute is absent.
  const useCase = a.charge_use_case || '';
  const dayMode = a.shaving_day_mode || (useCase === 'B' ? 'shaving_day' : 'car_day');
  const shaveDay = dayMode === 'shaving_day';
  // Topic 3 dynamic charge ceiling (longevity target; 100 = calibration).
  const target = a.battery_target_soc != null ? Math.round(a.battery_target_soc) : null;
  const tEnabled = a.charge_target_enabled;
  const byEv = a.discharge_blocked_by_ev;
  const byProt = a.discharge_blocked_by_protection;
  const disAllowed = a.discharge_allowed;
  const willFull = a.battery_will_be_full;
  const fullTime = a.battery_full_time;
  const peak = a.battery_peak_soc;
  // Live battery flow sign (verified): + = charge (from PV), - = discharge (to house/car).
  const bp = num('sensor.battery_charge_discharge_power');
  const charging = bp != null && bp > 50;
  const discharging = bp != null && bp < -50;
  // When the car is drawing, a discharging battery is (mostly) helping the car.
  const wbp = num('sensor.wallbox_power');
  const carCharging = wbp != null && wbp > 100;
  // battery-to-car, computed identically to the EV card so the two cards agree:
  // the discharge capped at the gap the surplus doesn't cover (the rest feeds the
  // house). Uses the EV sensor's own snap_power_w / surplus_power_w.
  const evs = states['sensor.ev_target_power'];
  const eva = evs ? evs.attributes : {};
  const carP = eva.snap_power_w || 0;
  const evSurp = eva.surplus_power_w;
  const gap = (carP > 0 && evSurp != null) ? Math.max(0, carP - Math.max(0, Math.min(evSurp, carP))) : 0;
  const battToCar = discharging ? Math.min(-bp, gap) : 0;
  const dischargeStr = (carCharging && battToCar > 30)
    ? ('Helping charge the car · ' + kw(battToCar))
    : ('Powering the house · ' + kw(-bp));
  let surplus = null;
  const sp = states['sensor.surplus_power'];
  if (sp && sp.state && !isNaN(parseFloat(sp.state))) { surplus = parseFloat(sp.state); }
  const hasSun = surplus != null && surplus >= 100;
  let lines = [];
  // Line 1 — what the battery is ACTUALLY doing now (live flow beats charge_action,
  // which only says charging is "released"/allowed, not that it is happening).
  if (soc !== '?' && soc >= 100) {
    if (charging) lines.push('Full — surplus exported');
    else if (discharging) lines.push(dischargeStr);
    else lines.push('Full');
  } else if (discharging) {
    lines.push(dischargeStr);
  } else if (charging) {
    lines.push((shaveDay ? 'Shaving the peak · charging ' : 'Charging from solar · ') + kw(bp));
  } else if (action === 'deferred') {
    lines.push(shaveDay ? 'Shaving day · holding for the midday peak' : 'Saving room for the midday peak');
  } else if (!hasSun) {
    lines.push('Idle — no solar surplus');
  } else {
    lines.push('Idle');
  }
  // Line 2 — Topic 3 charge ceiling (longevity). "Shaving headroom" only on a shaving day.
  if (tEnabled && target != null && soc !== '?' && soc < 100) {
    if (target >= 100) { lines.push('Calibration charge · 100%'); }
    else { lines.push('Longevity cap ' + target + '%' + (shaveDay ? ' + shaving headroom' : '')); }
  }
  // Line 3 — fill / peak forecast.
  if (soc !== '?' && soc < 100) {
    if (willFull === true && fullTime) {
      lines.push((target != null && target < 100 ? 'Reaches ' + target + '% by ' : 'Full by ') + fullTime);
    } else if (willFull === false && peak != null && peak >= soc + 1 && hasSun) {
      lines.push('Peaks at ~' + peak + '% today');
    }
  }
  // Line 4 — Topic 4 discharge policy. Block reasons always; the generic
  // "powers the house" only when not already shown as discharging above.
  if (byEv) { lines.push('Reserved for the car'); }
  else if (byProt) { lines.push('Holding charge for tonight'); }
  else if (disAllowed && !discharging) { lines.push('Powers the house when needed'); }
  let out = '<div style="font-weight:600">🔋  Home battery ' + soc + '%</div>';
  for (const l of lines) { out += '<div style="opacity:0.75">' + l + '</div>'; }
  return out;
  ]]]
tap_action:
  action: none
styles:
  card:
    - padding: 16px
    - background-color: var(--card-background-color)
  label:
    - font-size: 15px
    - justify-self: start
    - white-space: normal
    - line-height: "1.6"
    - text-align: left
```

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

### 4.7.5 M-Bus Staleness Alert

The M-Bus grid reading (`mbus_grid_power` — the EBL smart meter via the external gPlug reader) feeds
**reporting and observability only**: the grid-export dashboard attribute, energy-flow accounting,
and the integration-test observer. EV and battery control run on `surplus_power` (PV − house load),
**not** on this signal. `_read_grid_power()` treats the reading as fresh only when its `last_updated`
age is under 20 s and otherwise falls back silently to the DTSU meter (`dtsu_grid_power`), so a
reader failure that stops M-Bus updates entirely degrades reporting unseen.

A watchdog (`src/mbus_watchdog.py`, `MbusWatchdog`) is fed the freshness of every grid read and
sends a **one-shot Telegram warning** once the M-Bus meter has been **continuously stale for
`mbus_stale_alert_seconds`** (default 300 s), naming the entity, the elapsed stale time, and that
reporting is degraded while control is unaffected. Brief gaps (the ordinary 20 s fallback) never
alert. When the meter resumes publishing fresh readings, a single **info** recovery notice is sent
and the watchdog rearms for the next episode.

The stale timer is in-process: an add-on restart re-arms detection, so a still-dead meter re-alerts
within `mbus_stale_alert_seconds` of restart.

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

---

# Chapter 6: Test Cases

This chapter is the canonical home for EnergyManager's test-case specs; it is indexed in the testing
hub `Harness/project/testing.md` (strategy + levels in `Harness/standards/testing.md`).

## 6.1 Battery Discharge Optimizer Tests

Test file: `energy-manager/tests/test_battery_optimizer.py`

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
| `test_previously_blocked_requires_margin_to_reallow` | When already blocked, min_soc barely above 10% stays blocked | Cheap tariff, min_soc 10-12%, `previously_blocked=True` | `discharge_allowed=False` |
| `test_previously_blocked_allows_with_clear_margin` | When already blocked but min_soc clearly above 12%, allow | Cheap tariff, SOC 90%, `previously_blocked=True` | `discharge_allowed=True` |
| `test_not_previously_blocked_allows_at_threshold` | When not already blocked, min_soc at 10% allows normally | Cheap tariff, min_soc 10-12%, `previously_blocked=False` | `discharge_allowed=True` |

#### Dataclass Validation

| Test | Description | Conditions | Expected Result |
|------|-------------|------------|-----------------|
| `test_decision_has_required_fields` | DischargeDecision has all required fields | Create DischargeDecision | Has `discharge_allowed`, `reason`, `min_soc_percent` fields |

**Run tests:**
```bash
cd energy-manager && python -m pytest tests/test_battery_optimizer.py -v
```

**All 24 tests passing** (as of v1.6.97)

---

## 6.2 Appliance Signal Tests

Test file: `energy-manager/tests/test_appliance_signal.py`

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
cd energy-manager && python -m pytest tests/test_appliance_signal.py -v
```

**All 26 tests passing** (as of v1.5.12)

---

## 6.3 EV Charging State Machine Tests

Test file: `energy-manager/tests/test_ev_state_machine.py`

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
| TestOffMode | 6 | X0 from IDLE/SOLAR/IMMEDIATE (→ OFF, 0 W, budget cleared); sticky under surplus; not auto-reverted; X1 OFF → solar resumes |

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
cd energy-manager && python -m pytest tests/test_ev_state_machine.py -v
```

**Integration tests (manual):**

| ID | Test | Expected |
|----|------|----------|
| EV-10 | Dashboard: tap Cheap Charge | `input_select.ev_charging_mode` = `cheap` |
| EV-11 | Dashboard: tap Charge Now | `input_select.ev_charging_mode` = `immediate` |
| EV-12 | Dashboard: tap active button | `input_select.ev_charging_mode` = `solar` (back to default) |
| EV-13 | Dashboard: car connected, charging | Card shows power in W, state = SOLAR/CHEAP/IMMEDIATE |
| EV-14 | Mode change while charging | New mode takes effect within ~60 s (OCPP throttle) |
| EV-15 | Home-battery target gates charging | SOLAR mode: surplus above threshold but the live forecast cannot reach `battery_target_soc` → `ev_charging_power_w=0`; the 48-hour floor remains dashboard/step-up state and is not the stop reason |
| EV-16 | Battery full, low excess | SOLAR with 1-phase min_power_w — captures every watt |
| EV-17 | Select `off` while solar-charging | Wallbox → 0 W, `sensor.ev_charge_status` = `off`; mode stays `off` (no revert to `solar`) even with surplus present |
| EV-18 | Cheap tariff toggles | CHEAP state: charges at max during cheap, pauses during expensive, no state change |
| EV-19 | Car reaches target SOC | Returns to IDLE from any charging state |
| EV-20 | Step-up suppressed when both fill (Rule 4) | SOLAR mode, battery above the floor (Rule 3 would allow step-up), and the p10 forecast reaches `battery_target_soc` **and** the car target by end of today → `ev_step_offset` <= 0, `step_up_suppressed=true`; the car keeps charging at the step at/below surplus |
| EV-21 | Step-up restored when the car falls short | Same as EV-20 but the p10 car end-of-day SOC is below its target → `step_up_suppressed=false`, step-up available again |
| EV-22 | Suppression fails open | Car SOC or car-side target unavailable, or the forecast is stale → `step_up_suppressed=false` (Rule 3 governs alone) |

---

## 6.4 Discharge Blocking Tests

Test file: `energy-manager/tests/test_discharge_blocking.py`

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
cd energy-manager && python -m pytest tests/test_discharge_blocking.py -v
```

**All 19 tests passing** (as of v1.6.28)

---

## 6.5 EV Charging Power Tests

Test file: `energy-manager/tests/test_ev_charging.py`

Tests the `snap_to_power_step()`, `calculate_ev_power()`, and `resolve_phase_gap()` logic (Sections 4.3.6-4.3.7).

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

#### `build_solar_candidates()` — Step-Up Suppression (Rule 4, Section 4.3.7)

| Test | Description | Expected |
|------|-------------|----------|
| `test_both_full_suppresses_step_up` | Rule 3 allows step-up, both targets reached under p10 | Step-up step dropped; snap-down candidates kept |
| `test_default_off_preserves_step_up` | Flag omitted (signal not computable) | Step-up present — unchanged behaviour |
| `test_suppression_does_not_block_charging` | Suppression active | Car still charges at/below surplus |
| `test_suppression_is_redundant_when_already_unprotected` | Below the no-buy floor | Identical candidates with and without suppression |
| `test_target_gate_still_wins_over_suppression` | Battery cannot reach its target | No candidates at all (Topic 1 Rule 4) |

#### `simulate_house_and_car()` — Shared Allocation Model

| Test | Description | Expected |
|------|-------------|----------|
| `test_house_fills_before_car` | Surplus within house headroom | All to house; car unchanged |
| `test_overflow_past_target_goes_to_car` | Surplus beyond `battery_target_soc` | Overflow raises car SOC |
| `test_efficiency_applied_to_car_only` | `charge_efficiency` < 1 | Applied to the car share only |
| `test_deficit_drains_house_not_car` | Negative net energy | House drains; car unchanged |
| `test_house_never_goes_negative` | Deficit exceeds stored energy | House clamps at 0 |
| `test_car_soc_is_monotonic_and_capped_at_100` | Long surplus run | Non-decreasing, capped at 100 % |
| `test_house_ceiling_is_the_target_not_capacity` | Target below capacity | House stops at the target |

**Run tests:**
```bash
cd energy-manager && python -m pytest tests/test_ev_charging.py -v
```

---

## 6.5a Step-Up Suppression Signal Tests

Test file: `energy-manager/tests/test_step_up_suppression.py`

Tests `_evaluate_both_full_by_evening()` — the 15-min p10 evaluation backing Topic 2 Rule 4 (Section 4.3.7).

| Test | Description | Expected |
|------|-------------|----------|
| `test_both_reach_targets_suppresses` | p10 fills battery to target and car to its target | `_both_full_by_evening=True` |
| `test_car_short_keeps_step_up` | Battery reaches target, car short | `False`, reason names the car |
| `test_battery_short_keeps_step_up` | Battery short of `battery_target_soc` | `False` |
| `test_battery_peak_not_eod_counts` | Targets met midday, evening deficit drains the house | `True` — peak counts, not end-of-day |
| `test_periods_after_midnight_are_ignored` | Large surplus lands after today's Swiss midnight | `False` — tomorrow's sun does not count |
| `test_stale_forecast` | Forecast heartbeat stale | `False` (fails open) |
| `test_empty_forecast` / `test_none_forecast` | No p10 frame | `False` |
| `test_no_house_soc` | House SOC unavailable | `False` |
| `test_car_disabled` | Smart-car integration disabled | `False` |
| `test_car_soc_unknown` | Car SOC unreadable (live and cached) | `False` |
| `test_car_target_unreadable` | Car-side target unparseable | `False` |

**Run tests:**
```bash
cd energy-manager && python -m pytest tests/test_step_up_suppression.py -v
```

---

## 6.6 Integration Tests

Integration tests verify cross-module behavior — interactions between EV charging, battery optimizer, tariff boundaries, OCPP transactions, and fail-safes.

### 6.6.1 Staleness & Timing (Category A)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-STALE-01 | Stale M-Bus reading falls back to DTSU | Mock M-Bus `last_updated` > 20 s ago | `_read_grid_power()` returns DTSU value | 🔮 Future — requires HA client mock |
| IT-STALE-02 | Both meters stale → safe default | Both sensors return None | EV power set to 0 (pause) | 🔮 Future — requires HA client mock |
| IT-STALE-03 | Prolonged M-Bus staleness alerts once, recovers once (4.7.5) | Feed `MbusWatchdog` fresh/stale/recovered edges | One `stale` edge past `mbus_stale_alert_seconds`, one `recovered` edge on return; brief gaps silent | ✅ `test_mbus_watchdog.py` |
| IT-TIME-01 | Scheduler fires at 15-min boundaries | APScheduler mock with time steps | `run_optimization` called at :00, :15, :30, :45 | 🔮 Future — requires scheduler mock |
| IT-TIME-02 | EV loop runs at 10 s interval | APScheduler mock | `control_ev_charging` called every 10 s | 🔮 Future — requires scheduler mock |

### 6.6.2 Fail-Safe & Watchdog (Category B)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-FAIL-01 | InfluxDB down → discharge allowed | `forecast_reader.get_combined_forecast` raises | Decision defaults to allow | 🔮 Future — requires InfluxDB mock |
| IT-FAIL-02 | HA API down → no battery control | `ha_client.set_battery_discharge_power` fails 5× | Telegram notification sent | 🔮 Future — requires HA + Telegram mock |
| IT-FAIL-03 | Smart car API timeout → stale SOC retained | `HelloSmartClient.authenticate` raises | Previous SOC entity unchanged | 🔮 Future — requires Smart car mock |
| IT-FAIL-04 | Wallbox disconnects mid-charge → power limit reset | `wallbox_connected` transitions False | No power limit commands sent | 🔮 Future — requires OCPP mock |

### 6.6.3 Phase Switching & Gap (Category C)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-PHASE-01 | Cloud fluctuation stability | 20 excess values oscillating in gap | All snap to one side, zero phase switches | ✅ `test_ev_charging.py::TestPhaseGapStability` |
| IT-PHASE-02 | Phase transition on battery-full change | Excess in gap, toggle `battery_full` | Output switches 3700↔4140 only on flag change | 🔮 Future — pure-logic (extend TestPhaseGapStability) |
| IT-PHASE-03 | Wallbox confirms phase switch | OCPP `MeterValues` after gap-snap change | Measured power matches target phase | 🔮 Future — requires OCPP mock |

### 6.6.4 Battery ↔ EV Cross-Coupling (Category D)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-BATT-01 | Cheap mode blocks discharge | `ev_charging_mode = "cheap"`, power > 0 | `_discharge_blocked_by_ev = True` | ✅ `test_discharge_blocking.py::TestCheapModeBlocksDischarge` |
| IT-BATT-02 | Battery safety blocks EV | Forecast min SOC in next 48 h < `battery.reserve_percent` with candidate EV load | `ev_charging_power_w = 0`, `ev_safe = False` (dashboard) | ✅ `test_ev_battery.py::TestSafetyGate` |
| IT-BATT-03 | Tariff boundary transitions | 20:59 (expensive), 21:01 (cheap), 05:59 (cheap), 06:01 (expensive) | Correct `is_cheap_now` flag | ✅ `test_battery_optimizer.py::TestTariffBoundaryTransitions` |
| IT-BATT-04 | Wallbox idle detection exits all modes | SOLAR/CHEAP/IMMEDIATE + `wallbox_idle=True` | State machine → IDLE, 0 W | ✅ `test_ev_state_machine.py::TestIdleDetection` |

### 6.6.5 Authorization & Transaction (Category E)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-OCPP-01 | RFID authorize → transaction start | OCPP `Authorize.req` with valid tag | `StartTransaction.conf` accepted, power flows | 🔮 Future — requires OCPP handler mock |
| IT-OCPP-02 | Remote stop → transaction ends | `RemoteStopTransaction.req` during charge | Power → 0, `StopTransaction.req` sent | 🔮 Future — requires OCPP handler mock |

### 6.6.6 End-to-End Scenarios (Category F)

| ID | Description | Setup | Expected | Status |
|----|-------------|-------|----------|--------|
| IT-E2E-01 | Full solar day: battery + EV + appliance | Sunny forecast, battery 50%, car connected | Battery fills, EV charges surplus, appliance GREEN | 🔮 Future — requires all mocks |
| IT-E2E-02 | Cloudy day with cheap-mode EV | Low PV forecast, cheap mode at 21:30 | Discharge blocked, EV charges at max, battery holds | 🔮 Future — requires all mocks |

**Legend:** ✅ Implemented and passing | 🔮 Future (prerequisite listed)

## 6.7a Daily Flows Summary Tests

| Case | Assertion | Test |
|------|-----------|------|
| Consumer split & balance | lab = desk+bench; rest = house − lab; consumption = production − export + import; autarky/self-consumption per §4.5.2a | `tests/test_flows_daily.py::test_consumers_and_balance` |
| Tariff attribution | Hourly import costed by HT/NT mask, not a flat rate | `tests/test_flows_daily.py::test_tariff_attribution` |
| Degraded input | Missing hourly data falls back to NT pricing | `tests/test_flows_daily.py::test_no_hourly_data_falls_back_to_nt` |

## 6.7 Passive Integration Observer Tests

24 tests (11 normal, 13 edge) run automatically every 10 s cycle during live operation.
Results persist to `/config/ev_integration_tests.json`; Telegram notifications on status changes.

Report version: **3** (bumped when test definitions change — invalidates stale results).

### 6.7.1 Normal Operation (11 tests)

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

### 6.7.2 Edge Cases (13 tests)

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

# Appendix A: Operations (installation, dashboards, troubleshooting)

Operator procedures — installation, the pre-built Grafana dashboard, and troubleshooting — are
OPERATE, not WHAT. See the Handbook:
[`Handbook.md` → Installation](../../Handbook.md#installation),
[Dashboards & queries](../../Handbook.md#dashboards--queries), and
[Troubleshooting](../../Handbook.md#troubleshooting).

---

# Appendix E: EnergyManager Configuration

See **Section 1.10** for the full configuration architecture.

Secrets (InfluxDB token, Telegram credentials) are entered in the HA add-on Configuration UI — see Section 1.11.2. They are **not** stored in the YAML file below.

## E.1 Non-Secrets (`/config/energy-manager.yaml`)

Editable via File Editor at `/addon_configs/energy-manager/energy-manager.yaml`:

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
  mbus_stale_alert_seconds: 300                   # Telegram alert if M-Bus stale this long (FSD 4.7.5)

ev_charging:
  enabled: true
  mode_entity: "input_select.ev_charging_mode"
  min_solar_power_w: 3500                         # Minimum energy budget for solar charging (battery buffers gap)
  min_current_a: 6                                # Wallbox hardware minimum (amps)
  max_current_a: 16                               # Wallbox hardware maximum (amps)
  max_power_w: 11000

smart_car:
  enabled: true
  soc_entity: "sensor.smart_battery"              # EV SOC sensor (with InfluxDB fallback when unavailable)
  capacity_kwh: 100.0                             # EV usable battery capacity, used by Car SOC Forecast (Section 4.3.9)
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

See Section 4.3.8 for adaptive polling logic.

---

## Changelog

- v2.87: **Topic 2 Rule 4 — no step-up when the p10 forecast fills both the home battery and the car by evening (Section 4.3.7).** Step-up (one amp level above surplus) draws the gap from the home battery, so on a strong day it routes energy PV → battery → car instead of PV → car and pays the round-trip loss for an identical end state: the car reaches its target either way, just marginally later. Observed live 2026-08-06 — battery 78 % (target 90 %, forecast full at 12:15), car 71 % (target 80 %, forecast reached 13:45), surplus ~4.9 kW, yet the manager held the wallbox one step above surplus at 5117 W and discharged the battery at ~300–500 W for hours. New Rule 4 vetoes Rule 3 when the **p10 PV / p50 load** forecast reaches `battery_target_soc` (simulated **peak**, since the battery discharges into the evening) **and** the car's end-of-day SOC reaches its car-side target. p10 rather than p50 because suppression must survive a low-PV outcome. Fails **open** — a missing car SOC/target, disabled smart-car integration, or a stale/empty forecast leaves Rule 3 governing alone. Evaluated on the 15-min cycle (`_evaluate_both_full_by_evening`), cached for the 10-s EV loop, published as `step_up_suppressed` / `step_up_suppressed_reason` on `sensor.ev_target_power`. The car SOC forecast's allocation model is extracted as `simulate_house_and_car()` and now backs both the p50 dashboard curve and the p10 gate. New `TestStepUpSuppression`, `TestSimulateHouseAndCar`, `test_step_up_suppression.py`; EV-20…EV-22. 302 tests pass. (1.9.11 -> 1.9.12)

- v2.86: **Prolonged M-Bus grid-meter staleness now raises a Telegram alert (Section 4.7.5).** The M-Bus grid reading (`mbus_grid_power`, the EBL smart meter via the external gPlug reader) feeds reporting and observability only — the grid-export dashboard attribute, energy-flow accounting, and the integration-test observer — while EV and battery control run on `surplus_power` (PV − house load). `_read_grid_power()` accepts the reading only when its `last_updated` age is under 20 s and otherwise falls back **silently** to the DTSU meter, so a reader outage degraded reporting unseen for a full day. New `MbusWatchdog` (`src/mbus_watchdog.py`), a pure state machine fed the freshness of every grid read: once the meter is continuously stale for `sensors.mbus_stale_alert_seconds` (default 300 s) it sends a **one-shot** warning naming the entity, the elapsed stale time, and that reporting is degraded while control is unaffected; a single info notice follows on recovery and rearms it for the next episode. Brief gaps (the ordinary 20 s DTSU fallback) never alert. The stale timer is in-process, so an add-on restart re-arms detection and a still-dead meter re-alerts within `mbus_stale_alert_seconds`. New `test_mbus_watchdog.py` (7 cases); IT-STALE-03. (1.9.10 -> 1.9.11)

- v2.85: **Topic 3 longevity cap now enforced by the inverter's native end-of-charge SOC register, not just the software power limit (Section 4.2.4).** The old cap wrote `number.battery_maximum_charging_power = 0` once the battery reached `battery_target_soc`. Two gaps let the battery overshoot the 90 % longevity target — verified live 2026-07-17, where it reached 100 %: (1) the power limit is written on the 15-min battery cycle, so the battery kept charging at ~5 kW for up to ~15 min after crossing the target (90 % → ~96 % before the limit landed); (2) a 0 W charge-power limit does not stop DC PV surplus trickling the battery up to the inverter's own SOC cutoff, which sat at 100 %. EM now mirrors `battery_target_soc` onto `number.battery_end_of_charge_soc` every cycle, so the inverter hard-stops charging at the target in real time. The register accepts 90-100 %, exactly the range of the floored target, so it always fits; a 100 % target means "no cap". The power limit is kept as a backing control and drives the dashboard action. When `charge_target_enabled` is off EM leaves the register untouched, releasing it to 100 % only if it had previously lowered it. New `end_of_charge_soc_entity` config key; new `_apply_soc_ceiling`; new `TestSocCeiling`. (1.9.9 -> 1.9.10)

- v2.84: **Shaving day-mode decision log now reports the actual decision time.** The once-daily shave-vs-car-day snapshot (Section 4.2.3) is evaluated on the 15-minute battery-control cycle, so the first tick at/after `shaving_decision_hour` lands up to 15 min past the hour (e.g. 08:12 for an 08:00 hour). The log line previously printed the configured hour (`decided at 08:00`), which misrepresented when the snapshot was taken; it now prints the real local time plus the configured hour: `decided at 08:12 (decision hour 08:00)`. Behaviour and the 15-minute cadence are unchanged. (1.9.8 -> 1.9.9)

- v2.83: **1-phase mode no longer honors `input_number.ev_min_solar_power` (Section 4.3.6 Rule 3).** That gate ("don't start solar charging below X W") is sized for 3-phase, where the minimum step is already 3962 W. In single-phase the whole range is 1380–3680 W (6–16 A), so a 3000 W `ev_min_solar_power` stranded the bottom two-thirds of it — verified live 2026-07-10, where a full sunny day only ever charged in the 13–16 A band. New `solar_start_threshold(phases, ev_min_solar_power, wallbox_min)`: 1φ returns the wallbox minimum (6 A ≈ 1380 W), 3φ returns `ev_min_solar_power` (or the wallbox min as fallback). Selected from `sensor.wallbox_phases`. New `TestSolarStartThreshold`. (1.9.7 -> 1.9.8)

- v2.82: **Solar power-step table is now phase-aware — single-phase charging steps correctly (Section 4.3.7).** The step table `POWER_STEPS_3P` = [3962…7624] is 3-phase M-Bus ground truth, all values ≥ 3962 W. With a **single-phase charging cable** (the wallbox draws only L1, so the max is 3680 W / 16 A), `snap_to_power_step` found no step within the 1φ range and the step-up path even overrode the dynamic range with `POWER_STEPS_3P[0]/[-1]` (3962–7624 W), commanding e.g. 4354 W — which the OCPP server turned into ~19 A on one phase (over the 16 A limit) and could not modulate down. New `POWER_STEPS_1P` = [1380…3680] (230 W/A from live single-phase MeterValues, 6–16 A) and `power_steps_for_phases(phases)` select the table from the OCPP server's detected cable phase count (`sensor.wallbox_phases`, ocpp-server §3.6.4.1). `snap_to_power_step` / `build_solar_candidates` take a `steps=` arg (default 3φ, backward-compatible); the solar path threads the selected table through the candidate, step-up, snap-down and `ev_step_offset` logic. Pairs with ocpp-server 0.9.71 (1φ divisor 230 + `max_current_a` amp cap). New tests: `TestPowerStepsForPhases`, `TestSnapSinglePhase`, `TestBuildSolarCandidatesSinglePhase`. (1.9.6 -> 1.9.7)

- v2.81: **Shaving day now keys on EV fullness alone — connection is no longer a criterion (Section 4.2.3).** The once-daily shave-vs-car-day snapshot required the car to be *connected* (it read `binary_sensor.car_ready`). But a full car reports wallbox status `SuspendedEV`/`Finishing`, for which `car_ready` is **off** (`CAR_READY_MAP` maps both to `False`), so a full car — the *exact* shaving-day premise — read as "no car" and latched a **car day**, charging the battery greedily all morning and exporting the whole midday peak instead of absorbing it. Observed live 2026-07-07: `soc=80 target=80` (full) → car_day; battery filled 39 %→97 % by 10:30 while ~6 kW was exported at the peak. Fix: the premise is now purely `smart_battery_last_known >= smart_charging_max_last_known` (`_car_is_full`), evaluated whether or not the car is plugged in — the EV can come and go, so only its charge level matters (a full car won't need the surplus; a car below target will, so bank the battery greedily). Both sides read the **last-known** sensors (`sensor.smart_battery_last_known` / `sensor.smart_charging_max_last_known`), **not** the volatile `sensor.smart_battery` — which goes `unavailable` when the car is **asleep** (telematics sleep). The cached last-known value stays valid across sleep because a sleeping car does not consume, whereas reading the volatile sensor would force `None` → car day and never shave; a car that is awake and driving still reports, so the departure trigger catches a draining car. The departure trigger (`_car_departed`) likewise fires only on the SOC dropping below target — which the last-known SOC catches as the car drives off and drains, so no connection check is needed and a brief unplug/replug no longer ends the day. `car_ready`/`wallbox_connected`/`wallbox_status` are no longer read for this decision. `TestDayModeDecision` rewired to drive SOC-vs-target only (new `test_full_car_at_decision_is_shaving_day`, `test_car_full_soc_unknown_is_car_day`, `test_reads_last_known_soc_not_volatile`; the removed disconnect-departure and wallbox-status cases are subsumed by the below-target trigger). 253 tests pass. (1.9.5 -> 1.9.6)

- v2.80: **New OFF charging mode — a real user hard-stop (Section 4.3.4 / 4.3.5).** `input_select.ev_charging_mode` already offered an `off` option, but `run.py` treated any value other than `solar`/`immediate`/`cheap` as invalid and forced it back to `solar` on the next ~10 s cycle — so selecting `off` self-reverted (observed live 2026-07-02: `off` → `solar` in 0.5 s). `off` is now a first-class mode: a new `EVState.OFF` and a top-of-`step()` override hold the wallbox at 0 W from any state, ignoring surplus and tariff and giving the SUN2000 full battery control (as in IDLE). Unlike Immediate/Cheap it is **sticky** — never auto-reverted to `solar`; it persists until the user selects another mode (the auto-revert path is unchanged and still scoped to immediate/cheap). On entry to OFF the manual-charge budget is cleared. `run.py`'s mode-validity guard now accepts `off` (still resets genuinely-invalid values to `solar`). `sensor.ev_charge_status` publishes `off`. 6 new tests in `test_ev_state_machine.py` (`TestOffMode`). Note: `src/ev_goal_mode.py` remains dead code (the live path is `EVStateMachine`); not modified. (1.8.44 -> 1.8.45)

- v2.79: **Test cases promoted from Appendix D to Chapter 6 (doc-only).** The test-case specs are a first-class part of the spec, not implementation trivia, so they now live in a numbered chapter (`Chapter 6: Test Cases`, placed after Chapter 5 and before the appendices) rather than an appendix. D.1–D.7 renumbered to 6.1–6.7 (subsections D.6.x/D.7.x → 6.6.x/6.7.x); appendices reserved for implementation-detail reference (config, Smart-car raw API). Updated the testing index (`Harness/project/testing.md`), `STRUCTURE.md`, `Harness/AI-Workflow.md`, and in-doc changelog pointers.

- v2.78: **LoadForecast spec extracted to its own self-contained add-on FSD (doc-only, no code change).** Chapter 3 (profiling algorithm, data source, output schema, configuration, limitations) and the §1.13.3 parameter block moved to `load-forecast/Documents/load-forecast-fsd.md`; this doc keeps a summary + link and the `load_forecast` interface contract EnergyManager consumes. Corrected the output-field contract there to `power_w_p10/p50/p90` + `run_time` (verified against `load-forecast/src/influxdb_writer.py` — the old stub listed `energy_wh_*`; energy is derived by consumers) and recorded the deployed 120 h horizon vs the 48 h code/example default. SwissSolarForecast and LoadForecast are now both self-contained; EnergyManager (Chapter 4) remains in this FSD.

- v2.77: **SwissSolarForecast spec extracted to its own self-contained add-on FSD (doc-only, no code change).** Each add-on is an independently shipped HA app and now owns a complete FSD; Chapter 2 (ICON/STAC pipeline, PV configuration, output schema, calculation pipeline, shading correction) and the §1.13.2 parameter block moved to `swiss-solar-forecast/Documents/swiss-solar-forecast-fsd.md`. This doc keeps a one-paragraph summary + link and the `pv_forecast` interface contract EnergyManager consumes.

- v2.76: **SOC-forecast scenarios renamed to the two decision options + the chosen path (Sections 4.2.1, 4.2.2, 4.5.1).** The `soc_forecast` `scenario` tag carried `with_strategy` / `without_strategy`, where `with_strategy` was actually the *winning* trajectory (the chosen path), not a fixed "discharge held" curve — so the two plotted lines collapsed onto each other whenever holding won nothing, and the names didn't say what each was. The decision is a per-cycle choice between two options: **battery_on** (free discharge) and **battery_off** (hold discharge during cheap hours); `calculate_decision` now returns both pure sims plus **planned** (whichever it selects). All three are written: `battery_on` and `battery_off` are the two candidate options the dashboard compares; `planned` is the realistic path the EV safety gate (`ev_battery.py`) and the forecast snapshot read (was `with_strategy`). Naming is now congruent across code, InfluxDB, and Grafana. Existing InfluxDB history was migrated in place to preserve it: `without_strategy` → `battery_on` and `with_strategy` → `planned` (the old `with_strategy` curve was the winning/chosen trajectory, i.e. `planned`); `battery_off` (the pure-hold sim) has no pre-deploy history because it was never stored as a separate series before. Observability/naming change — discharge actuation and the EV gate read the same trajectory as before. (1.8.43 -> 1.8.44)

- v2.75: **Manual charge (immediate/cheap): user-facing feedback when the target SOC is already met (Section 4.3.4).** Selecting immediate/cheap while the car is already at/above `input_number.ev_target_soc` makes the state machine bounce straight back to IDLE on entry (the SOC stop), and the controller silently reverts the mode to `solar` — so pressing the button looked like nothing happened (observed: car 85 %, target 60 % → instant revert). Now, when the revert is a same-tick entry bounce (i.e. a button press, not the end of a real charge), a Home Assistant **persistent notification** is raised ("EV charge: target already reached — … Raise the EV target SOC to charge.") via a new `HAClient.create_notification` helper (fixed `notification_id`, so repeated presses replace rather than stack). No control-behaviour change — feedback only. (1.8.42 -> 1.8.43)

- v2.74: **EV target gate (Rule 4) now re-anchors the SOC sim to the live SOC every 10 s instead of reading the 15-min `soc_forecast` (Section 4.3.6).** The gate read the `soc_forecast` curve from InfluxDB, which is regenerated only on the 15-min cycle and anchored to the SOC at that cycle. While the car drained the battery, the curve stayed frozen and optimistic, so the car ran ~one forecast period too long before the gate tripped — an overshoot of ≈ `car_power × 15 min` (observed live 2026-06-25: gate tripped at 16:40 with the battery landing ~79 % vs the 90 % target; the ~1.1 kWh shortfall ≈ 4.3 kW × 15 min). New `BatteryOptimizer.reaches_target_today(live_soc, forecast, now, target)` re-runs `simulate_soc` from the **live** SOC over the cached net-energy forecast (`self._latest_forecast`, cached each 15-min cycle) and tests today's peak vs target — evaluated on the 10-s EV loop, so the overshoot shrinks to one 10-s step. This also **removes** the per-10-s InfluxDB read the gate used to do (`will_battery_hit_full` in the EV path); that function now only backs the 15-min dashboard attributes, and the `soc_forecast` write is purely for the dashboard. Only the SOC *simulation* moved to 10 s — all battery actuation decisions (Topic 3/4/5) stay on the 15-min cycle, so no inverter write-rate change and no new rate-limiting needed (the wallbox already has its 30-s min-interval guard). New tests: `reaches_target_today` reaches/not-reached/re-anchor-lowers-peak/empty-fails-closed. (1.8.41 -> 1.8.42)

- v2.73: **Removed the 48 h no-buy-floor veto from the EV charge decision — Rule 4 (target gate) supersedes it (Section 4.3.6).** Topic 1 previously had two home-battery gates: the old "home battery safe" rule (`battery_min_soc_48h >= no_buy_floor`, the 48 h trough) and the v2.72 target gate (`will_battery_hit_full`, today's peak vs target). They protect the same thing, and the target gate is strictly the better one: charging *at/below* surplus never drains the battery (the remainder charges it), the only draining step (Topic 2 step-up) is already gated by the **instantaneous** SOC floor (§4.3.7), and a multi-day 48 h trough is driven by future PV/load — not by the car spending *today's* surplus, which the target gate already protects. So the 48 h veto only ever produced false positives (when the target gate passed) or fired redundantly (when the target gate had already stopped the car). Dropped it: the may-charge rules are now 1–3 + Rule 4 (was Rule 5). The candidate loop no longer calls `check_ev_safe` per power step (it was power-independent anyway, so it never actually stepped down by power) — it takes the highest Topic 2 candidate, which is already safety-filtered by the step-up floor. `battery_min_soc_48h` is still computed for the dashboard and the Topic 2 step-up gate. `check_ev_safe` itself is unchanged (still primes the dashboard cache). Rewrote `test_power_calculation.py` to model the real selection (the old replica tested a power-dependent step-down that production never did). (1.8.40 -> 1.8.41)

- v2.72: **Topic 1 Rule 5 — the home battery's charge target now gates the car (Section 4.3.6).** Previously the only battery protection on the wallbox was the 48 h no-buy floor (20 %, Rule 4); the car could keep eating surplus on a marginal-sun day while the home battery never reached its agreed `battery_target_soc` (e.g. SOC 26 %, target 90 %, car at 4.3 kW). Both gates protect against *the very same situation* — the battery ending the day too low — so they now share the **same `compute_charge_target` survival model** (Section 4.2.4): the car may charge only while `will_battery_hit_full(battery_target_soc)` is true. That forecast is **car-excluded**, so it answers *"if the car stops now, does the battery still reach its target today?"* — and it self-corrects, because while the car charges it suppresses the real SOC, so each 15-min cycle the forecast is recomputed from a lower SOC and the moment the target becomes unreachable the car stops, handing 100 % of the surplus to the battery (which then lands at nearly the target). `will_battery_hit_full` was already computed every cycle for the dashboard — promoted from informational to a gate. The 20 % no-buy floor (Rule 4) is **kept** as a strictly-lower hard backstop. Implemented in the shared `build_solar_candidates` helper (new `target_reachable` arg → no candidates when false), so the gate is unit-tested. Full-battery exception (SOC = 100 %) bypasses it. New tests: target unreachable blocks all charging / overrides step-down / default unchanged. (1.8.39 -> 1.8.40)

- v2.71: **Removed dead `ev_charging.protection_soc_percent` constant.** `self.ev_protection_soc` (default 80) was assigned in `run.py.__init__` but never read anywhere — a leftover from the pre-v2.44 per-EV battery-protection target. Deleted the assignment and the stale line from the example config block. No behaviour change. (1.8.38 -> 1.8.39)

- v2.70: **Topic 4 discharge protection: floor the sim at `reserve_percent` = 10 % (was 0), a forecast-error buffer (Section 4.2.2).** With the floor at 0 the free-discharge sim spends the battery's last few % to cover a small *forecast* morning deficit → ties → discharges overnight; on 2026-06-24 the median load forecast (~280 W) badly undershot the actual (~750 W), so the tiny forecast deficit was "covered" and the battery drained to ~1 %, then the house bought at the expensive morning rate with weak sun. Flooring the discharge sim at 10 % makes free-discharge unable to bank on the bottom slice → hold wins → the battery is kept high overnight, leaving a real buffer for the expensive morning even when the forecast is optimistic. Code default raised 0 → 10 (run.py:130, matching the example config); the **live host config** also moved 0 → 10. Topic 3's charge-target sim is unaffected (it passes `floor_wh=0`). New test: floor 10 holds overnight where floor 0 discharges. (1.8.37 -> 1.8.38)

- v2.69: **Step-up (Topic 2, Section 4.3.7) now also requires the *current* SOC >= no-buy floor.** The step-up gate keyed only off `battery_min_soc_48h`, which excludes the wallbox load and so reads optimistically high while the car drains the real battery — observed live 2026-06-23 with SOC at 12 % but the forecast at 29 %, still firing "snap-up 4354→5117W" and draining the home battery toward ~9 %. Added an instantaneous-SOC condition (`battery_soc >= no_buy_floor_percent`) so step-up no longer deliberately drains the battery below the floor. Topic 1 Rule 4 (may the car charge at all, at/below surplus) is unchanged — by decision it stays forecast-based. New tests: step-up suppressed below floor / allowed at floor. (1.8.36 -> 1.8.37)

- v2.68: **Fixed `battery_charge_discharge_power` sign convention + dashboard card flow display (Sections 1.9, 4.6.3, 4.6.4).** The canonical §1.9 table (and two other spots) had the battery sign **inverted** — verified live by energy balance (deficit day, low SOC, `bp = −724 W` while the car drew 4.3 kW → battery discharging): the true convention is **`+` = charge (from PV), `−` = discharge (to house/car)**. No control impact (the raw sensor is used by nothing in code — display only). Card fixes: the EV card's home-battery line now labels `−` as "adding" (discharging to help the car) and `+` as "charging"; the battery card's **line 1 now reflects the live flow** (`bp`) instead of `charge_action` — "released" means charging is *allowed*, not that it is happening, so on a car day with the car drawing surplus it correctly shows "Powering the house · N kW" rather than the old wrong "Charging from solar"; and the charge-ceiling line drops "shaving headroom" on a car day. Display refinements: the EV card's home-battery flow + amp-step are now **one combined line** ("Home battery 16% · adding 707 W (step +1, covers the gap)"); the battery card says "**Helping charge the car**" instead of "Powering the house" when discharging while the car draws (`wallbox_power` > 100); the ceiling line reads "**Longevity cap N%**" (was "Ceiling N% · longevity cap"). Layout pass: cards are left-aligned blocks (`text-align: left`); the EV card shows the **day-mode line first** (most important context), and the power is a **single line** showing two components that sum to the total — "5.1 kW (4.8 kW surplus + 305 W battery)": battery = live contribution-to-car, surplus = remainder. Grid is never shown (a brief cloud-transient import is folded into surplus). Replaces the separate SOC/step home-battery line. The battery card's "Helping charge the car · N kW" now uses the **same battery-to-car value** as the EV card (was the total discharge), so the two cards agree. Dashboard/docs only; no version bump. (deployed live via lovelace/config/save)

- v2.67: **Topic 5 departure trigger + 90 % charge floor (Sections 4.0, 4.2.3, 4.2.4).** A shaving day now downgrades **one-way to a car day** the moment its premise breaks — the car disconnects (`car_ready = off`) **or** drops below target ("no longer full") — via `_car_departed()` checked every tick after the 08:00 latch. Rationale: a car that was full at 08:00 but then leaves almost always returns needing energy, so the battery should stop *deferring* its fill for an export peak (a bet the midday peak materialises) and instead charge **greedily and immediately**, banking the morning surplus with certainty rather than selling it. One-way: a later full reconnect does not re-arm shaving; an unknown SOC while still connected is not treated as departed (the cached last-known SOC is held). Separately, the general `charge_target_min` floor is raised **80 → 90 %** so the home battery banks more headroom for the car/house before exporting (modest LFP cost). New tests: 5 departure-trigger cases in `test_charge_gate.py`; `test_floor_90_keeps_headroom`. (1.8.35 -> 1.8.36)

- v2.66: **Exposed Topic 3/5 state as HA sensor attributes for the dashboard, and synced the card docs (Sections 4.6.3, 4.6.4).** `sensor.battery_decision` now publishes `battery_target_soc`, `charge_target_enabled`, `battery_target_reason` (Topic 3 charge ceiling, §4.2.4) and `shaving_day_mode` + `shaving_decision_hour` (Topic 5 day-mode latch, §4.2.3); `sensor.ev_target_power` also gains `shaving_day_mode`. The Lovelace cards now show the once-daily car-day/shaving-day context line and the dynamic charge ceiling. The FSD card YAML, attribute tables, examples, and status mappings were brought in sync with the deployed cards — including the EV card's forecast-based ETA line (`car_target_time` / `sensor.smart_charging_max_last_known`) and step line (`ev_step_offset`), which the doc had not previously reflected. No control-behaviour change (advisory display only). (1.8.34 -> 1.8.35)

- v2.65: **Topic 3 charge target — survival trough is now forward-looking (Section 4.2.4).** `compute_charge_target` measured `min_soc` over the whole trajectory including the current SOC, so a battery currently below `no_buy_floor_percent` (normal after the overnight discharge) made `min_soc(100) < reserve` trivially true → spurious "deficit needs full battery" → 100 % target every low-SOC morning, defeating the longevity target/80 % floor. Fixed: the trough is measured only from **today's charging-window end** — the last interval today (searched back from local 23:59) where PV > load (the daily peak / start of overnight discharge); if no surplus remains today, the anchor is now. The deficit-→100 % result now reflects a genuine forward shortfall. New tests: low-current-SOC sunny day (no longer 100 %), genuine forward deficit (still 100 %). (1.8.33 -> 1.8.34)

- v2.64: **Removed all history/rationale from the FSD body (docs hold current functionality only; history lives in this changelog).** Deleted §2.13.11 "Calibration History" (dated change table, 2026-03-20 data boundary, `pv_forecast_retrofitted` retrofit notes), the §2.6 "Calibration note (2026-03-20)" derivation rationale, and assorted historical clauses ("replaces the old/removed…", "no longer rendered", "(2026-03-04 calibration sweep)" provenance). No behaviour change; FSD-only.

- v2.63: **Wording: T3 calibration is rolling, not calendar-weekly (Sections 4.0, 4.2.4; docstrings).** No behaviour change — `_calibration_charge_due` already fires when >`charge_target_full_interval_days` (7) have passed since the SOC last reached >=99% (the clock restarts at each >=99%, by sun or prior calibration). Replaced misleading "weekly" labels; also corrected `_will_fill_today`'s stale "~100%/>=99%" docstring to "reaches the dynamic charge target". (1.8.32 -> 1.8.33)

- v2.62: **Topic 3 charge target floored at 80% (Section 4.2.4).** Even when the 48 h worst-case survival need is lower, the dynamic target is floored to `battery.charge_target_min` (default raised 20 → **80**), so the battery always charges to at least 80% — within the LFP-friendly band (no longevity cost) and keeping headroom available for shaving. The survival math still uses `no_buy_floor_percent` (20%); `charge_target_min` only raises the final target (passed as `compute_charge_target`'s `min_target`, previously fed `no_buy_floor_percent` — the dead `charge_target_min` config is now wired in). Reason string reports `floored to N%` when the floor binds. (1.8.31 -> 1.8.32)

- v2.61: **Topic 5 shave-vs-car-day choice made once per day at a fixed hour (Sections 4.0, 4.2.3).** Previously `_charge_gate_active()` re-evaluated the car state every tick, so shaving engaged the instant the car reached its target (e.g. 15:21 on 2026-06-20) — long after the midday peak, capping the battery at 87% and exporting ~1.7 kWh. Replaced with a daily latch (`_update_shaving_day_mode`): at `shaving_decision_hour` (local, default 08:00) the car is read once — connected & at/above target → shaving day; below target / absent / unknown → car day (battery charges greedily all day). Latched until the next local midnight; does not re-flip when the car fills mid-afternoon. Before the decision hour the default is car day. New config `battery.shaving_decision_hour` (8). (1.8.30 -> 1.8.31)

- v2.60: **Topics grouped and priority tiers documented (Section 4.0).** Three topic groups by managed resource — EV Charging (T1, T2), Battery Management (T3, T4, T5), Appliances (T6). Priority organized in tiers: Tier 0 invariant (no-buy floor); Tier 1 independent topics that each own a distinct control entity and always execute (T3 charge-off, T4 discharge, T6 advisory); Tier 2 the one contended decision — battery charge-timing, where EV (T1/T2) has priority and suppresses T5 shaving when the car is connected and needs energy. (FSD-only; matches deployed 1.8.30)

- v2.59: **Topic 3 corrected to the dynamic charge-target hold (4.2.4); shipped off.** The v2.58 "stop when `battery_min_soc_48h >= floor`" hold under-charged: it read the charge-to-100 forecast, so on a sunny day it deferred all day expecting future charging that the hold itself prevented, then could not cover the evening. Replaced with the dynamic `battery_target_soc` (lowest SOC keeping the worst-case p10/p50 48 h sim >= `no_buy_floor_percent` + margin, via `compute_charge_target`); charge to it, then hold. The target is enforced only by `control_battery_charge` and is **not** threaded into the discharge/EV forecast (`calculate_decision` uses `max_soc_percent=100`), so the EV gate reads the natural trajectory. `charge_target_enabled` default **false** -- Topics 1/2/4 ship live in 1.8.30, Topic 3 enabled after validation. (1.8.29 -> 1.8.30)

- v2.58: **Topics 1, 2, 3 implemented** (energy-manager 1.8.29). Topic 1 (4.3.6): added the **Rule 2** car-needs-charge hard gate (skip solar when `sensor.smart_battery` >= `sensor.smart_charging_max_last_known`); the Rule 4 full-battery exception was already the `battery_full` capture path. Topic 2 (4.3.7): the step-up gate now keys off `battery_min_soc_48h >= no_buy_floor_percent` (replacing the v2.51 `_will_battery_fill_today_with_ev` fills-today gate); `build_solar_candidates(... step_up_allowed=)`. Topic 3 (4.2.4): re-enabled (`charge_target_enabled` default true) as the simple longevity **hold** — stop charging when `battery_min_soc_48h >= no_buy_floor_percent`, weekly 100% calibration overrides; the old `compute_charge_target` worst-case machinery is no longer called (`_battery_target_soc` fixed at 100%). Config: `ev_charging.reserve_percent` -> **`battery.no_buy_floor_percent`** (20%, fallback to the old key). Tests updated; 226 pass. (1.8.28 -> 1.8.29)

- v2.57: **Topic 4 (home-battery discharge) re-based on an expensive-import comparison (Section 4.2.2); EBL tariff calendar pinned in Section 4.2.2.** The discharge decision simulates SOC over 48 h **with_strategy** (hold discharge during cheap slots) and **without_strategy** (free discharge), sums the **expensive-hours grid import (Wh, at SOC=0)** for each, lower wins (tie -> without_strategy). Cheap/expensive labeling = EBL double-tariff: weekday 06:00-21:00 expensive, Sat/Sun + 8 specific EBL holidays cheap, holidays computed in-add-on via `dateutil.easter` (`tariff.holidays` key removed). `battery.reserve_percent` becomes an optional safety margin (default 0). Topics 1-3 unchanged. (FSD-only; code follows)

- v2.56: **EV charging restructured into two numbered rule-topics (Sections 4.3.6, 4.3.7).** The old monolithic 4.3.6 "EV Charging Power Calculation" splits into **4.3.6 EV Charge Decision** (Topic 1 -- four yes/no rules) and **4.3.7 EV Charge Power** (Topic 2 -- step at/below surplus, step-up gated). Both consume one shared, power-independent home-battery check, **`battery_min_soc_48h`** (min of the wallbox-off solar-load SOC sim over 48 h), at a single bar **`battery.no_buy_floor_percent`** (renamed from `ev_charging.reserve_percent`, 20 %, no hysteresis). Three behavioural changes specced here, implemented per-topic in follow-ups: explicit **Rule 2** car-needs-charge gate; **full-battery exception** on Rule 4 (skip the check at 100 % SOC -- blocking would only export curtailed surplus); step-up (4.3.7 Rule 3) now keys off the shared check, replacing the v2.51 EV-aware `_will_battery_fill_today_with_ev` snap-up gate. Smart Car SOC Polling 4.3.7->4.3.8 and Car SOC Forecast 4.3.8->4.3.9; cross-refs updated. Plan cleanups recorded for implementation: C1 delete dead `self.reserve_percent`; C2 make the discharge-floor default explicit (10). (FSD-only; code follows)

- v2.54: Publish `ev_step_offset` on `sensor.ev_target_power` (Section 4.3.6) — the solar-charging step offset vs the surplus-snapped level (+n snap-up / −n stepped down / 0 matched / null), for the EV dashboard card to show whether the home battery is bridging the gap (+) or being preserved (−). (v1.8.25 → v1.8.26)
- v2.55: **Rule 4 (EV charge gate) reimplemented as the natural protection forecast; charge-target disabled.** `check_ev_safe` now gates purely on the home-battery SOC forecast (`solar forecast − load forecast`, wallbox excluded) min ≥ `ev_charging.reserve_percent` over 48 h — no EV-load subtraction (gate is power-independent), no charge-target cap. The wallbox is excluded by design: switching it off is the rule's action and an off wallbox doesn't affect the future; self-corrects every 15 min from the current SOC. The dynamic charge target (§4.2.4) is **disabled by default** because its cap polluted this forecast — root cause of the 2026-06-18 EV cut (forecast capped low → over-blocked) and 2026-06-19 empty battery (forecast stale-high → under-blocked). `EVBatteryOptimizer._extra_load_percent` removed; `test_ev_battery.py` updated. (v1.8.26 → v1.8.27)
- v2.53: Make `battery_target_soc` consistent across **all** calculations (Section 4.2.4 Interactions). It is now computed **first** in `run_optimization` (before the discharge decision and any forecast write) and threaded as a `max_soc_percent` ceiling through `simulate_soc` → `calculate_decision` (discharge decision + the published `with_strategy`/`without_strategy` SOC forecasts) and the car-SOC forecast (house fills to target, then overflow to car). EV safety (`check_ev_safe`) and the dashboard read the now-capped forecast and so become consistent automatically; `will_battery_hit_full` gained a `full_threshold` arg set to the target so "Full by HH:MM" means *reaches target* (a fixed 99% would never be hit against a capped forecast). Previously the target was computed late, so those consumers were optimistic about stored energy. (v1.8.24 → v1.8.25)
- v2.52: New **dynamic charge target** for the home battery (Section 4.2.4). Instead of charging the LFP pack to 100%, a single `battery_target_soc` is computed every 15 min as the lowest SOC ceiling that still keeps the battery above the discharge `reserve` over a worst-case (p10 PV / p50 load) `charge_target_horizon_h` (default 48 h) survival simulation, plus a small `charge_target_margin` (default 10%). No fixed cap — it reaches 100% when the forecast genuinely needs it; it just lands below 100% on most days (less high-SOC dwell → longer LFP life). Enforced **in software** (`number.battery_maximum_charging_power` = 0 at/above the target) — the inverter's native `battery_end_of_charge_soc` accepts only 90–100% and cannot enforce a lower ceiling. The survival floor is a dedicated `charge_target_min` (default 20%), **not** `battery.reserve_percent` (which is 0 and, with `simulate_soc` clamping SOC at 0, would make the check trivial). A calibration override forces 100% if >7 days (`charge_target_full_interval_days`) since the battery last hit ≥99% (LFP BMS SOC drift). Fail-safe: missing/stale forecast fails **up** to 100% (never starves the battery into evening grid import). `battery_target_soc` is consumed by export-peak-shaving (4.2.3, fills to it not 100%) and the EV snap-up gate (4.3.6, "full" = reaches it). New tests `test_charge_target.py`. Enforced in software (charge limit 0 at target) — the native inverter max-SOC accepts only 90–100%; survival floor is a dedicated charge_target_min, not the (zero) discharge reserve. (v1.8.22 → v1.8.24)
- v2.51: EV solar snap-up gate re-keyed from the car's EOD SOC to the **home battery's** fills-today forecast (Section 4.3.6). The snap-up step (drain the home battery to reach the next amp level) is now allowed only when the home battery is still forecast to reach full today *with the EV load subtracted* (`EnergyManager._will_battery_fill_today_with_ev`, cached as `_battery_full_with_ev`, recomputed every 15 min); otherwise the EV stays at-or-below surplus (snap-down only) so it cannot drain a battery that won't recover. Root cause (observed 2026-06-17): the old car-target gate let a low car (18%→49% at 4–7 kW) drain the home battery all day under full sun (bottomed 32%, ended ~43%, never full) because the car-target condition stayed true and the flat 48-h ≥20% floor never tripped. The home-battery load forecast excludes the wallbox, so the new check subtracts the live wallbox draw before re-simulating. `build_solar_candidates` signature changed (`forecast_eod`/`ev_target_max` → `battery_will_be_full`); `TestBuildSolarCandidates` updated. (v1.8.21 → v1.8.22)
- v2.50: Dropped the +10 SOC safety buffer in the manual-charge stop (Section 4.3.5.1). Stop is now symmetric: `car_soc >= target_soc` regardless of freshness. Rationale: if we stop too early the user re-presses the button and a fresh budget is computed from the new lower `start_soc`; if too late no buffer would have helped. Added `car_soc_age_s` input (computed in `run.py` from `sensor.smart_battery.last_updated`) — logged in the stop reason for diagnosis only, not used as a threshold. Test 47 → 60 (`test_target_reached_stops_charging`, `test_target_reached_logs_freshness`). **Validated live 2026-05-18 21:41**: 26%→30%, 3680 Wh delivered, age=1 s, mode auto-reverted to solar. (v1.8.7 → v1.8.8)
- v2.49: Phase 3 — manual-charge kWh budget and SOC stop (new Section 4.3.5.1). CHEAP and IMMEDIATE modes now stop automatically at a user-set target SOC instead of running until the car reaches its own max. On entry the state machine snapshots `start_soc` (`sensor.smart_battery_last_known`) and `start_session_wh` (`sensor.wallbox_energy`); each tick checks `car_soc >= target_soc` (SOC stop) and `delivered_wh >= (target - start) × capacity / η` (kWh budget). Session-energy regression (OCPP transaction restart on unplug/replug) triggers a re-snapshot. `run.py` auto-reverts `input_select.ev_charging_mode` to `solar` on any IMMEDIATE/CHEAP → IDLE transition. New EVInputs fields: `target_soc`, `car_soc`, `session_energy_wh`, `capacity_kwh`, `efficiency`. `smart_car.charge_efficiency` default bumped 0.9 → 0.88. 12 new tests in `test_ev_state_machine.py`. (v1.8.6 → v1.8.7)
- v2.48: Operational polish for the EV control loop. The 10-s loop was spamming ~34k INFO lines/day (apscheduler heartbeats + EV safety line + EV decision line) and firing two InfluxDB queries every cycle purely for dashboard diagnostics. Fixes: (a) silenced `apscheduler.executors.default` and `apscheduler.scheduler` at WARNING — removes the per-cycle "Running job…" chatter and its UTC timestamps; (b) `EVBatteryOptimizer.check_ev_safe` log dropped to DEBUG; (c) short-circuit in `control_ev_charging` — when no EV candidate (surplus < threshold, mode not solar, or wallbox unavailable), skip `check_ev_safe`/`will_battery_hit_full` and read cached values instead (~17k Flux queries/day saved); (d) cache primed in `run_optimization` after the SOC forecast is written, so the dashboard always reflects a real value from the most recent 15-min cycle; (e) single dense INFO log per EV cycle (`EV [state] power  mode=… surplus=…W±threshold  batt=…% [min48h=…%±floor]  src=…`) with dedup — DEBUG on no-change, INFO on state/power/source change, 60-s INFO heartbeat while idle; (f) stray mislabeled `DEBUG:` messages at INFO demoted to actual DEBUG; (g) startup timestamps rendered in Swiss local time via `swiss_datetime()` throughout (`run.py`, `forecast_reader.py`, scheduler next-run line). No FSD-content changes — this is operational only. (v1.8.3 → v1.8.5)
- v2.47: Split the EV safety floor from `battery.reserve_percent`. Added **`ev_charging.reserve_percent`** (default **20 %**) — an independent config option for the 48-h EV safety rule. Rationale: `battery.reserve_percent` = 0 (site default) makes the old shared rule toothless because the simulator clamps SOC at 0, so `min_soc >= 0` is always true. The two floors now answer different questions and evolve independently. Updated Section 4.1.4 (Battery configuration), Section 4.3.6 (Safety rule + "Independence from nightly battery protection"), example YAML, and README. (v1.8.2)
- v2.46: Moved `will_battery_hit_full()` from the main runtime class into `EVBatteryOptimizer` (its only caller). Dropped the misnamed "Shared Forecast Helpers" section — it had no shared helpers left. Folded the `will_battery_hit_full` description into Section 4.3.6 as a dashboard-diagnostic subsection. Renumbered 4.6 → 4.5, 4.7 → 4.6, 4.8 → 4.7 for InfluxDB Storage / Dashboard / Error Handling. Also: `_extra_load_percent` remains a private helper on each consumer class (one line of `wh / capacity_wh × 100`) — not worth extracting. 2 new unit tests covering the moved method. (v1.8.1)
- v2.45: Restructured Chapter 4 into a consumer-oriented layout — each decision block (home battery, EV battery, washer) is now a self-contained section covering its forecasts and rules. New **Section 4.1 Prerequisites** consolidates all shared inputs (PV forecast, load forecast, live SOC, tariff schedule, battery configuration, time/unit conventions) previously scattered across the chapter. Former separate sections collapsed into one per consumer: 4.2 Home Battery (sim + discharge rule), 4.3 EV Battery (state machine + power calc + 48-h safety rule + car SOC polling + car SOC forecast — merges the previous duplicate "## 4.6" numbering bug), 4.4 Washer (Appliance Signal), 4.5 Shared Forecast Helpers. Renumbered downstream: 4.6 InfluxDB Storage, 4.7 Dashboard, 4.8 Error Handling. Documentation-only; no code changes.
- v2.44: Replaced the EV battery-protection gate with a self-correcting 48-h min-SOC rule (Section 4.3.6). Root cause: the old rule targeted `tariff.target`, which during daytime (06:00–21:00) resolved to **tomorrow's** 21:00 — giving the forecast a full extra sun-day of headroom, so `reaches_target` stayed true all day even as the actual battery never climbed past ~48%. New rule: EV is allowed only while the home-battery SOC forecast stays ≥ `battery.reserve_percent` at every point across the next 48 h, with one 15-min slot of the candidate EV load subtracted as worst case. Re-evaluated every 15 min — if the forecast drops below the floor, EV stops and the battery (now EV-free) rides the remaining forecast back up. New module `src/ev_battery.py` (`EVBatteryOptimizer.check_ev_safe`); deleted `check_battery_protection`, `will_battery_hit_minimum`, `get_forecast_soc_at_target`; removed `ev_charging.battery_protection_soc` config (no per-EV target needed). Sensor attrs: `reaches_target`/`battery_will_hit_min`/`battery_forecast_soc` → `ev_safe`/`battery_min_soc_forecast_48h`/`battery_min_soc_floor`. 8 new unit tests (`test_ev_battery.py`); `test_power_calculation.py` `battery_check_fn` signature collapsed from `(bool,bool)` to `bool` (v1.8.0)
- v2.43: Car SOC Forecast (new Sections 4.6.4, 4.6.5) — multi-day prediction of EV SOC written to `energy_balance.car_soc_percent` every 15 min. House battery modelled as buffer: surplus first refills the house, overflow × efficiency goes to the car. New `smart_car.capacity_kwh` and `smart_car.charge_efficiency` config. Last-known-value fallback for `sensor.smart_battery` via InfluxDB (Python side) and `sensor.smart_battery_last_known` trigger-based template sensor (HA side). Grafana BatteryForecast panel 4 "Cumulative Energy Balance (Wh)" replaced by "Car SOC Forecast". Amazon Fire dashboard updated to reference the cached template sensor (v1.7.6)
- v2.42: Added 2% hysteresis to discharge soc_ok threshold (Section 4.3.2) — once blocked, projected min SOC must reach 12% (not 10%) to re-allow; prevents oscillation where shrinking simulation window causes min_soc to wobble ~0.5% around threshold every 15 min, flip-flopping discharge limit between 0W/5000W all night; 3 new tests (§6.1) (v1.6.97)
- v2.41: Both rules use surplus_power (PV − house load) as input (Section 4.6.6) — eliminates grid_export feedback loop; snap-up tries next amp step above surplus if battery protected; surplus smoothing 60s→30s, rate limit 60s→30s; removed stale power floor clamp; updated flowchart, scenarios, and snap description (v1.6.93–v1.6.96)
- v2.40: Added S0/C0/M0 wallbox-unavailable guards to all active state transitions (Section 4.6.5.2); `will_battery_hit_full()` now returns `full_time_local` HH:MM (Section 4.4.2); updated NO-01 test scope (§6.7.1) (v1.6.92)
- v2.39: Added Section 2.13.11 (Calibration History) — documents 2026-03-20 parameter change boundary, retrofitted data in InfluxDB (pv_forecast_retrofitted), and retrofit limitations
- v2.38: Updated Section 2.6 PV System Configuration — calibrated Pdc0 values (E:445W, W:490W, S:425W) and inverter efficiency (0.98) from per-string actual vs clear-sky model on sunny days; these are model calibration parameters, not changes to physical hardware; added calibration note explaining methodology
- v2.37: Added Section 2.13 (Shading Correction) — clear-sky reference model using pvlib.clearsky.ineichen(), per-hour sunny detection (actual GHI / clearsky GHI > 0.85), per-string shading factor calculation, InfluxDB storage schema for accuracy evaluation; replaces previous weather-factor-based approach that suffered from circular dependency
- v2.36: Restructured EV Charging Power Calculation (Section 4.6.6) — two rules based on battery state: Rule 1 (Battery Full) captures grid export, Rule 2 (Solar Surplus Charging) uses surplus with battery protection gate; `snap_to_power_step(available_power_w)` as shared amp-step conversion; decision flowchart; fixed missing power computation when battery full + forecast path (v1.6.85)
- v2.35: Discrete M-Bus power steps (Section 4.6.6) — replaced nominal `amps × 230 × 3` power steps with M-Bus calibrated values from 2026-03-04 sweep; `snap_to_power_step()` replaces `snap_to_amp_step()`; energy-manager works in real-world watts; OCPP server demand calibration converts M-Bus watts to integer amps via `round(W/637)` (v1.6.81, v0.9.47)
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
- v2.26: Passive integration observer test revision (§6.7) — replaced 5 obsolete surplus-tracking tests (NO-03, NO-04, EC-01, EC-10, EC-11) with forecast-strategy-aligned tests (NO-05, NO-13, EC-05, EC-06, EC-07); updated NO-02 preconditions for strategy-based entry; report version bumped to 3; evidence includes `strategy` field
- v2.25: Forecast-based EV solar charging strategy (Section 4.6.7) — replaces instantaneous open-loop/closed-loop excess with SOC simulation; battery acts as buffer for coarse amp steps (690W on 3-phase); bottom-up search from min to max amps; dynamic protection target adapts to bad days; `min_solar_power_w` config for early charging below wallbox minimum; `sensor.surplus_power` for entry decision; renamed `sensor.load_power` → `sensor.house_load_power`; added `sensor.total_load_power` (house + wallbox for Fire display)
- v2.24: Appendix F — Comprehensive Smart Car API reference: full `electricVehicleStatus` field catalogue with all 16 `chargerState` values (from pySmartHashtag/evcc/ioBroker), `statusOfChargerConnection` physical cable states, V2L fields, charging lids, DC charging fields; HA entity mapping table; other `vehicleStatus` sections (climate, doors, maintenance, GPS, 12V battery); poll frequency table
- v2.23: Wallbox idle detection exits all EV modes — added `wallbox_idle` input (Section 4.6.6); S1/C1/M1 transitions exit SOLAR/CHEAP/IMMEDIATE to IDLE when car finishes charging (wallbox idle ≥ 5 min); idle timer extended from immediate/cheap to all modes; dashboard shows `idle_minutes` and `wallbox_idle` attributes; EC-16 passive integration test; IT-BATT-04 test catalogue entry
- v2.22: Integration test catalogue (§6.5/6.6) — 22 tests across 6 categories; 3 implemented (IT-PHASE-01, IT-BATT-01, IT-BATT-03), 19 documented as future; EV charging power tests documented (§6.5)
- v2.21: Added wallbox status display mapping table for dashboard (Section 4.8.1) — documents how raw OCPP status is shown to the user
- v2.20: SOC poll on charging mode change — switching modes (e.g. solar → immediate) triggers immediate SOC refresh for dashboard accuracy (Section 4.6.1)
- v2.19: Adaptive Smart car SOC polling — 1-minute during charging, immediate on car connection, hourly baseline; cached Hello Smart client reduces API calls from 6 to 2 per poll (Section 4.6)
- v2.18: Removed battery protection gate from solar EV charging — solar mode always active when excess available; battery protection is now informational (dashboard only); removed S4 transition from SOLAR state; updated N3 condition (Section 4.6.6)
- v2.17: Two-flag battery discharge blocking — EV charging in immediate/cheap mode now independently blocks battery discharge (Section 4.3.2); prevents SUN2000 from draining battery to cover wallbox load via DTSU correction; 17 new tests (§6.4)
- v2.16: Added sections 4.7 (InfluxDB Storage), 4.8 (Dashboard Examples), 4.9 (Error Handling and Notifications), Chapter 5 (Forecast Accuracy Tracking), Appendix E (EnergyManager Configuration). Updated EV config for 4-state machine (phase-based min power, phase_threshold_kwh). Updated EV decision table to include EV charging forecast dependency.
- v2.15: FSD improvements — signal conventions box; weekend battery guard as explicit policy (`battery_guard_on_weekends`); `allow_1p_auto` flag for 1φ vs 3φ minimum; `effective_min_power_w` derived threshold; S06 forecast contract (measurement, field, staleness, missing=conservative); `battery_guard_margin_pct` (2% safety margin); smoothing defined (rolling median, 4 samples); rate limiting formalized (`setpoint_min_interval_s`, `setpoint_max_step_w`, `setpoint_step_w`); import tolerance (`import_tolerance_w`, `import_tolerance_cycles`); auto-revert trigger refined (only when setpoint > 0 recently); mode reset write-back semantics (one-shot, retry, idempotency); fault required-signals-per-mode; hard fault test-setpoint procedure; anti-flap table; S02 renamed EV_UNAVAILABLE; state priority order; scenario-based test table; edge-case worked examples (H: stale SoC, I: tariff boundary, J: phase gap)
- v2.14: Redesigned EV state machine — states represent charging behavior, not device status (Section 4.6.6); 12 states in 3 groups (base/policy/PV-excess); debounce (S21), hysteresis (200W), cooldown (S24) prevent oscillation; soft/hard fault classification with recovery dwell and anti-flap; battery reserve guard with configurable scope policy; mode renamed to auto_pv_excess/immediate/deferred_tariff; auto-revert on EV finish
- v2.13: Refactored EV charging to transition-based state machine with hysteresis (Section 4.6.6); 200W dead band prevents PAUSED↔SOLAR oscillation; 111 unit tests
- v2.12: Moved all test cases into a dedicated Test Cases chapter (now Chapter 6) with references from main chapters; dashboard button feedback (orange/green); car status card redesign
- v2.11: Appliance signal ORANGE now also triggers on grid export >= 1.5kWh before evening (Section 4.5.2.2)
- v2.10: Expensive hours check now excludes weekend/holiday days (Section 4.3.2, 4.3.3); fixes incorrect discharge blocking on Friday nights
- v2.9: Appliance signal uses min SOC instead of final SOC for ORANGE check (Section 4.5); ensures SOC never dips below threshold at any point in simulation
- v2.8: Dual SOC forecast scenarios (with/without strategy); forecast snapshot for accuracy tracking; updated InfluxDB storage schema (Section 4.7)
- v2.7: Comprehensive EV Charging Optimization specification (Section 4.6) - OCPP 1.6j, phase switching, goal mode
- v2.6: Simplified battery discharge algorithm - rolling 15-minute threshold check; added test cases (Section 4.3.6); appliance signal test cases (Section 4.5.5)
- v2.5: Added Home Assistant API access documentation (homeassistant_api: true, battery entity reading)
- v2.4: Added Chapter 5 - Forecast Accuracy Tracking (Accuracy #1: Battery Discharge Optimization)
