# LoadForecast

Statistical load prediction using historical household consumption data.

## Overview

This add-on generates probabilistic load forecasts (P10/P50/P90) per 15-minute interval by analyzing historical consumption patterns from InfluxDB. Forecasts are used by:

- **EnergyManager** — SOC simulation and battery discharge optimization
- **Grafana dashboards** — consumption trend visualization

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     LoadForecast Add-on                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  PREDICTOR (scheduled: hourly at :15)                             │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │ InfluxDB (90 days history)                                 │   │
│  │   ───▶ time-of-day profiling (96 slots × 15 min)          │   │
│  │   ───▶ P10/P50/P90 percentiles                            │   │
│  │   ───▶ InfluxDB (load_forecast bucket)                    │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## How It Works

1. Queries the last 90 days of `load_power` data from InfluxDB (HomeAssistant bucket)
2. Groups readings into 96 time-of-day slots (15-minute resolution) in local time
3. Computes P10, P50, P90 percentiles for each slot
4. Projects these profiles forward for the next 48 hours
5. Writes forecasts to InfluxDB `load_forecast` bucket

## Configuration

Configuration is split between the add-on **Configuration tab** (secrets) and a **YAML file** (everything else).

### Secrets (Configuration tab)

| Option | Description |
|--------|-------------|
| `influxdb_token` | InfluxDB 2.x API token |

### YAML Configuration

Edit via File Editor or VS Code at `/addon_configs/loadforecast/loadforecast.yaml`.

```yaml
# InfluxDB connection (token in Configuration tab, not here!)
influxdb:
  host: "192.168.0.203"
  port: 8087
  org: "energymanagement"
  source_bucket: "HomeAssistant"     # Historical consumption data
  target_bucket: "load_forecast"     # Forecast output

# Load sensor settings
load_sensor:
  entity_id: "load_power"           # HA entity (domain prefix stripped)

# Forecast settings
forecast:
  history_days: 90                   # Days of history to analyze
  horizon_hours: 48                  # Forecast time horizon

# Timezone for time-of-day profiles
timezone: "Europe/Zurich"

# Schedule settings (cron expression)
schedule:
  cron: "15 * * * *"                 # Every hour at :15

# Logging level: debug, info, warning, error
log_level: "info"
```

## InfluxDB Schema

**Measurement:** `load_forecast`

| Tag | Values |
|-----|--------|
| `model` | `statistical` |
| `run_time` | ISO timestamp of forecast calculation |

| Field | Unit | Description |
|-------|------|-------------|
| `energy_wh_p10` | Wh | Low estimate (10th percentile) |
| `energy_wh_p50` | Wh | Median estimate (50th percentile) |
| `energy_wh_p90` | Wh | High estimate (90th percentile) |

## HACS Installation

1. Add this repository to HACS as a custom repository
2. Install "LoadForecast" add-on
3. Configure the InfluxDB token in the Configuration tab
4. Copy and edit `loadforecast.yaml` in `/addon_configs/loadforecast/`
5. Start the add-on

## Troubleshooting

### No forecast data

- Verify InfluxDB is accessible and the token is correct
- Check that the `HomeAssistant` bucket contains `load_power` data
- Ensure at least a few days of history exist (90 days recommended for stable profiles)

### Forecast looks flat or wrong

- Check `timezone` setting — profiles are built in local time
- Verify `entity_id` matches your load sensor (domain prefix stripped, e.g. `load_power` not `sensor.load_power`)
- Check add-on logs for query errors
