# OCPP Server

![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green)
![Supports armv7](https://img.shields.io/badge/armv7-yes-green)
![License](https://img.shields.io/badge/license-MIT-blue)

OCPP 1.6j WebSocket server for EV wallbox control via Home Assistant entities.

## Features

- **OCPP 1.6j Server**: WebSocket server on port 8887 for wallbox communication
- **HA Entity Bridge**: Wallbox state exposed as sensors, charging controlled via number entity
- **Phase Switching**: Automatic 1-phase/3-phase switching via EARU relay
- **Calibrated Current**: Measured power-to-current lookup table for accurate charging
- **Smart Throttling**: Sends power changes immediately if idle >60s, throttles only rapid consecutive changes
- **MQTT Integration**: Publishes wallbox power to ESP32 Modbus Proxy for grid meter correction

## How It Works

1. Wallbox connects via WebSocket (`ws://<ha-ip>:8887/<wallbox_id>`)
2. OCPP messages update HA sensor entities (power, energy, status, phases)
3. EnergyManager writes to `number.wallbox_power_limit`
4. OCPP Server translates power limit to SetChargingProfile commands
5. Phase switching and transaction management handled automatically

## Installation

1. Add this repository to Home Assistant:
   ```
   https://github.com/SensorsIot/Energy-Management
   ```

2. Install "OCPP Server" from the Add-on Store

3. Configure wallbox ID and point your wallbox OCPP backend to `ws://<ha-ip>:8887/<wallbox_id>`

## Configuration

All options are in the add-on **Configuration tab**:

| Option | Default | Description |
|--------|---------|-------------|
| `wallbox_id` | `AcTec001` | Expected charge point identifier |
| `ws_port` | `8887` | WebSocket server listen port |
| `min_current_a` | `6` | Minimum charging current (A) |
| `max_current_a` | `16` | Maximum charging current (A) |
| `phase_switch_entity` | `""` | HA switch entity for EARU relay (empty = disabled) |
| `single_phase_supported` | `false` | Wallbox supports 1-phase charging |
| `power_update_interval_s` | `60` | Throttle interval (s) — immediate if last change >60s ago |
| `mqtt_host` | `192.168.0.203` | MQTT broker host (optional) |
| `mqtt_port` | `1883` | MQTT broker port (optional) |
| `mqtt_topic` | `wallbox` | MQTT topic for Modbus Proxy (optional) |

## HA Entities

### Sensors (read by EnergyManager)

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.wallbox_power` | W | Current charging power |
| `sensor.wallbox_energy` | Wh | Session energy delivered |
| `sensor.wallbox_status` | — | Available / Preparing / Charging / SuspendedEV / Finishing / Faulted |
| `sensor.wallbox_transaction` | — | idle / charging |
| `sensor.wallbox_phases` | — | Active phase count (1 or 3) |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| `binary_sensor.wallbox_connected` | WebSocket connection to wallbox |
| `binary_sensor.wallbox_single_phase_supported` | Config flag for phase selection |

### Controls (written by EnergyManager)

| Entity | Range | Description |
|--------|-------|-------------|
| `number.wallbox_power_limit` | 0–11000 W | 0 = pause, >0 = charge |

## Specification

See [OCPP Server FSD](Documents/ocpp-server-fsd.md) for the full design, configuration model, Home Assistant entity contract, and operational notes.

## Related Add-ons

- **EnergyManager**: Energy optimizer that writes wallbox power limits
- **SwissSolarForecast**: PV power forecast for solar excess calculation
- **LoadForecast**: Statistical load prediction

## License

MIT License - See LICENSE file for details.
