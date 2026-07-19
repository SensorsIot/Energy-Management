# EnergyManager

![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green)
![Supports armv7](https://img.shields.io/badge/armv7-yes-green)
![License](https://img.shields.io/badge/license-MIT-blue)

Household energy optimizer for PV self-consumption, battery discharge control, EV charging, and appliance signaling.

## Features

- **Battery Discharge Control**: Blocks discharge during cheap tariff hours when the PV forecast is insufficient, so stored energy covers expensive-hour consumption
- **EV Charging (3 modes)**: Solar surplus, immediate, and cheap-tariff. Solar mode gives the home battery priority to reach its daily target; immediate and cheap stop automatically when the user-set target SOC or computed kWh budget is reached.
- **Appliance Signal**: Traffic-light signal (GREEN/ORANGE/RED) indicating whether to run high-power appliances
- **Smart Car SOC**: Reads EV battery level from the Hello Smart API and publishes as a HA sensor
- **SOC Simulation**: Forward-looking battery state-of-charge simulation using PV and load forecasts
- **InfluxDB Output**: Writes decisions and SOC forecasts for Grafana dashboards

## How It Works

1. **Every 15 minutes**: fetches PV and load forecasts from InfluxDB, simulates home-battery SOC forward, decides whether to block discharge, computes the appliance signal, and updates the forecast-based EV strategy inputs
2. **Every 10 seconds**: reads live `sensor.surplus_power` (PV − house_load), evaluates the EV charging rule, and sets the wallbox power limit. The 48-hour minimum-SOC forecast constrains upward power steps but is not a may-charge veto; a separate live forecast check pauses the car when the home battery cannot reach its daily target
3. **Adaptive Smart car SOC polling**: every 60 s while charging, immediately on car connection or mode change, every 60 min otherwise. The authenticated client is cached (2 requests per cached poll vs 6 for full re-auth)

## Installation

1. Add this repository to Home Assistant:
   ```
   https://github.com/SensorsIot/Energy-Management
   ```

2. Install "EnergyManager" from the Add-on Store

3. Configure secrets in the Configuration tab and edit `energy-manager.yaml`

## Configuration

Secrets (InfluxDB token, Telegram, Smart car credentials) are entered in the add-on **Configuration tab**. All other settings go in `/addon_configs/energy-manager/energy-manager.yaml`.

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
  min_power_w: 1400        # 1-phase 6A
  max_power_w: 11000       # 3-phase 16A
```

The 48-hour minimum-SOC forecast does not disable solar charging. Solar mode starts when surplus is
available and the home battery can still reach its computed daily target. The controller may use
the home battery as a buffer for the wallbox's coarse current steps only while both current and
forecast SOC stay above `battery.no_buy_floor_percent`.

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
| **solar** | Charge from available solar surplus while the home battery can still reach its computed daily target. The 48-hour SOC floor constrains upward power steps but is not itself a may-charge veto. No EV target-SOC cap — runs until the car is full, surplus drops out, or home-battery priority pauses it. |
| **immediate** | Charge at `input_number.ev_manual_power` regardless of tariff. Stops at the target SOC (see Manual Charge). |
| **cheap** | Charge at `input_number.ev_manual_power` during cheap tariff, 0 W during expensive. Stops at the target SOC (see Manual Charge). |

### Manual Charge (immediate / cheap)

The user sets a target SOC via `input_number.ev_target_soc`. On press of **Cheap Charge** or **Charge Now**, the state machine snapshots `start_soc` (`sensor.smart_battery_last_known`) and the wallbox session energy (`sensor.wallbox_energy`). Two stops run in parallel each tick — whichever fires first ends the session:

- **SOC stop** — `car_soc ≥ target_soc`. Symmetric, no buffer.
- **kWh budget** — `delivered_wh ≥ (target_soc − start_soc) × capacity_kwh × 1000 / η`. Primary protection against a stale car SOC.

When either stop fires the mode auto-reverts to `solar`. Pressing the same button again while the mode is active toggles it off (revert to `solar`).

Required `smart_car` config:

```yaml
smart_car:
  enabled: true
  capacity_kwh: 17.6        # EV battery usable capacity
  charge_efficiency: 0.88   # AC-to-battery efficiency
```

## Appliance Signal

Published as `sensor.appliance_signal`. Reuses the home-battery SOC simulation and subtracts the appliance energy:

| Signal | Meaning |
|--------|---------|
| **GREEN** | Current PV excess exceeds appliance power — run on pure solar |
| **ORANGE** | Running now would *not* force grid import before 21:00 (battery covers it) |
| **RED** | Running now would force grid import before 21:00 — defer |

## Requirements

- **SwissSolarForecast** add-on (PV forecasts in InfluxDB)
- **LoadForecast** add-on (load forecasts in InfluxDB)
- **OCPP Server** add-on (wallbox control, if EV charging enabled)
- InfluxDB 2.x
- Huawei inverter with battery (or compatible)

## Specification

See [EnergyManager FSD](Documents/energy-manager-fsd.md) for the full design, configuration model, control contracts, and operational notes.

## Related Add-ons

- **SwissSolarForecast**: PV power forecast using MeteoSwiss weather data
- **LoadForecast**: Statistical load prediction for consumption forecasting
- **OCPP Server**: OCPP 1.6j wallbox server for EV charging control

## License

MIT License - See [LICENSE](../LICENSE).
