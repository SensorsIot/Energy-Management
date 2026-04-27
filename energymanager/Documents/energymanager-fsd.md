# EnergyManager Add-on - Functional Specification

**Last updated:** 2026-04-27

## 1. Purpose

EnergyManager is the household energy optimization add-on. It combines PV forecasts, load forecasts, live Home Assistant state, tariff windows, battery constraints, and wallbox state to decide when to charge/discharge the home battery, charge the EV, and signal whether high-power appliances should run.

## 2. Scope

In scope:

- Home battery SOC simulation and discharge blocking.
- EV charging mode control for `off`, `solar`, `immediate`, and `cheap`.
- Appliance signal generation.
- Smart car SOC polling and last-known behavior support.
- InfluxDB output for decisions, SOC forecasts, and diagnostics.
- Home Assistant control of battery and wallbox entities.

Out of scope:

- Producing PV forecasts. That is owned by SwissSolarForecast.
- Producing load forecasts. That is owned by LoadForecast.
- OCPP protocol handling. That is owned by OCPP Server.

## 3. Architecture

EnergyManager runs as a Home Assistant add-on with Home Assistant API access enabled. It reads configuration from add-on options and `/addon_configs/energymanager/energymanager.yaml`, reads forecasts from InfluxDB, reads live state from Home Assistant, and writes both Home Assistant control entities and InfluxDB measurements.

Primary modules:

| Module | Responsibility |
|--------|----------------|
| `run.py` | Main add-on orchestration and scheduling |
| `src/forecast_reader.py` | Reads PV, load, and historical battery data from InfluxDB |
| `src/soc_simulator.py` | Simulates forward home-battery SOC |
| `src/battery_optimizer.py` | Decides whether battery discharge should be allowed |
| `src/ev_state_machine.py` | EV charging state machine |
| `src/ev_charging.py` | EV charging power calculation |
| `src/ev_battery.py` | EV/home-battery interaction and safety checks |
| `src/appliance_signal.py` | Appliance traffic-light signal |
| `src/ha_client.py` | Home Assistant REST API client |
| `src/ev_goal_mode.py` | EV charging mode (`solar` / `immediate` / `cheap`); auto-reverts to `solar` after the car stops drawing current for a configurable timeout |
| `src/influxdb_writer.py` | Writes EnergyManager measurements |
| `src/sanity.py` | Runtime bounds checks on power sensor readings |
| `src/integration_observer.py` | Passive observer of the 10-second EV control loop; tracks 23 integration test cases and persists results to JSON |
| `src/notifications.py` | Telegram notification sender |

## 4. Runtime Behavior

The add-on has two main loops:

| Loop | Cadence | Responsibility |
|------|---------|----------------|
| Optimization loop | 15 minutes | Read forecasts, simulate home-battery SOC, decide battery discharge control, write appliance signal, refresh EV safety forecast |
| EV control loop | 10 seconds | Read live surplus power and wallbox/car state, apply EV charging mode and power limit |

Smart car SOC polling is adaptive:

- More frequent while charging.
- Immediate on connection or mode change.
- Less frequent while idle/asleep to reduce API load.

## 5. Configuration

Secrets are configured in the Home Assistant add-on Configuration tab:

| Secret | Purpose |
|--------|---------|
| `influxdb_token` | InfluxDB read/write access |
| `telegram_bot_token` | Optional Telegram notifications |
| `telegram_chat_id` | Optional Telegram destination |

Non-secret configuration is stored in `/addon_configs/energymanager/energymanager.yaml`.

Important configuration areas:

| Section | Purpose |
|---------|---------|
| `influxdb` | Hosts, buckets, org, and non-secret connection settings |
| `battery` | Capacity, reserve, efficiency, SOC entity, discharge-control entity |
| `ev_charging` | Min/max charging power, reserve floor, wallbox behavior |
| `sensors` | HA entity IDs for PV, load, grid, and surplus |
| `tariff` | Cheap/expensive tariff windows |

## 6. Home Assistant Interface

Inputs commonly read from HA:

| Entity | Purpose |
|--------|---------|
| `sensor.battery_state_of_capacity` | Current home-battery SOC |
| `sensor.surplus_power` | Solar surplus used for EV charging |
| `input_select.ev_charging_mode` | EV charging mode |
| `input_boolean.ev_charge_now` | Immediate EV charge override |
| `input_boolean.ev_goal_charge` | EV goal-charge workflow |

Outputs commonly written to HA:

| Entity | Purpose |
|--------|---------|
| `number.battery_maximum_discharging_power` | Blocks/allows home-battery discharge |
| `number.wallbox_power_limit` | EV wallbox power limit |
| `sensor.appliance_signal` | Appliance run/defer signal |

## 7. InfluxDB Interface

Inputs:

| Bucket | Measurement | Purpose |
|--------|-------------|---------|
| `pv_forecast` | `pv_forecast` | PV forecast from SwissSolarForecast |
| `load_forecast` | `load_forecast` | Load forecast from LoadForecast |
| Home Assistant / energy buckets | battery/load/grid measurements | Historical and actual state comparison |

Outputs:

| Bucket | Measurement | Purpose |
|--------|-------------|---------|
| `energy_manager` | `soc_forecast` | Forward SOC curve |
| `energy_manager` | `discharge_decision` | Battery discharge decision |
| `energy_manager` | `appliance_signal` | Appliance signal and reason |
| `energy_manager` | `energy_balance` | Energy balance diagnostics |
| `energy_manager` | `soc_forecast_snapshot` | Forecast snapshot for accuracy tracking |

## 8. Dependencies

- Home Assistant OS/add-on runtime.
- InfluxDB 2.x.
- SwissSolarForecast add-on.
- LoadForecast add-on.
- OCPP Server add-on when EV charging is enabled.
- Huawei battery/inverter entities or compatible replacements.
- Optional Telegram bot.
- Optional Smart car integration/API credentials.

## 9. Failure Handling

Expected behavior:

- Missing forecast data should degrade decisions conservatively.
- Missing HA token prevents HA control writes and is logged.
- Battery control writes use retry/error handling.
- Smart car API failures should not block core home-battery decisions.
- Notifications are optional and must not stop optimization.

## 10. Tests

Primary tests live in `energymanager/tests/`.

Coverage areas:

- Battery optimizer and discharge blocking.
- EV charging state machine.
- EV power calculation.
- Appliance signal logic.
- Integration observer checks.
- Sanity/invariant checks.

## 11. Related Documentation

- Repository-level system FSD: `Documents/EnergymanagementV2_fsd.md`
- Home installation documentation: `Documents/Home-Installation-fsd.md`
- Related add-ons: SwissSolarForecast, LoadForecast, OCPP Server
