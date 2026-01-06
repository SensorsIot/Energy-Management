# SwissSolarForecast

Swiss PV power forecast using MeteoSwiss ICON ensemble data with pvlib.

## Overview

This add-on generates probabilistic PV power forecasts (P10/P50/P90) using:

- **MeteoSwiss ICON-CH1**: 1km resolution, 33h horizon, 11 ensemble members
- **MeteoSwiss ICON-CH2**: 2.1km resolution, 120h horizon, 21 ensemble members
- **pvlib**: Industry-standard PV modeling library

Forecasts are stored in InfluxDB for use by:
- Energy management optimizers (MPC)
- Grafana dashboards
- Home Assistant automations

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SwissSolarForecast Add-on                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  FETCHER (scheduled: 3h/6h)                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ MeteoSwiss STAC API  ───▶  GRIB files (local /share/data)    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                      │                               │
│                                      │ (files on disk)               │
│                                      ▼                               │
│  CALCULATOR (scheduled: every 15 min)                                │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ GRIB files ───▶ pvlib ───▶ P10/P50/P90 forecast ───▶ InfluxDB│   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Configuration

### InfluxDB Settings

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  token: "your-influxdb-token"
  org: "energymanagement"
  bucket: "pv_forecast"
```

### Location

```yaml
location:
  latitude: 47.475
  longitude: 7.767
  altitude: 330
  timezone: "Europe/Zurich"
```

### PV System Configuration

Create a file `/config/swisssolarforecast.yaml` with your PV system:

```yaml
panels:
  - id: "AE455"
    model: "AE Solar AC-455MH/144V"
    pdc0: 455
    gamma_pdc: -0.0035

plants:
  - name: "House"
    inverters:
      - name: "East+West"
        max_power: 10000
        efficiency: 0.82
        strings:
          - name: "East"
            azimuth: 90
            tilt: 15
            panel: "AE455"
            count: 8
          - name: "West"
            azimuth: 270
            tilt: 15
            panel: "AE455"
            count: 9
```

### Schedule

```yaml
schedule:
  ch1_cron: "30 2,5,8,11,14,17,20,23 * * *"  # UTC
  ch2_cron: "45 2,8,14,20 * * *"              # UTC
  calculator_interval_minutes: 15
```

## InfluxDB Schema

**Measurement:** `pv_forecast`

| Tag | Values |
|-----|--------|
| `percentile` | P10, P50, P90 |
| `inverter` | total, East+West, South, etc. |
| `model` | ch1, ch2, hybrid |
| `run_time` | ISO timestamp |

| Field | Unit |
|-------|------|
| `power_w` | Watts |
| `ghi` | W/m² |
| `temp_air` | °C |

## Grafana Query Examples

### Current Forecast

```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 24h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.percentile == "P50")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "power_w")
```

### Forecast vs Actual

```flux
forecast = from(bucket: "pv_forecast")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.percentile == "P50" and r.inverter == "total")
  |> filter(fn: (r) => r._field == "power_w")

actual = from(bucket: "HomeAssistant")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.entity_id == "sensor.solar_pv_total_ac_power")

union(tables: [forecast, actual])
```

## Storage

GRIB files are stored in `/share/swisssolarforecast`:

```
/share/swisssolarforecast/
├── icon-ch1/
│   └── YYYYMMDDHHMM/
│       └── *.grib2
└── icon-ch2/
    └── YYYYMMDDHHMM/
        └── *.grib2
```

Configure `max_storage_gb` to limit disk usage (default: 3 GB).
Old runs are automatically cleaned up.

## HACS Installation

1. Add this repository to HACS as a custom repository
2. Install "SwissSolarForecast" add-on
3. Configure InfluxDB connection and PV system
4. Start the add-on

## Troubleshooting

### No forecast data

Check if GRIB files were downloaded:
```bash
ls -la /share/swisssolarforecast/icon-ch1/
ls -la /share/swisssolarforecast/icon-ch2/
```

### InfluxDB connection failed

Verify InfluxDB is accessible and token is correct:
```bash
curl -H "Authorization: Token YOUR_TOKEN" \
  http://192.168.0.203:8087/api/v2/buckets
```

### Scheduler not running

Check add-on logs for errors:
```
Settings > Add-ons > SwissSolarForecast > Log
```
