# EnergyManager

Household energy optimizer for PV self-consumption, battery discharge control, EV charging, and appliance signaling.

## Overview

This add-on runs a 15-minute optimization loop that:

- **Battery discharge control** — blocks battery discharge during cheap tariff hours when PV forecast is insufficient, ensuring stored energy covers expensive-hour consumption
- **EV charging** — four operating modes (solar/immediate/cheap/normal) with closed-loop solar tracking via grid meter feedback
- **Appliance signal** — traffic-light signal (GREEN/ORANGE/RED) indicating whether it's a good time to run high-power appliances (dishwasher, washing machine, etc.)
- **Smart car SOC** — reads EV battery level from the Hello Smart API and publishes it as a HA sensor

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      EnergyManager Add-on                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  OPTIMIZER (every 15 min)                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ PV forecast + Load forecast + Battery SOC                     │   │
│  │   ───▶ SOC simulation ───▶ discharge decision                 │   │
│  │   ───▶ appliance signal (GREEN/ORANGE/RED)                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  EV CONTROLLER (every 60 s)                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Grid power + Wallbox power ───▶ solar excess                  │   │
│  │   ───▶ state machine ───▶ wallbox power limit                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  SMART CAR (every 60 min)                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Hello Smart API ───▶ sensor.smart_battery (SOC %)             │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

External dependencies:
  SwissSolarForecast ──▶ InfluxDB (pv_forecast bucket)
  LoadForecast ────────▶ InfluxDB (load_forecast bucket)
  OCPP Server ─────────▶ HA entities (wallbox power/status/limit)
```

## Configuration

Configuration is split between the add-on **Configuration tab** (secrets) and a **YAML file** (everything else).

### Secrets (Configuration tab)

| Option | Description |
|--------|-------------|
| `influxdb_token` | InfluxDB 2.x API token |
| `telegram_bot_token` | Telegram bot token for notifications (optional) |
| `telegram_chat_id` | Telegram chat ID for notifications (optional) |
| `smart_user` | Hello Smart API login email (optional) |
| `smart_password` | Hello Smart API password (optional) |

### YAML Configuration

Edit via File Editor or VS Code at `/addon_configs/energymanager/energymanager.yaml`.

#### InfluxDB

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"
  pv_bucket: "pv_forecast"
  load_bucket: "load_forecast"
  output_bucket: "energy_manager"
  soc_bucket: "HomeData"
  soc_measurement: "Energy"
  soc_field: "BATT_Level"
```

#### Battery

```yaml
battery:
  capacity_kwh: 10.0
  reserve_percent: 10
  charge_efficiency: 0.95
  discharge_efficiency: 0.95
  max_charge_w: 5000
  max_discharge_w: 5000
  soc_entity: "sensor.battery_state_of_capacity"
  discharge_control_entity: "number.battery_maximum_discharging_power"
```

#### Tariff

```yaml
tariff:
  weekday_cheap_start: "21:00"
  weekday_cheap_end: "06:00"
  weekend_all_day_cheap: true
  holidays: []              # List of "YYYY-MM-DD" strings
```

#### Appliances

```yaml
appliances:
  power_w: 2500             # Typical appliance power draw
  energy_wh: 1500           # Energy threshold for ORANGE signal
```

#### Sensors

```yaml
sensors:
  pv_power: "sensor.solar_pv_total_ac_power"
  load_power: "sensor.load_power"
  mbus_grid_power: "sensor.grid_power"              # M-Bus smart meter (preferred)
  dtsu_grid_power: "sensor.power_meter_active_power" # DTSU fallback
  appliance_signal: "sensor.appliance_signal"
```

Grid power prefers the M-Bus smart meter if fresh (< 20 s), falling back to the DTSU meter at the inverter.

#### EV Charging

```yaml
ev_charging:
  enabled: true
  min_power_w: 1400         # Minimum wallbox power (1-phase 6A)
  max_power_w: 11000        # Maximum wallbox power (3-phase 16A)
  target_soc: 80            # Target SOC % for car
  battery_protection_soc: 80 # Block EV until battery forecast reaches this %
  auto_reset_timeout_min: 5 # Minutes idle before auto-revert to solar mode
```

#### Smart Car

```yaml
smart_car:
  enabled: true
  user: ""                  # Hello Smart login email
  vin: ""                   # Auto-detects if empty
  soc_entity: "sensor.smart_battery"
```

## EV Charging Modes

Control via `input_select.ev_charging_mode` in HA:

| Mode | Behavior |
|------|----------|
| **solar** | Track solar excess via closed-loop grid feedback. Battery protection: blocks EV if forecast SOC at 21:00 < 80% |
| **immediate** | Charge at max power immediately |
| **cheap** | Charge at max power during cheap tariff hours, pause during expensive hours |

Immediate and Cheap modes auto-revert to Solar after 5 minutes of 0W (car full or disconnected).

## Appliance Signal

Published as `sensor.appliance_signal` with values:

| Signal | Meaning |
|--------|---------|
| **GREEN** | PV excess above appliance threshold right now |
| **ORANGE** | Battery SOC forecast stays above reserve + appliance threshold, or grid export before evening exceeds energy threshold |
| **RED** | Insufficient energy — defer appliance use |

## HA Entities Created

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.appliance_signal` | sensor | GREEN / ORANGE / RED |
| `sensor.smart_battery` | sensor | Smart car SOC % |
| `sensor.battery_discharge_decision` | sensor | allow / block |

## HACS Installation

1. Add this repository to HACS as a custom repository
2. Install "EnergyManager" add-on
3. Configure secrets in the Configuration tab
4. Copy and edit `energymanager.yaml` in `/addon_configs/energymanager/`
5. Start the add-on

## Troubleshooting

### No forecast data

Verify the SwissSolarForecast and LoadForecast add-ons are running and writing to InfluxDB:

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
  "http://192.168.0.203:8087/api/v2/query?org=energymanagement" \
  --data-urlencode 'query=from(bucket:"pv_forecast") |> range(start:-1h) |> limit(n:1)'
```

### EV not charging in solar mode

1. Check `binary_sensor.wallbox_connected` is `on`
2. Check `sensor.wallbox_status` is `Preparing` or `Charging`
3. Check battery protection: if forecast SOC at 21:00 < 80%, EV is blocked
4. Check grid power sensors are updating (M-Bus < 20s old)

### Appliance signal stuck on RED

- Verify battery SOC entity is updating
- Check PV forecast bucket has data for today
- Check `reserve_percent` isn't set too high
