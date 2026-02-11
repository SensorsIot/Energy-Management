# OCPP Server HA Add-on - Functional Specification Document

**Version:** 1.5 | **Status:** Draft | **Created:** 2026-02-10

## 1. Overview

Home Assistant add-on providing an OCPP 1.6J Central System that bridges EV charging stations to Home Assistant. The add-on exposes wallbox state as HA entities and accepts control via HA services — no MQTT required between the add-on and EnergyManager.

### 1.1 Scope

| In Scope | Out of Scope |
|----------|-------------|
| OCPP 1.6J WebSocket server | Captive portal (HA handles config) |
| HA entity integration (sensors, services) | OTA firmware updates (Docker handles this) |
| Charging profile management | Dual-network isolation |
| Authorization | |
| Transaction management | |
| Phase switching (via EARU latching relay) | |

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Home Assistant                            │
│                                                              │
│  ┌──────────────┐              ┌───────────────────────┐    │
│  │ EnergyManager│──HA states──►│  OCPP Server Add-on   │    │
│  │              │  & services  │                       │    │
│  │  reads:      │              │  • WebSocket :8887    │    │
│  │  sensors     │              │  • OCPP 1.6J handler  │    │
│  │  calls:      │              │  • HA entity provider │    │
│  │  services    │              │  • Phase switch ctrl  │    │
│  └──────────────┘              └───┬───────────┬───────┘    │
│                                    │           │            │
│  ┌─────────────────────┐          │           │            │
│  │ EARU Breaker        │◄─────────┘           │            │
│  │ (ESPHome BK7231N)   │  switch.turn_on/off  │            │
│  │ • Relay: 1φ/3φ      │                       │            │
│  │ • BL0942: V,A,W,kWh │                       │            │
│  │ ON=3φ  OFF=1φ       │                       │            │
│  └─────────┬───────────┘                       │            │
│            │ relay                              │            │
│  Exposed HA entities:                          │            │
│  • sensor.wallbox_power (W)                    │            │
│  • sensor.wallbox_energy (Wh)                  │            │
│  • sensor.wallbox_status                       │            │
│  • sensor.wallbox_phases (1 or 3)              │            │
│  • binary_sensor.wallbox_connected             │            │
│  • number.wallbox_power_limit (W)              │            │
│  • sensor.wallbox_transaction                  │            │
└────────────┼───────────────────────────────────┼────────────┘
             │ L1/L2/L3 switching                │ OCPP 1.6J
             │                                   │ WebSocket
             │                         ┌─────────┴─────────┐
             │                         │     Wallbox        │
             └────────────────────────►│  (AcTec / OCPP)    │
                                       │  • OCPP 1.6J client│
                                       └────────────────────┘
```

### 2.1 Software Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| OCPP library | `ocpp` (Python) |
| WebSocket | `websockets` |
| HA integration | `homeassistant_api` (REST API from add-on) |
| Phase switch | EARU breaker: BK7231N (LibreTiny/ESPHome) + BL0942 energy meter |
| Deployment | HA add-on (Docker, s6-overlay) |

### 2.2 Integration with Project Components

This add-on is part of the Energy-Management project alongside:

| Add-on | Role in EV Charging |
|--------|---------------------|
| **EnergyManager** (v1.5.11) | Reads wallbox sensors, sets `number.wallbox_power_limit`. See [Energymanagement_fsd.md Section 4.5](../../Documents/Energymanagement_fsd.md) |
| **SwissSolarForecast** (v1.2.4) | Provides PV forecast used by EnergyManager to plan charging windows |
| **LoadForecast** (v1.2.3) | Provides load forecast used by EnergyManager to calculate excess power |

**EnergyManager interaction (Section 4.5 of Energymanagement_fsd.md):**

```
EnergyManager reads:                    EnergyManager writes:
  sensor.wallbox_power          →         number.wallbox_power_limit
  sensor.wallbox_energy
  sensor.wallbox_status
  binary_sensor.wallbox_connected
  sensor.wallbox_transaction
  sensor.wallbox_phases
```

The EnergyManager decides charging power every minute based on:
- Current PV production (from SwissSolarForecast sensors)
- Current load (from LoadForecast sensors)
- Battery state (from Huawei inverter sensors)
- Operating mode: opportunistic solar (default) or goal-based

**Difference from ESP32 OCPP Server:** The ESP32 hardware supports GPIO-based phase switching (1-phase/3-phase) directly. This HA add-on achieves the same via an external EARU latching relay controlled through ESPHome. When `phase_switch_entity` is configured, the add-on auto-switches phases based on the requested power limit — no manual intervention needed.

## 3. Configuration

Configured via HA add-on options (`/data/options.json`):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `wallbox_id` | string | `AcTec001` | Expected chargepoint ID |
| `ws_port` | int | 8887 | WebSocket listen port |
| `min_current_a` | int | 6 | Minimum charging current |
| `max_current_a` | int | 16 | Maximum charging current |
| `phase_switch_entity` | string? | `""` | HA switch entity for EARU relay (empty = disabled) |

## 4. Functional Requirements

### 4.1 WebSocket Server

| Property | Value |
|----------|-------|
| Port | 8887 (configurable) |
| Path | `/{chargePointId}` (e.g., `/AcTec001`) |
| Subprotocol | `ocpp1.6` |
| Max connections | 1 |
| Bind | `0.0.0.0` |

The server extracts the chargepoint ID from the WebSocket connection path.

### 4.2 OCPP 1.6J Messages

#### 4.2.1 Incoming (Wallbox → Server)

| Message | Action |
|---------|--------|
| `BootNotification` | Accept, set 60s heartbeat interval |
| `Heartbeat` | Return current UTC time |
| `StatusNotification` | Update status → `sensor.wallbox_status` |
| `Authorize` | Accept all tags |
| `StartTransaction` | Assign transaction ID → update entities |
| `StopTransaction` | Clear transaction → update entities |
| `MeterValues` | Extract power/energy → update sensor entities |

#### 4.2.2 Outgoing (Server → Wallbox)

| Message | Trigger |
|---------|---------|
| `SetChargingProfile` | `number.wallbox_power_limit` changed |
| `RemoteStartTransaction` | Auto: power limit changes from 0 to >0 (no active transaction) |
| `RemoteStopTransaction` | Wallbox-initiated (plug removed) or future explicit mechanism |
| `TriggerMessage` (MeterValues) | Periodic or on demand |

### 4.3 HA Entity Interface

The add-on exposes wallbox state as native HA entities via the Supervisor API. The EnergyManager (or any automation) reads sensors and calls services — no MQTT needed.

#### 4.3.1 Sensor Entities (read by EnergyManager)

| Entity | Type | Unit | Description |
|--------|------|------|-------------|
| `sensor.wallbox_power` | sensor | W | Current charging power |
| `sensor.wallbox_energy` | sensor | Wh | Session energy delivered |
| `sensor.wallbox_status` | sensor | - | `Available` / `Preparing` / `Charging` / `Faulted` |
| `binary_sensor.wallbox_connected` | binary_sensor | - | Wallbox WebSocket connected |
| `sensor.wallbox_transaction` | sensor | - | `idle` / `charging` |
| `sensor.wallbox_phases` | sensor | - | Active phase count: `1` or `3` |

#### 4.3.2 Control Entities (set by EnergyManager)

| Entity | Type | Description |
|--------|------|-------------|
| `number.wallbox_power_limit` | number | Target power in W (min/max from config). The OCPP server auto-starts transactions: setting power >0 starts a transaction if none is active. Setting power to 0 pauses charging (0A profile) but keeps the transaction alive. |

#### 4.3.3 SetChargingProfile and Phase Switching

When `number.wallbox_power_limit` changes:
1. **Auto-transaction management:**
   - Power 0 → >0 (no active transaction): send `RemoteStartTransaction`
   - Power >0 → 0: send `SetChargingProfile` with 0A (pause), transaction stays alive
2. Determine target phases (if `phase_switch_entity` configured):
   - 0 W → keep current phases (pause only)
   - 1–4139 W → 1-phase (relay OFF)
   - ≥ 4140 W → 3-phase (relay ON)
   - Threshold = `min_current_a × 230 × 3` (default 6 × 230 × 3 = 4140 W)
3. If phase change needed, execute safety sequence (see below)
4. Convert power to current: `current_a = power_w / (230 × num_phases)`
5. Clamp to `[min_current_a, max_current_a]` (from config)
6. If power_w = 0: send profile with limit = 0 (pause charging)
7. Send `SetChargingProfile` with `TxDefaultProfile`, `Absolute`, rate unit `Amps`

**Phase switching safety sequence** (EARU latching relay via ESPHome):

| Step | Action | Duration |
|------|--------|----------|
| 1 | Send `SetChargingProfile` with limit = 0 A (pause) | immediate |
| 2 | Wait for current to drop | 2 s |
| 3 | Call `switch.turn_on` (3φ) or `switch.turn_off` (1φ) on relay entity | immediate |
| 4 | Wait for relay to settle | 3 s |
| 5 | Send `SetChargingProfile` with target current and new phase count | immediate |

**Relay mapping:** ON = 3-phase, OFF = 1-phase

If `phase_switch_entity` is empty (default), phase switching is disabled and the add-on assumes 3-phase. On startup, the add-on reads the relay state from HA to initialize `sensor.wallbox_phases`.

#### 4.3.4 EARU Breaker Hardware

The EARU breaker is an ESPHome device with two key components:

| Component | Chip | Interface | Function |
|-----------|------|-----------|----------|
| MCU | BK7231N ([LibreTiny](https://esphome.io/components/libretiny/)) | WiFi / ESPHome native API | Runs ESPHome, exposes entities to HA |
| Energy meter | [BL0942](https://esphome.io/components/sensor/bl0942/) | UART (4800 baud) | Measures V, A, W, kWh, Hz on wallbox feed |
| Relay | Latching relay | GPIO | Switches L2/L3 for 1φ/3φ |

The BL0942 exposes the following ESPHome sensor entities to HA (names depend on user's ESPHome YAML):

| ESPHome sensor | Unit | HA entity example |
|---------------|------|-------------------|
| Voltage | V | `sensor.earu_breaker_voltage` |
| Current | A | `sensor.earu_breaker_current` |
| Power | W | `sensor.earu_breaker_power` |
| Energy | kWh | `sensor.earu_breaker_energy` |
| Frequency | Hz | `sensor.earu_breaker_frequency` |

These sensors are **not consumed by the OCPP Server add-on** — the add-on gets power/energy from the wallbox via OCPP MeterValues. However, the EARU sensors are available in HA for independent verification, dashboards, or EnergyManager use.

### 4.4 Authorization

Current implementation: accept all tags. Future: configurable whitelist.

### 4.5 Transaction Management

Transactions are managed automatically by the OCPP server — no external start/stop commands needed. EnergyManager only sets `number.wallbox_power_limit`.

- **Auto-start:** When power limit changes from 0 to >0 and no transaction is active, the server sends `RemoteStartTransaction`
- **Pause (not stop):** When power limit changes to 0, the server sends `SetChargingProfile` with 0A limit — the transaction stays alive so charging can resume instantly when power becomes available again
- **Transaction end:** Transactions end only when the wallbox initiates `StopTransaction` (e.g., plug removed) or on WebSocket disconnect
- Server assigns incrementing transaction IDs (starting from 1, not persisted across restarts)
- Only one transaction at a time
- On wallbox disconnect: transaction state cleared, entities updated

## 5. Non-Functional Requirements

| Metric | Target |
|--------|--------|
| Startup time | < 5 seconds |
| OCPP response | < 1 second |
| Entity update latency | < 500ms |
| Memory | < 100MB RSS |
| Uptime | Match HA uptime |

## 6. Test Cases

| ID | Test | Expected |
|----|------|----------|
| TC-01 | Wallbox connects via WebSocket | BootNotification accepted, `wallbox_connected` = on |
| TC-02 | Power limit 0 → >0 (no transaction) | Auto `RemoteStartTransaction`, then `SetChargingProfile` |
| TC-03 | Power limit >0 → 0 (active transaction) | `SetChargingProfile` 0A sent, transaction stays alive (no RemoteStopTransaction) |
| TC-04 | Power limit change (transaction active) | `SetChargingProfile` sent, no start/stop |
| TC-05 | MeterValues received | `sensor.wallbox_power` and `sensor.wallbox_energy` update |
| TC-06 | Wallbox disconnect | `wallbox_connected` = off, transaction cleared |
| TC-07 | Power limit = 0 (no transaction) | Profile set to 0A, no `RemoteStopTransaction` |
| TC-08 | Invalid power limit | Clamped to min/max, no crash |
| TC-09 | Multiple wallbox connect attempts | Only one connection active |
| TC-10 | Power limit < 4140 W with phase_switch_entity set | Relay OFF, 1-phase charging, `sensor.wallbox_phases` = 1 |
| TC-11 | Power limit ≥ 4140 W with phase_switch_entity set | Relay ON, 3-phase charging, `sensor.wallbox_phases` = 3 |
| TC-12 | Phase switch safety: pause → relay → resume | SetChargingProfile(0A) sent before relay toggle, 2s + 3s delays observed |
| TC-13 | phase_switch_entity empty (default) | No relay calls, 3-phase assumed, no `_switch_phases` invoked |

## 7. File Structure

```
ocpp-server/
├── config.yaml              # HA add-on manifest
├── Dockerfile               # Container build
├── requirements.txt         # Python dependencies
├── run.py                   # Entry point: OCPPServer class
├── src/
│   ├── ha_entities.py       # HA entity definitions (sensors, controls)
│   └── ocpp_handler.py      # ChargePointHandler (OCPP message handlers)
├── rootfs/
│   └── etc/s6-overlay/...   # s6 service definition
├── tests/
│   └── test_ocpp_handler.py # Unit tests
└── docs/
    └── this file
```

## 8. Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| WebSocket server | ✅ Done | Port 8887, ocpp1.6 subprotocol |
| OCPP message handling | ✅ Done | 7 incoming + 4 outgoing |
| HA entity integration | ✅ Done | Supervisor REST API, auto-transaction management |
| Phase switching | ✅ Done | EARU relay via ESPHome, auto based on power limit |
| Unit tests | ✅ Done | 9 tests |
| HA add-on deployment | ✅ Done | Tested on HA instance, s6-overlay service starts |
| Wallbox integration test | ✅ Done | Tested with AcTec EV-AC22K (FW V1.17.9), 3-phase charging at 5kW |

## 9. Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-10 | Initial FSD, based on ESP32 OCPP Server FSD v1.4 |
| 1.1 | 2026-02-10 | Replaced MQTT interface with native HA entities and services |
| 1.2 | 2026-02-10 | Added Section 2.2: integration with EnergyManager, SwissSolarForecast, LoadForecast |
| 1.3 | 2026-02-10 | Added phase switching via EARU breaker: config, sensor, safety sequence, test cases |
| 1.4 | 2026-02-11 | Removed button entities, auto-transaction management (start/stop driven by power limit) |
| 1.5 | 2026-02-11 | Fix: 0W pauses charging (0A profile) instead of stopping transaction. Tested with real AcTec wallbox. |
