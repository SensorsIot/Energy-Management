# EnergyManager

![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green)
![Supports armv7](https://img.shields.io/badge/armv7-yes-green)
![License](https://img.shields.io/badge/license-MIT-blue)

Household energy optimizer for PV self-consumption, battery discharge control, EV charging, and appliance signaling.

## Features

- **Battery Discharge Control**: Blocks discharge during cheap tariff hours when PV forecast is insufficient, ensuring stored energy covers expensive-hour consumption
- **EV Charging (4 modes)**: Solar excess tracking (closed-loop), immediate, cheap-tariff, and normal (off)
- **Appliance Signal**: Traffic-light signal (GREEN/ORANGE/RED) indicating whether to run high-power appliances
- **Smart Car SOC**: Reads EV battery level from Hello Smart API and publishes as HA sensor
- **SOC Simulation**: Forward-looking battery state-of-charge simulation using PV and load forecasts
- **InfluxDB Output**: Writes decisions and SOC forecasts for Grafana dashboards

## How It Works

1. **Every 15 minutes**: Fetches PV and load forecasts from InfluxDB, simulates battery SOC forward, decides whether to block discharge, computes appliance signal
2. **Every 60 seconds**: Reads grid power and wallbox state, calculates solar excess, adjusts wallbox power limit via closed-loop control
3. **Every 60 minutes**: Reads Smart car SOC from Hello Smart API

## Installation

1. Add this repository to Home Assistant:
   ```
   https://github.com/SensorsIot/Energy-Management
   ```

2. Install "EnergyManager" from the Add-on Store

3. Configure secrets in the Configuration tab and edit `energymanager.yaml`

## Configuration

Secrets (InfluxDB token, Telegram, Smart car credentials) are entered in the add-on **Configuration tab**. All other settings go in `/addon_configs/energymanager/energymanager.yaml`.

### Battery

```yaml
battery:
  capacity_kwh: 10.0
  reserve_percent: 10
  charge_efficiency: 0.95
  discharge_efficiency: 0.95
  soc_entity: "sensor.battery_state_of_capacity"
  discharge_control_entity: "number.battery_maximum_discharging_power"
```

### EV Charging

```yaml
ev_charging:
  enabled: true
  min_power_w: 1400       # 1-phase 6A
  max_power_w: 11000      # 3-phase 16A
  target_soc: 80
  battery_protection_soc: 80
```

### Sensors

```yaml
sensors:
  pv_power: "sensor.solar_pv_total_ac_power"
  load_power: "sensor.load_power"
  mbus_grid_power: "sensor.grid_power"
  dtsu_grid_power: "sensor.power_meter_active_power"
```

## EV Charging Modes

Control via `input_select.ev_charging_mode`:

| Mode | Behavior |
|------|----------|
| **solar** | Track solar excess via grid meter feedback. Battery protection blocks EV if forecast SOC at 21:00 < 80% |
| **immediate** | Charge at max power |
| **cheap** | Charge at max during cheap tariff, pause during expensive |

## Appliance Signal

Published as `sensor.appliance_signal`:

| Signal | Meaning |
|--------|---------|
| **GREEN** | PV excess above appliance threshold right now |
| **ORANGE** | Battery forecast stays above reserve threshold |
| **RED** | Insufficient energy — defer appliance use |

## Requirements

- **SwissSolarForecast** add-on (PV forecasts in InfluxDB)
- **LoadForecast** add-on (load forecasts in InfluxDB)
- **OCPP Server** add-on (wallbox control, if EV charging enabled)
- InfluxDB 2.x
- Huawei inverter with battery (or compatible)

## Related Add-ons

- **SwissSolarForecast**: PV power forecast using MeteoSwiss weather data
- **LoadForecast**: Statistical load prediction for consumption forecasting
- **OCPP Server**: OCPP 1.6j wallbox server for EV charging control

## License

MIT License - See LICENSE file for details.
