# LoadForecast Add-on - Functional Specification

**Last updated:** 2026-04-27

## 1. Purpose

LoadForecast generates a statistical forecast of household electrical load from historical Home Assistant data stored in InfluxDB. The forecast is consumed by EnergyManager for home-battery and EV charging decisions.

## 2. Scope

In scope:

- Read historical load data from InfluxDB.
- Build time-of-day load profiles.
- Generate P10/P50/P90 forecast bands.
- Write 15-minute forecast points to InfluxDB.
- Run on a configurable cron schedule.

Out of scope:

- PV forecasting.
- Battery optimization.
- Direct Home Assistant entity control.

## 3. Architecture

LoadForecast runs as a Home Assistant add-on. It reads a user configuration file, merges the InfluxDB token from add-on options, builds a forecast, writes output to InfluxDB, then sleeps until the next cron schedule.

Primary modules:

| Module | Responsibility |
|--------|----------------|
| `run.py` | Main loop, config loading, cron scheduling |
| `src/load_predictor.py` | Historical data query and percentile forecast generation |
| `src/influxdb_writer.py` | Bucket creation and forecast writes |

## 4. Forecast Method

The add-on groups historical load data into 15-minute time slots.

Process:

1. Query historical load data for the configured history window.
2. Normalize timestamps into 96 daily slots.
3. Compute P10, P50, and P90 consumption for each slot.
4. Project the slot profile over the configured forecast horizon.
5. Write forecast points to InfluxDB.

## 5. Configuration

Secret configured in the Home Assistant add-on Configuration tab:

| Secret | Purpose |
|--------|---------|
| `influxdb_token` | InfluxDB read/write access |

User configuration is stored under add-on config mapping, normally `/addon_configs/loadforecast/loadforecast.yaml`.

Important sections:

| Section | Purpose |
|---------|---------|
| `influxdb` | Host, port, org, source bucket, target bucket |
| `load_sensor` | Historical load entity/field identifier |
| `forecast` | History length and forecast horizon |
| `schedule` | Cron expression for forecast generation |

Default behavior:

- Source bucket: `HomeAssistant`
- Target bucket: `load_forecast`
- History: 90 days
- Horizon: 48 hours
- Schedule: hourly at minute 15

## 6. InfluxDB Interface

Inputs:

| Bucket | Purpose |
|--------|---------|
| `HomeAssistant` | Historical household load data |

Outputs:

| Bucket | Measurement | Fields |
|--------|-------------|--------|
| `load_forecast` | `load_forecast` | `energy_wh_p10`, `energy_wh_p50`, `energy_wh_p90` |

Tags:

- `model`

Fields also include run metadata such as `run_time`.

## 7. Runtime Behavior

At startup:

1. Load options/configuration.
2. Merge the InfluxDB token from add-on options.
3. Run one forecast generation cycle.
4. Schedule future runs using cron.

During each run:

1. Connect to InfluxDB.
2. Read historical data.
3. Generate forecast bands.
4. Ensure target bucket exists.
5. Write forecast data.

## 8. Dependencies

- Home Assistant add-on runtime.
- InfluxDB 2.x.
- Historical household load data.
- At least 7 days of data; 90+ days recommended.

## 9. Failure Handling

Expected behavior:

- Missing InfluxDB token logs a warning.
- Missing or insufficient historical data should result in no/limited forecast output.
- Empty forecasts are not written.
- Existing forecast points are overwritten by timestamp/tag identity rather than deleted first.

## 10. Tests and Validation

Current direct test coverage is limited compared with EnergyManager and OCPP Server. Validation should include:

- InfluxDB query against known historical data.
- Forecast output shape and 15-minute spacing.
- P10/P50/P90 ordering.
- Bucket creation/write behavior.
- Cron scheduling behavior.

## 11. Consumers

Primary consumer:

- EnergyManager, which reads `load_forecast` to simulate future home-battery SOC and decide discharge/EV behavior.
