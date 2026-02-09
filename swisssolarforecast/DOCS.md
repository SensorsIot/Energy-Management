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
│  │ GRIB files ───▶ pvlib ───▶ shading ───▶ P10/P50/P90 ───▶ DB  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                      ▲                               │
│                                      │ (shading_factors.yaml)        │
│  SHADING TRACKER (scheduled: 21:15 daily)                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Actual vs Forecast ───▶ shading_observations ───▶ YAML       │   │
│  │                         (InfluxDB - all days)     (sunny only)│   │
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

### Shading Correction

The add-on learns site-specific shading patterns by comparing actual production with model predictions on sunny days. Factors are stored in `shading_factors.yaml`:

```yaml
shading_correction:
  description: Shading factors by hour (local time). Updated automatically.
  last_updated: '2025-02-09T00:00:00+01:00'
  num_sunny_days: 10
  factors:
    East:
      6: 0.20    # Heavy morning shading (trees/buildings to east)
      7: 0.25
      8: 0.55
      # ... factors for hours 6-18
    West:
      6: 0.65
      # ... factors for hours 6-18
    South:
      6: 0.45
      # ... factors for hours 6-18
```

**How it works:**
1. At 21:15 daily, compares yesterday's forecast with actual production
2. Stores ratio (actual/forecast) and weather_factor to InfluxDB for ALL days
3. If weather_factor >= 0.90 (sunny day), recalculates factors from last 10 sunny days
4. Writes updated factors to `shading_factors.yaml`
5. Calculator loads factors and applies per-string, per-hour corrections

## InfluxDB Schema

**Measurement:** `pv_forecast`

**Resolution:** 15-minute intervals (aligned to :00, :15, :30, :45)

**One point per timestamp** - all P10/P50/P90 values in a single record for guaranteed timestamp alignment.

| Tag | Values |
|-----|--------|
| `inverter` | total, East+West, South, etc. |
| `model` | ch1, ch2, hybrid |
| `run_time` | ISO timestamp of forecast calculation |

| Field | Unit | Description |
|-------|------|-------------|
| `power_w_p10` | Watts | PV power (pessimistic) |
| `power_w_p50` | Watts | PV power (expected) |
| `power_w_p90` | Watts | PV power (optimistic) |
| `energy_wh_p10` | Wh | Cumulative PV energy (pessimistic) |
| `energy_wh_p50` | Wh | Cumulative PV energy (expected) |
| `energy_wh_p90` | Wh | Cumulative PV energy (optimistic) |
| `load_power_w` | Watts | Load/consumption power |
| `load_energy_wh` | Wh | Cumulative load energy |
| `net_power_w_p10` | Watts | Net P10 = PV_p10 - Load |
| `net_power_w_p50` | Watts | Net P50 = PV_p50 - Load |
| `net_power_w_p90` | Watts | Net P90 = PV_p90 - Load |
| `net_energy_wh_p10` | Wh | Cumulative net (pessimistic) |
| `net_energy_wh_p50` | Wh | Cumulative net (expected) |
| `net_energy_wh_p90` | Wh | Cumulative net (optimistic) |
| `ghi` | W/m² | Global horizontal irradiance |
| `temp_air` | °C | Air temperature |

**Note:** All values share the exact same timestamp, ensuring perfect alignment for MPC calculations.

---

**Measurement:** `shading_observations`

Stores raw shading ratios for analysis and factor calculation.

| Tag | Values |
|-----|--------|
| `snapshot_id` | Date string (YYYY-MM-DD) |
| `string` | East, West, South |

| Field | Unit | Description |
|-------|------|-------------|
| `hour` | int | Local hour (0-23) |
| `ratio` | ratio | Actual/forecast ratio (clamped 0.1-1.5) |
| `weather_factor` | ratio | Day's weather factor (actual/clear-sky) |

**Note:** Observations are stored for ALL days. Only days with `weather_factor >= 0.90` are used for factor calculation.

## Grafana Query Examples

### PV Power Forecast (P10/P50/P90 bands)

```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 24h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "power_w_p10" or r._field == "power_w_p50" or r._field == "power_w_p90")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
```

### Net Power (Surplus/Deficit)

```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 24h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "net_power_w_p50")
```

### Energy Balance (PV vs Load)

```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 24h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "energy_wh_p50" or r._field == "load_energy_wh" or r._field == "net_energy_wh_p50")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
```

### Today's Total Energy Forecast

```flux
from(bucket: "pv_forecast")
  |> range(start: today(), stop: tomorrow())
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "energy_wh_p50")
  |> last()
  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))  // Wh to kWh
```

### Forecast vs Actual

```flux
forecast = from(bucket: "pv_forecast")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "power_w_p50")

actual = from(bucket: "HomeAssistant")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.entity_id == "sensor.solar_pv_total_ac_power")

union(tables: [forecast, actual])
```

### Shading Observations by String

```flux
from(bucket: "pv_forecast")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "shading_observations")
  |> filter(fn: (r) => r._field == "ratio" or r._field == "hour" or r._field == "weather_factor")
  |> pivot(rowKey: ["_time", "string", "snapshot_id"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.weather_factor >= 0.90)  // Sunny days only
  |> group(columns: ["string", "hour"])
  |> mean(column: "ratio")
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
