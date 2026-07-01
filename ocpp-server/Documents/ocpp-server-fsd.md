# OCPP Server HA Add-on - Functional Specification Document

## 1. Overview

Home Assistant add-on providing an OCPP 1.6J Central System that bridges EV charging stations to Home Assistant. Exposes wallbox state as HA entities and accepts control via a single power limit entity.

### 1.1 Scope

| In Scope | Out of Scope |
|----------|-------------|
| OCPP 1.6J WebSocket server | OTA firmware updates |
| HA entity integration | Captive portal |
| Charging profile management | |
| Authorization, transaction management | |
| Phase switching (EARU latching relay) | |

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Home Assistant                            │
│                                                              │
│  ┌──────────────┐              ┌───────────────────────┐    │
│  │ EnergyManager│──HA states──►│  OCPP Server Add-on   │    │
│  │              │  & services  │                       │    │
│  │  reads:      │              │  • WebSocket :8887    │    │
│  │  car_ready   │              │  • OCPP 1.6J handler  │    │
│  │  power/energy│              │  • Phase switch ctrl  │    │
│  │  writes:     │              │                       │    │
│  │  power_limit │              │                       │    │
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
└────────────┼───────────────────────────────────┼────────────┘
             │ L1/L2/L3 switching                │ OCPP 1.6J
             │                                   │ WebSocket
             │                         ┌─────────┴─────────┐
             └────────────────────────►│     Wallbox        │
                                       │  (AcTec / OCPP)    │
                                       └────────────────────┘
```

### 2.1 Software Stack

Build stack and deployment are HOW — see the Harness:
[`Harness/project/modules/ocpp-server.md`](../../Harness/project/modules/ocpp-server.md).

### 2.2 Wallbox States

The wallbox reports its state via OCPP `StatusNotification`. These states drive all server decisions.

#### 2.2.1 State Reference

| Status | Car plugged | Current flowing | Transaction | Description |
|--------|:-----------:|:---------------:|:-----------:|-------------|
| `Available` | No | No | No | Connector free |
| `Preparing` | Yes | No | No | Car plugged in, onboard charger initializing (up to 7 min cold start) |
| `Charging` | Yes | Yes | Yes | Active power delivery, MeterValues every ~60s |
| `SuspendedEVSE` | Yes | No | Yes | Paused by charger (0A profile sent). After AcTec correction: always charger-initiated. |
| `SuspendedEV` | Yes | No | Yes | Car refusing to charge. **Synthesized** by OCPP server — AcTec never reports this natively (see Section 3.6.1). |
| `Finishing` | Yes | No | No | Transaction ended, car still plugged |

#### 2.2.2 Observed Transitions (AcTec EV-AC22K + Smart #5)

```
                    plug in                    RemoteStart + car ready
  Available ──────────────► Preparing ──────────────────────────────► Charging
      ▲                         ▲                                     │  ▲
      │ unplug                  │ car wakes                 0A profile│  │ >0A profile
      │                         │                                     ▼  │
  Finishing ◄───────────── Finishing                          SuspendedEVSE
                           (StopTxn)                         ▲    │
                                                     raw≠25  │    │ cloud: raw=25/4
                                                              │    ▼
                                                          SuspendedEV
```

#### 2.2.3 Server Behavior per State

| Status | `car_ready` | Power | Phase switch | Re-send | Cloud check |
|--------|:-----------:|:-----:|:------------:|:-------:|:-----------:|
| `Available` | off | Zeroed | Allowed | — | — |
| `Preparing` | on | Zeroed | Allowed | — | — |
| `Charging` | on | MeterValues | **Blocked** | — | — |
| `SuspendedEVSE` | on | Zeroed | Allowed | **Throttled** | **Poll** → if raw=25/4 → SuspendedEV |
| `SuspendedEV` | off | Zeroed | Allowed | Stopped | **Poll** → if raw≠25/4 → SuspendedEVSE |
| `Finishing` | off | Zeroed | Allowed | — | — |

**Power zeroing:** On any status ≠ `Charging`, the server sets `sensor.wallbox_power` to 0W (wallbox does not send 0W MeterValues when paused).

**Re-send logic:** In `SuspendedEVSE` with last sent > 0W, retries at 10s, 30s, 60s intervals. Cloud check runs in parallel — if car-initiated stop confirmed, corrects to `SuspendedEV` and stops retries.

**Keep-alive pulse:** None. The AcTec wallbox maintains sessions indefinitely at 0W in SuspendedEVSE without periodic pulses, so the server sends none.

## 3. Functional Requirements

### 3.1 WebSocket Server

| Property | Value |
|----------|-------|
| Port | 8887 (configurable) |
| Path | `/{chargePointId}` |
| Subprotocol | `ocpp1.6` |
| Max connections | 1 |
| Keepalive | WebSocket-level ping disabled (`ping_interval=None`); link liveness is tracked via the OCPP `Heartbeat` instead. Avoids dropping wallboxes that don't answer WS pings. |

### 3.2 Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `wallbox_id` | string | `none` | Expected chargepoint ID |
| `ws_port` | int | 8887 | WebSocket listen port |
| `min_current_a` | int | 6 | Minimum charging current |
| `max_current_a` | int | 16 | Maximum charging current |
| `wallbox_type` | enum | `three_phase` | One of `three_phase` (no phase switching), `external_breaker` (server drives EARU relay), `universal` (wallbox handles phase switching natively). Drives the derived `single_phase_supported` flag (`true` for `external_breaker` and `universal`). |
| `phase_switch_entity` | string? | `""` | HA switch entity for EARU relay. Used only when `wallbox_type=external_breaker`. |
| `power_update_interval_s` | int | 60 | Throttle interval for SetChargingProfile. 0W bypasses (safety-critical). |
| `current_sensor_entity` | string? | `sensor.earu_breaker_current` | EARU BL0942 current sensor (safety gate for phase switching) |
| `cloud_charging_entity` | string? | `""` | HA entity exposing the cloud charging-status raw value used for AcTec SuspendedEVSE correction (typically `sensor.smart_charging_status_raw_value`). Empty disables the correction. |

### 3.3 OCPP Messages

**Incoming (Wallbox → Server):**

| Message | Action |
|---------|--------|
| `BootNotification` | Accept, set 60s heartbeat |
| `Heartbeat` | Return UTC time |
| `StatusNotification` | Update `sensor.wallbox_status` |
| `Authorize` | Accept all tags |
| `StartTransaction` | Assign transaction ID |
| `StopTransaction` | Clear transaction |
| `MeterValues` | Sum per-phase power → `sensor.wallbox_power`. Energy-only messages (e.g. `Sample.Clock` at 15-min boundaries) update energy but do **not** change power. **Stale filter:** readings with wallbox timestamps older than 5 minutes are dropped (the Actec replays its internal meter-log queue after reconnects, e.g. DST reboots, delivering readings from hours/days ago). |

**Outgoing (Server → Wallbox):**

| Message | Trigger |
|---------|---------|
| `SetChargingProfile` | Power limit changed, or before RemoteStart |
| `RemoteStartTransaction` | After SetChargingProfile, when no active transaction |
| `TriggerMessage` (MeterValues) | On connect (sync state) |

**Not used:** `RemoteStopTransaction` (causes Finishing), `Reset` (causes reboot + stale Finishing).

### 3.4 Authorization

Accept all tags. Future: configurable whitelist.

### 3.5 Transaction Management

Transactions are fully automatic — EnergyManager only sets `number.wallbox_power_limit`.

**Start sequence** (order critical for AcTec):

| Step | Command | Notes |
|------|---------|-------|
| 1 | `SetChargingProfile` (target amps, TxDefaultProfile) | Must precede RemoteStart |
| 2 | Wait 3s | Let wallbox apply profile |
| 3 | `RemoteStartTransaction` (idTag, connector_id=1) | Rejected without prior profile |

**Pause:** SetChargingProfile 0A → SuspendedEVSE. Transaction stays alive.

**Resume:** SetChargingProfile target amps → Charging. No new RemoteStart needed.

**Stop:** 0A profile → SuspendedEVSE. Transaction ends only on car unplug (StopTransaction from wallbox).

**Connection/reconnect:**
- Wallbox may not send BootNotification on reconnect (only on power-up)
- On reconnect after server restart: `StopTransaction` with `reason=PowerLoss`
- If server dies while charging, wallbox continues autonomously with last profile

**General rules:**
- Transaction IDs increment from 1, not persisted across restarts
- `TxDefaultProfile` does not reference transaction ID — charging control works without recovery
- One transaction at a time
- On disconnect: transaction cleared, entities updated

### 3.6 External Interface

The OCPP server exposes HA entities as its external interface. All OCPP details, phase switching, transactions, and device quirks are hidden.

#### 3.6.1 OCPP Server → Consumers (read-only)

**Static values** — from configuration, do not change during operation:

| Wallbox type | `min_power_w` | `max_power_w` |
|-------------|-------------:|-------------:|
| 1-phase only | 1380W | 3680W |
| 3-phase only | 4140W | 11040W |
| Switchable (1+3) | 1380W | 11040W |

**Dynamic values:**

| Entity | Type | Unit | Description |
|--------|------|:----:|-------------|
| `binary_sensor.car_ready` | binary | — | Can I charge? on = car plugged + system ready + server synced |
| `sensor.wallbox_power` | sensor | W | Actual power (0 when not charging) |
| `sensor.wallbox_energy` | sensor | Wh | Session energy since transaction start |

**`car_ready` derivation:**

| Wallbox state | `car_ready` | Reason |
|--------------|:-----------:|--------|
| Server initializing | off | Not yet synced |
| `Available` | off | No car |
| `Preparing` | on | Car plugged in, ready |
| `Charging` | on | Active charging |
| `SuspendedEVSE` | on | Paused by us, can resume |
| `SuspendedEV` | off | Car refusing to charge |
| `Finishing` | off | Transaction ended |

**AcTec SuspendedEVSE correction:** AcTec reports `SuspendedEVSE` for both charger- and car-initiated stops (firmware bug). The server corrects this:

1. `SuspendedEVSE` + last sent 0W → genuine pause → stays `SuspendedEVSE`
2. `SuspendedEVSE` + last sent > 0W → poll the entity configured as `cloud_charging_entity` (typically `sensor.smart_charging_status_raw_value`):
   - Raw 25 (user-stopped) or 4 (complete) → correct to `SuspendedEV`, stop retries
   - Otherwise → continue retries
3. In `SuspendedEV` → keep polling. If raw changes away from 25/4 → back to `SuspendedEVSE`

If `cloud_charging_entity` is empty the correction is disabled and `SuspendedEVSE` is reported as-is.

Cloud lags 3–10 min behind OCPP — throttled retries bridge the gap.

**Dashboard-only entities** (not part of control interface):

| Entity | Description |
|--------|-------------|
| `sensor.wallbox_status` | OCPP status (with AcTec correction) |
| `sensor.wallbox_transaction` | `idle` or `charging` |
| `sensor.wallbox_phases` | `1` or `3` |
| `binary_sensor.wallbox_connected` | WebSocket alive |

#### 3.6.2 Consumers → OCPP Server (write)

| Entity | Type | Unit | Range |
|--------|------|:----:|-------|
| `number.wallbox_power_limit` | number | W | 0 to max_power_w |

| Value | Action |
|-------|--------------------|
| `0` | Pause immediately (bypasses throttle) — 0A `SetChargingProfile`, wallbox goes `SuspendedEVSE` |
| `min–max` | Charge — server selects phases, converts to amps, manages transaction |
| Gap (3681–4139W) | Stay on current phase, clamp to nearest boundary |

The current limit (including `0`) is **re-applied to the wallbox on every (re)connect**, not only on change. A wallbox resumes at its minimum current after a WebSocket reconnect, so a `0` (pause) that is not re-asserted would silently become a minimum-current charge; re-applying it keeps the commanded state authoritative across reconnects.

#### 3.6.3 Initialization Sequence

`car_ready` stays off until wallbox state is confirmed. EnergyManager sees off and skips EV control. Stages run top-to-bottom on every connect **and reconnect** (`_post_connect_setup`). ("Stage", not "phase", to avoid collision with the electrical 1φ/3φ *phases* in §3.6.4.)

| Stage | What happens | `car_ready` |
|-------|-------------|:-----------:|
| **Init** | Read last-known state from HA entities | off |
| **State-sync** | Wait for StatusNotification from wallbox | off |
| **Inner-sync** | Only if Charging: wait for MeterValues to recover power/energy/transaction | off |
| **Active** | Derive car_ready from status, accept power commands, re-apply the current `number.wallbox_power_limit` (including `0`/pause) | per table |

#### 3.6.4 Phase Switching

Behavior depends on `wallbox_type`:

| `wallbox_type` | Phase switching |
|----------------|-----------------|
| `three_phase` | Disabled. Server clamps to the 3-phase range (4140–11040W); requests below 4140W are zeroed. |
| `external_breaker` | Server drives the EARU latching relay via `phase_switch_entity`. Detailed flow below. |
| `universal` | Wallbox manages phases natively; server passes the requested power through and reports observed phase count. |

The remainder of this section describes the `external_breaker` flow (the only mode where the server actively switches phases). Consumer sends power; server decides phases.

**Power ranges** (6A min, 16A max, 230V):

| Phases | Min | Max |
|:------:|----:|----:|
| 1-phase | 1380W | 3680W |
| 3-phase | 4140W | 11040W |
| Gap | 3681W | 4139W |

Non-overlapping ranges provide natural hysteresis.

**Decision table:**

| Requested | Current | Action |
|-----------|:-------:|--------|
| 0W | any | Pause, stay on current phase |
| 1380–3680W | 1φ | Stay |
| 1380–3680W | 3φ | Switch to 1φ (if time lock allows) |
| Gap | any | Stay, clamp to current phase boundary |
| 4140–11040W | 3φ | Stay |
| 4140–11040W | 1φ | Switch to 3φ (if time lock allows) |

**Time lock:** 5 min after phase switch. During lock, clamp to current phase range. Battery/grid absorb mismatch.

**Safety sequence** (before relay toggle):

| Step | Action |
|------|--------|
| 1 | Send 0A profile (pause) |
| 2 | Wait for phase-switch-allowed status (up to 5s) |
| 3 | Verify BL0942 current < 0.5A |
| 4 | Abort if step 2 or 3 fails — disable single-phase for session |
| 5 | Toggle relay (ON=3φ, OFF=1φ) |
| 6 | Wait 3s for relay settle |
| 7 | Send target profile with new phase count |

#### 3.6.5 Internal Responsibilities

| Responsibility | Details |
|----------------|---------|
| OCPP protocol | W→A demand calibration (`round(W/637)`), meter power correction (linear regression), SetChargingProfile, RemoteStart |
| Transactions | Auto-start on >0W, end on unplug |
| Re-send | Throttled retries (10s, 30s, 60s) in SuspendedEVSE |
| Phase switching | Phase selection, relay safety sequence, time lock |
| Throttle | Rate-limit SetChargingProfile (0W bypasses) |
| Device quirks | AcTec: SuspendedEVSE bug, integer-only amps, profile-before-start |

#### 3.6.6 Modbus-proxy power feed (MQTT)

The server publishes the wallbox's active power to MQTT topic `wallbox` (config `mqtt_host` /
`mqtt_port` / `mqtt_topic`) for the ESP32 Modbus Proxy, which adds it to the Huawei DTSU meter so the
SUN2000 sees grid demand including the wallbox (wired outside the DTSU loop). The last value is
re-published every 10 s to prevent staleness. The proxy applies the value to the inverter within
~1 s (≈1 Hz RS485 serve); HA's own read of the corrected DTSU (every ~30 s) is downstream display
only and not in this loop.

**The correction is the commanded power, not the measured MeterValues.** Measured readings are
unusable as the correction source: ~60 s cadence, the first post-resume reading is a tiny ramp value
(~120 W) that falls below the proxy's activation threshold, and it then sits stale through the car's
whole ramp. So the published value is:

- The **commanded** power (`_last_sent_power_w`) the entire time a charge is commanded — injected the
  instant the command is sent while the car is in an active session (`Charging` or `SuspendedEVSE` =
  warm resume / amp change), and on reaching `Charging` for the cold-start path. This signals the
  SUN2000 the full load immediately, so it stops charging the home battery and covers the car. If the
  car starts late, the inverter briefly **exports** (sells) — deliberately preferred over
  under-reading and **importing** (buying). Commanded also slightly overstates the actual draw in
  steady state (integer-amp flooring), keeping the bias toward export.
- `0` on a confirmed stop: a commanded `0` pause, `SuspendedEV`, `Finishing`, `Available`, or
  transaction end — so a car that refuses to charge (`SuspendedEV`) produces no phantom load.
- **Cold start excluded:** a `>0` command during `Preparing` (car drawing 0 for up to ~7 min, §5.1)
  does **not** inject — it would export for minutes. The correction starts only when that car reaches
  `Charging`.

The measured MeterValues still drive the `sensor.wallbox_power` HA entity (display); they do not
affect the proxy correction.

## 4. EARU Breaker Hardware

| Component | Chip | Function |
|-----------|------|----------|
| MCU | BK7231N (LibreTiny/ESPHome) | WiFi, exposes entities to HA |
| Energy meter | BL0942 (UART 4800 baud) | V, A, W, kWh on wallbox feed |
| Relay | Latching relay (GPIO) | Switches L2/L3 for 1φ/3φ |

BL0942 sensors are available in HA for dashboards but **not consumed by the OCPP server** — power/energy comes from OCPP MeterValues.

## 5. Observed Behavior

### 5.1 Smart #5 Car Timing (verified 2026-02-18)

| Event | Timing |
|-------|--------|
| Cold start (Preparing → Charging) | ~7 min |
| Warm resume (SuspendedEVSE → Charging) | ~4s |
| Power ramp to full | ~60s after Charging |
| Pause (0A → SuspendedEVSE) | ~2s |

`RemoteStartTransaction` rejected in `Finishing` state — wait for `Preparing`.

### 5.2 Smart Cloud API vs OCPP

| Finding | Detail |
|---------|--------|
| Cloud lag | 3–10 min behind OCPP |
| Short pauses | Invisible to cloud |
| SOC updates | Slower than status |
| Stop detection | ~3–4 min to recognize car stopped |
| Rate limiting | 30s polling causes auth failures; use 60s+ |

**Cloud `charging_status_raw_value` mapping:**

| Raw | Label | Meaning |
|-----|-------|---------|
| 2 | charging | Actively AC charging |
| 4 | complete | Target SOC reached |
| 25 | unknown | User-stopped or idle |

**smarthashtag polling** (HA → Settings → Integrations → Smart → Configure):

| Setting | Default | Recommended |
|---------|---------|-------------|
| `scan_interval` | 300s | 300s |
| `charging_interval` | 30s | 60s |
| `driving_interval` | 60s | 60s |

Manual refresh: `homeassistant.update_entity` on any smarthashtag entity.

### 5.3 AcTec Sample.Clock MeterValues (verified 2026-03-03)

The AcTec wallbox sends a `Sample.Clock` MeterValues message at every 15-minute boundary (`:00`, `:15`, `:30`, `:45`). This message contains **only** `Energy.Active.Import.Register` — no Power, Current, or Voltage measurands. The message arrives at `:XX:47` (consistently ~13s before the clock boundary).

The OCPP server must **not** update `sensor.wallbox_power` from these messages, because the absence of Power measurands would incorrectly zero the reported power. Energy is still updated normally.

### 5.4 AcTec SuspendedEVSE Bug

AcTec always reports `SuspendedEVSE` regardless of whether the charger or car initiated the stop. The OCPP server corrects this using cloud status (see Section 3.6.1).

## 6. Non-Functional Requirements

| Metric | Target |
|--------|--------|
| Startup time | < 5s |
| OCPP response | < 1s |
| Entity update latency | < 500ms |
| Memory | < 100MB RSS |
| Log retention | Rotating file `/config/ocpp-server.log` (5 MB × 5 ≈ 25 MB), persists across restarts |

Logging goes to both the console (s6/journal, wiped on restart) and a rotating file on the add-on's `addon_config` volume (`/config/ocpp-server.log`, host path `/addon_configs/<slug>/ocpp-server.log`) so connection events survive restarts for post-mortem. The directory is overridable via the `OCPP_LOG_DIR` env var; file logging is skipped gracefully if the path is unwritable.

## 7. Meter Power Correction

### 7.1 Linear Regression (v0.9.46)

OCPP MeterValues over-report power compared to M-Bus ground truth.
Corrected at source in `on_meter_values()` using a single linear formula:

```
corrected = 0.962115 × raw + 105.6
```

Regression on 2026-03-04 sweep (6–14A), max residual ≈ 33W (<0.5%).

| Amps | OCPP W | M-Bus W (actual) | Error W |
|-----:|-------:|-----------------:|--------:|
|    6 |   3999 |             3962 |     -37 |
|    7 |   4438 |             4354 |     -84 |
|    8 |   5175 |             5117 |     -58 |
|    9 |   5829 |             5727 |    -102 |
|   10 |   6447 |             6288 |    -159 |
|   11 |   7211 |             7034 |    -177 |
|   12 |   7848 |             7624 |    -224 |
|   13 |   8520 |             8303 |    -217 |
|   14 |   9245 |             9029 |    -216 |

15A/16A excluded (solar noise during measurement).

The corrected power is published to `sensor.wallbox_power` as an integer (rounded for display).

### 7.2 Demand Calibration (v0.9.47)

The energymanager sends demand values in M-Bus watts (the actual power delivered at each amp level). The OCPP server converts these to integer amps using a calibrated divisor:

```
limit_a = round(power_w / 637)
```

The divisor 637 is the midpoint of the safe range [612, 662], derived from the M-Bus calibration sweep. Each M-Bus power value maps to the correct integer amp, without relying on the wallbox's internal
flooring:

| M-Bus W | W / 637 | round() | Correct A |
|--------:|--------:|--------:|----------:|
| 3962 | 6.22 | 6 | 6 ✓ |
| 4354 | 6.83 | 7 | 7 ✓ |
| 5117 | 8.03 | 8 | 8 ✓ |
| 5727 | 8.99 | 9 | 9 ✓ |
| 6288 | 9.87 | 10 | 10 ✓ |
| 7034 | 11.04 | 11 | 11 ✓ |
| 7624 | 11.97 | 12 | 12 ✓ |

## 8. Test Cases

This section is the canonical home for OCPP-server test-case specs; it is indexed in the testing hub
`Harness/project/testing.md`.

| ID | Test | Expected |
|----|------|----------|
| TC-01 | Wallbox connects via WebSocket | First message accepted, `wallbox_connected` = on |
| TC-02 | Power limit 0→>0 (no transaction) | SetChargingProfile → 3s → RemoteStart → Charging |
| TC-03 | Power limit >0→0 (active transaction) | 0A profile → SuspendedEVSE, transaction alive, power=0 |
| TC-04 | Power limit change (transaction active) | SetChargingProfile only |
| TC-05 | MeterValues during transaction | Per-phase power summed → wallbox_power |
| TC-05b | Energy-only MeterValues (Sample.Clock) | Energy updated, power unchanged (not zeroed) |
| TC-05c | Stale MeterValues (timestamp > 5 min old) | Dropped with log message, no state update |
| TC-06 | Wallbox disconnect | connected=off, transaction cleared |
| TC-07 | Phase switch: <4140W | Relay OFF, 1-phase, phases=1 |
| TC-08 | Phase switch: ≥4140W | Relay ON, 3-phase, phases=3 |
| TC-09 | Phase switch safety | 0A → wait status → verify BL0942 < 0.5A → toggle relay |
| TC-10 | Rapid power changes (<60s) | Only last value sent when interval expires |
| TC-11 | 0W during throttle | Sent immediately, queue cleared |
| TC-12 | Reconnect after server restart | StopTransaction(PowerLoss), previous session closed |
| TC-13 | Full charge cycle | Start → Charge → Pause → Resume → Stop |
| TC-14 | HA restart with active wallbox | Entities re-registered, state re-synced |

## 9. File Structure

File structure and build stack are HOW — see [`Harness/project/modules/ocpp-server.md`](../../Harness/project/modules/ocpp-server.md).

## Appendix A — Wallbox Power Calibration (AcTec EV-AC22K)

Measured 2026-02-19, OCPP `chargingRateUnit=W`, 3-phase, firmware v0.9.26.

The wallbox accepts watts in `SetChargingProfile` but internally converts to integer amps by flooring `demand_w / 3 / 230`. This creates a staircase response where different demand values map to the same output.

| Demand (W) | Wallbox meter (W) | Car meter (W) | Car (A) | Internal A |
|-----------|------------------|--------------|--------|-----------|
| 4000 | 3931 | 4182 | 5.9 | 6 |
| 4500 | 3934 | 4185 | 5.9 | 6 |
| 5000 | 4313 | 4613 | 6.5 | 7 |
| 5500 | 4316 | 4536 | 6.4 | 7 |
| 6000 | 5108 | 5236 | 7.4 | 8 |
| 6500 | 5696 | 6007 | 8.5 | 9 |
| 7000 | 6418 | 6646 | 9.4 | 10 |
| 7500 | 6368 | 6626 | 9.4 | 10 |
| 8000 | — | 7402 | 10.5 | 11 |
| 8500 | — | 8117 | 11.5 | 12 |
| 9000 | 8471 | 8735 | 12.4 | 13 |
| 9500 | 8475 | 8724 | 12.4 | 13 |
| 10000 | 9176 | 9486 | 13.5 | 14 |
| 10500 | 9860 | 10227 | 14.5 | 15 |
| 11000 | 9869 | 10164 | 14.4 | 15 |

**Notes:**
- Wallbox meter reads ~5–8% lower than car meter (Smart #1 onboard charger losses).
- 8000/8500W wallbox meter readings were anomalous (caught during meter transition).
- Max effective current is 15A. To reach 16A, demand must be ≥ 11040W (`16 × 3 × 230`).
- Adjacent demand values landing on the same integer amp produce identical output.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0–1.9 | 2026-02-10–12 | Initial FSD through AcTec wallbox verification |
| 2.0–2.4 | 2026-02-12–16 | Post-connect setup, throttle, integer amps, HA restart recovery |
| 2.5–2.7 | 2026-02-17 | Phase switching safety (dual gate), 0W bypass, EnergyManager contract |
| 2.8–2.9 | 2026-02-18 | Smart #5 live session data, cloud API timing, raw value mapping |
| 2.10 | 2026-02-18 | Wallbox state reference table, transitions diagram |
| 3.0 | 2026-02-18 | Major interface redesign: car_ready, static min/max, initialization sequence, phase switching with time lock |
| 3.1 | 2026-02-18 | Simplified FSD: removed duplicate sections, condensed session logs, consolidated entity interface |
| 3.2 | 2026-03-02 | Document keep-alive pulse behavior in SuspendedEVSE (existing code, previously undocumented) |
| 3.3 | 2026-03-03 | Section 5.3: AcTec Sample.Clock energy-only MeterValues handling; TC-05b |
| 3.4 | 2026-03-03 | Simplified keep-alive pulse: fixed-duration sleep instead of MeterValues sync (tunable `KEEPALIVE_PULSE_DURATION_S`) |
| 3.5 | 2026-03-03 | Keep-alive pulse waits for Charging StatusNotification instead of fixed sleep; reverts immediately after confirmation |
| 3.6 | 2026-03-24 | Removed keep-alive pulse — Actec confirmed to maintain sessions at 0W indefinitely without periodic nudging (tested 2026-03-24) |
| 3.7 | 2026-03-29 | Stale MeterValues filter: drop readings with wallbox timestamps > 5 min old. Actec replays buffered meter-log queue after reconnects (e.g. DST reboot), causing energy counter jumps that corrupt daily statistics. TC-05c |
| 3.8 | 2026-04-27 | Config refactor: `wallbox_type` enum (`three_phase`/`external_breaker`/`universal`) replaces `single_phase_supported` bool; new optional `cloud_charging_entity` makes the AcTec SuspendedEVSE cloud-correction source explicit. Consolidated two duplicate FSD copies into a single canonical document under `Documents/`. |
| 3.9 | 2026-06-30 | Modbus-proxy power feed: post-resume ramp bridge (§3.6.6). The MQTT `wallbox` value feeding the ESP32 proxy now uses the commanded power on `→Charging` until the first MeterValues>0, instead of the wallbox's stale `0 W` during the ~60 s ramp — closing the window where the DTSU correction dropped out and the grid silently supplied the car (visible as M-Bus-vs-DTSU grid divergence). Bridge ends on first real reading or a confirmed stop (`SuspendedEV`/`Finishing`/`Available`/pause), so a refusing car makes no phantom load. ocpp-server 0.9.61; 4 tests (`TestProxyRampBridge`). |
| 3.10 | 2026-06-30 | Modbus-proxy feed → **commanded-primary** (§3.6.6). Live MQTT measurement showed 3.9 collapsed after ~4 s: the bridge handed off on the first MeterValues (~120 W), which falls below the proxy's activation threshold, leaving the correction off for the ~60 s ramp. Now the correction is the **commanded** power the whole time a charge is commanded — injected the instant the command is sent during an active session (`Charging`/`SuspendedEVSE`), not waiting for `→Charging` (was ~9 s) and never handed to the measured value. Cold-start (`Preparing`) excluded to avoid minutes-long export. Biases to export-not-import per design. Measured still drives `sensor.wallbox_power` display only. ocpp-server 0.9.62; tests `TestProxyCommandedCorrection`. |
