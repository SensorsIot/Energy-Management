# OCPP Server

OCPP 1.6j WebSocket server for EV wallbox control via Home Assistant entities.

## Overview

This add-on provides:

- **OCPP 1.6j WebSocket server** on port 8887 for wallbox communication
- **HA entity bridge** — wallbox state exposed as sensors, charging controlled via number entity
- **Phase switching** — automatic 1-phase/3-phase switching via EARU relay
- **Calibrated current** — measured power-to-current lookup table for accurate charging control
- **Power throttling** — rate-limits SetChargingProfile commands to prevent wallbox oscillation

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                     OCPP Server Add-on                         │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│  WebSocket Server (port 8887)                                  │
│  ┌────────────────────────────────────────────────────────┐   │
│  │ Wallbox ◄──OCPP 1.6j──▶ ChargePointHandler             │   │
│  └────────────────────────────────────────────────────────┘   │
│                          │                                     │
│                          ▼                                     │
│  HA Entity Manager (REST API)                                  │
│  ┌────────────────────────────────────────────────────────┐   │
│  │ sensor.wallbox_power/energy/status/phases               │   │
│  │ binary_sensor.wallbox_connected                         │   │
│  │ number.wallbox_power_limit  ◄── EnergyManager writes    │   │
│  └────────────────────────────────────────────────────────┘   │
│                          │                                     │
│  MQTT (optional)         │                                     │
│  ┌────────────────────────────────────────────────────────┐   │
│  │ Publishes wallbox power to ESP32 Modbus Proxy           │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                                │
└───────────────────────────────────────────────────────────────┘
```

## Configuration

All options are configured in the add-on **Configuration tab**.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `wallbox_id` | string | `AcTec001` | Expected charge point identifier |
| `ws_port` | int | `8887` | WebSocket server listen port |
| `min_current_a` | int | `6` | Minimum charging current (A) |
| `max_current_a` | int | `16` | Maximum charging current (A) |
| `phase_switch_entity` | string | `""` | HA switch entity for EARU relay (empty = disabled) |
| `single_phase_supported` | bool | `false` | Wallbox supports 1-phase charging |
| `power_update_interval_s` | int | `60` | Throttle interval (s) for SetChargingProfile |
| `mqtt_host` | string | `192.168.0.203` | MQTT broker host (optional) |
| `mqtt_port` | int | `1883` | MQTT broker port (optional) |
| `mqtt_topic` | string | `wallbox` | MQTT topic for ESP32 Modbus Proxy (optional) |

## HA Entities

### Sensors (wallbox state, read by EnergyManager)

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.wallbox_power` | W | Current charging power |
| `sensor.wallbox_energy` | Wh | Session energy delivered |
| `sensor.wallbox_status` | — | Available, Preparing, Charging, SuspendedEV, SuspendedEVSE, Finishing, Faulted |
| `sensor.wallbox_transaction` | — | idle / charging |
| `sensor.wallbox_phases` | — | Active phase count (1 or 3) |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| `binary_sensor.wallbox_connected` | WebSocket connection to wallbox |
| `binary_sensor.wallbox_single_phase_supported` | Config flag for phase selection logic |

### Controls (written by EnergyManager)

| Entity | Range | Description |
|--------|-------|-------------|
| `number.wallbox_power_limit` | 0–11000 W | Target charging power. 0 = pause, >0 = charge. Triggers SetChargingProfile via OCPP |

**Important:** This entity is created via REST API. Use `POST /api/states/` to set its value, not `number.set_value` (which silently fails on REST-created entities).

## Phase Switching

When `phase_switch_entity` is configured and power changes cross the threshold (`min_current_a * 230V * 3`):

1. Pause charging (set 0 A)
2. Wait 2 s for current to drop
3. Toggle relay (ON = 3-phase, OFF = 1-phase)
4. Wait 3 s for relay to settle
5. Resume with new phase count

## Power Throttling

The `power_update_interval_s` setting (default 60 s) prevents rapid SetChargingProfile commands that can cause wallbox oscillation. When EnergyManager changes the power limit multiple times within the interval, only the latest value is sent after the interval elapses.

## Wallbox Connection

The wallbox connects via WebSocket to `ws://<ha-ip>:8887/<wallbox_id>`. The OCPP subprotocol `ocpp1.6` is required.

## HACS Installation

1. Add this repository to HACS as a custom repository
2. Install "OCPP Server" add-on
3. Configure wallbox ID and port
4. Point your wallbox's OCPP backend URL to `ws://<ha-ip>:8887/<wallbox_id>`
5. Start the add-on

## Troubleshooting

### Wallbox not connecting

- Verify the wallbox OCPP backend URL matches `ws://<ha-ip>:8887/<wallbox_id>`
- Check that port 8887 is not blocked by a firewall
- Verify the wallbox supports OCPP 1.6j with WebSocket transport

### Power limit not taking effect

- Check `binary_sensor.wallbox_connected` is `on`
- Check `sensor.wallbox_status` is `Charging` or `Preparing`
- Check `power_update_interval_s` — changes are throttled (default 60 s)
- Check add-on logs for SetChargingProfile response status
