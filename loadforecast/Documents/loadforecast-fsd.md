# LoadForecast Add-on — Functional Specification

LoadForecast generates statistical household-load forecasts from historical consumption stored in
InfluxDB. It builds time-of-day profiles from 90 days of history and produces P10/P50/P90 percentile
bands at 15-minute resolution, written to InfluxDB for EnergyManager optimization and dashboards.

## 1. System context

LoadForecast is one of four independent Home Assistant add-ons in the Energy-Management suite. Each
ships, installs, and runs on its own; they cooperate **only** through InfluxDB buckets and Home
Assistant entities — there are no direct calls between add-ons.

| Add-on | Role | Produces | Consumes |
|--------|------|----------|----------|
| SwissSolarForecast | PV production forecast | `pv_forecast` bucket | HA battery SOC (context) |
| **LoadForecast** (this add-on) | household load forecast | `load_forecast` bucket | HA load history |
| EnergyManager | battery / EV / appliance optimization | HA control entities, `energy_manager` bucket | `pv_forecast` + `load_forecast` |
| OCPP Server | OCPP 1.6j wallbox bridge | wallbox HA entities | EnergyManager power setpoint |

**This add-on's interfaces:**

- **Output (the contract EnergyManager depends on):** writes the `load_forecast` bucket, measurement
  `load_forecast`, at 15-minute resolution. EnergyManager reads `power_w_p10/p50/p90` to simulate
  future house load against the PV forecast. Full schema in §8.
- **Input:** reads historical `house_load_power` from the `HomeAssistant` bucket (90-day window) to
  build the profile. See §6.

## 2. Purpose & scope

In scope:

- Read historical load from InfluxDB and build 15-minute time-of-day profiles.
- Produce P10/P50/P90 forecast bands over the configured horizon.
- Write forecast points to InfluxDB on a cron schedule.

Out of scope: PV forecasting, battery/EV optimization, and any Home Assistant entity control.

| Property | Value |
|----------|-------|
| Slug | `loadforecast` |
| Architectures | aarch64, amd64, armv7 |
| Timeout | 120 seconds |
| Schedule | Hourly (`15 * * * *`) |

## 3. Architecture

Build/architecture, module layout, and the forecast-cycle diagram are HOW — see the Harness:
[`Harness/project/modules/loadforecast.md`](../../Harness/project/modules/loadforecast.md).

## 4. Algorithm

### Time-of-day profiling

Each day is divided into 96 slots of 15 minutes:

```
slot = hour × 4 + (minute ÷ 15)
slot 0  = 00:00–00:15   slot 48 = 12:00–12:15   slot 95 = 23:45–00:00
```

### Profile building

For each of the 96 slots, collect all historical values at that slot across the 90-day window and
compute:

- **P10** — low consumption (90% chance to exceed)
- **P50** — median / typical consumption
- **P90** — high consumption (10% chance to exceed)

### Forecast generation

For each future 15-minute timestamp in the horizon: compute its slot number, look up the slot's
P10/P50/P90 power, and write it. Consumers derive per-period energy as `power_w × 0.25 h`.

## 5. Configuration

### Secrets (Configuration UI)

Entered in **Settings → Add-ons → LoadForecast → Configuration**:

| Secret | Schema | Purpose |
|--------|--------|---------|
| `influxdb_token` | `password` | InfluxDB read/write access (required) |

### Non-secrets (`/config/loadforecast.yaml`)

```yaml
# NOTE: Token is configured in the Configuration tab, not here!
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"
  source_bucket: "HomeAssistant"     # read historical load from here
  target_bucket: "load_forecast"      # write forecasts here

load_sensor:
  entity_id: "house_load_power"       # HA entity used for load

forecast:
  history_days: 90                    # days of history to analyze
  horizon_hours: 120                  # forecast horizon (matches PV)

schedule:
  cron: "15 * * * *"                  # run at :15 every hour

log_level: "info"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `influxdb.host` | 192.168.0.203 | InfluxDB server IP/hostname |
| `influxdb.port` | 8087 | InfluxDB HTTP port |
| `influxdb.org` | energymanagement | InfluxDB organization |
| `influxdb.source_bucket` | HomeAssistant | Bucket with historical load |
| `influxdb.target_bucket` | load_forecast | Output bucket |
| `load_sensor.entity_id` | house_load_power | HA load-power entity |
| `forecast.history_days` | 90 | Days of history for the profile |
| `forecast.horizon_hours` | 48 (code/example) | Forecast horizon; the deployment sets **120** to match the 5-day PV forecast |
| `schedule.cron` | `15 * * * *` | Cron schedule for forecast runs |
| `log_level` | info | Logging level |

## 6. Data source

Historical consumption is queried from the `HomeAssistant` bucket:

```flux
from(bucket: "HomeAssistant")
  |> range(start: -90d)
  |> filter(fn: (r) => r.entity_id == "house_load_power")
  |> filter(fn: (r) => r._field == "value")
  |> aggregateWindow(every: 15m, fn: mean)
```

`sensor.house_load_power` is the Shelly 3EM 3-phase sum (direct measurement, template sensor
`load_total_power`). The Huawei Solar integration also derives a load value (`inverter_active_power
− power_meter_active_power + battery_charge_discharge_power`), but the Shelly measurement is the
source used here for accuracy.

## 7. Runtime behavior

At startup: load configuration, merge the InfluxDB token from add-on options, run one forecast
cycle, then schedule future runs by cron. Each run: connect to InfluxDB → read history → build the
profile and forecast bands → ensure the target bucket exists → write the forecast.

## 8. InfluxDB output schema

**Measurement:** `load_forecast` (bucket `load_forecast`), 15-minute intervals.

**Tags:**

| Tag | Values | Description |
|-----|--------|-------------|
| `model` | `statistical` | Forecast model type |

**Fields:**

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | W | Load power (low, 90% chance to exceed) |
| `power_w_p50` | W | Load power (median / typical) |
| `power_w_p90` | W | Load power (high, 10% chance to exceed) |
| `run_time` | ISO string | When the forecast was calculated |

Values are instantaneous power (W). Per-period energy is `power_w × 0.25` for 15-minute intervals
(the forecast stores power only; consumers derive energy).

## 9. Source files

Source-file layout is HOW — see [`Harness/project/modules/loadforecast.md`](../../Harness/project/modules/loadforecast.md).

## 10. Dependencies

Requires at least 7 days of historical load data (90+ recommended). The build dependency list is
HOW — see [`Harness/project/modules/loadforecast.md`](../../Harness/project/modules/loadforecast.md).

## 11. Grafana queries

Operator dashboard queries are OPERATE — see [`Handbook.md` → Dashboards & queries → LoadForecast](../../Handbook.md#dashboards--queries).

## 12. Failure handling

- A missing InfluxDB token logs a warning.
- Missing or insufficient history yields no / limited forecast output; empty forecasts are not written.
- Existing forecast points are overwritten by timestamp/tag identity, not deleted first.

## 13. Limitations and future enhancements

Current limitations: no weekday/weekend differentiation; no seasonal adjustment; no special-event
handling (holidays, vacations); no appliance-level modeling.

Potential enhancements: separate weekday/weekend profiles; seasonal scaling; short-term adaptation
from recent hours; calendar-event integration; machine-learning models (LSTM, XGBoost).

## 14. Tests and validation

Test approach and invocation are HOW — see [`Harness/project/modules/loadforecast.md`](../../Harness/project/modules/loadforecast.md)
and the testing hub [`Harness/project/testing.md`](../../Harness/project/testing.md). No test code exists yet (gap).

## Changelog

- 2026-06-29: FSD made self-contained — folded the full LoadForecast spec (algorithm, data source,
  output schema, configuration, limitations) in from the combined system FSD (since split into
  per-add-on FSDs). Corrected the output-field contract to `power_w_p10/p50/p90` + `run_time` (verified
  against `src/influxdb_writer.py`; energy is derived by consumers). Noted the deployed 120 h horizon
  vs the 48 h code default.
