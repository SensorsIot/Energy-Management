# Module HOW — swisssolarforecast

How the SwissSolarForecast add-on is built and structured. Behaviour (ICON/STAC data contract, PV
config, output schema, calculation pipeline, shading) is in its FSD:
[`swisssolarforecast/Documents/swisssolarforecast-fsd.md`](../../../swisssolarforecast/Documents/swisssolarforecast-fsd.md).

## Architecture

A scheduled **fetcher** downloads GRIB files; a 15-minute **calculator** turns them into forecasts.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SwissSolarForecast Add-on                        │
├─────────────────────────────────────────────────────────────────────┤
│  FETCHER (scheduled via cron)                                       │
│    CH1: 8× daily (30 2,5,8,11,14,17,20,23 * * *)                    │
│    CH2: 4× daily (45 2,8,14,20 * * *)                               │
│    MeteoSwiss STAC API ──▶ GRIB files (/share/swisssolarforecast)   │
│                                 │ local files                       │
│                                 ▼                                   │
│  CALCULATOR (every 15 minutes)                                      │
│    1. Load GRIB files from disk                                     │
│    2. Extract GHI + Temperature at location                         │
│    3. Per ensemble member: GHI→DNI+DHI (Erbs), solar position,      │
│       POA per string, cell temp (Faiman), DC power (PVWatts),       │
│       inverter efficiency + clipping                                │
│    4. P10/P50/P90 across ensemble members                           │
│    5. Write to InfluxDB pv_forecast bucket                          │
└─────────────────────────────────────────────────────────────────────┘
```

Primary modules:

| Module | Responsibility |
|--------|----------------|
| `run.py` | Entry point, scheduler initialization |
| `src/scheduler.py` | APScheduler wrapper (fetch/calculation scheduling) |
| `src/icon_fetcher.py` | MeteoSwiss STAC API client, GRIB discovery and download |
| `src/grib_parser.py` | GRIB parsing, unstructured-grid handling, variable extraction |
| `src/pv_model.py` | pvlib-based PV power modeling |
| `src/influxdb_writer.py` | Forecast writes |
| `src/config.py` | PV system configuration loader |
| `src/shading_tracker.py` | Shading observation/factor handling |
| `src/accuracy_tracker.py` | Forecast accuracy tracking |
| `src/notifications.py` | Optional Telegram notifications |

## Source files

| File | Lines | Purpose |
|------|-------|---------|
| `run.py` | 386 | Entry point, scheduler initialization |
| `src/icon_fetcher.py` | 466 | STAC API client, GRIB download |
| `src/grib_parser.py` | 840 | GRIB parsing, grid handling |
| `src/pv_model.py` | 338 | pvlib PV power calculations |
| `src/influxdb_writer.py` | 405 | InfluxDB forecast writer |
| `src/scheduler.py` | 202 | APScheduler wrapper |
| `src/config.py` | 146 | PV configuration loader |
| `src/notifications.py` | 135 | Telegram notifications |

## Dependencies

```
pvlib>=0.10.0              # PV modeling
pandas>=2.0.0              # data manipulation
numpy>=1.24.0             # numerical computing
requests>=2.28.0          # HTTP client for STAC API
xarray>=2023.1.0          # N-dimensional arrays
cfgrib>=0.9.10            # GRIB handling
eccodes>=1.5.0            # GRIB codec
PyYAML>=6.0               # YAML parsing
influxdb-client>=1.36.0   # InfluxDB client
APScheduler>=3.10.0       # task scheduling
```

## Tests and validation

- `swisssolarforecast/test_pipeline.py`, `swisssolarforecast/testdata/`, and the Grafana dashboard
  JSON for visual validation.
- Confirm a successful GRIB fetch; confirm points in `pv_forecast`; check 15-minute spacing and
  P10 ≤ P50 ≤ P90 ordering; compare daily forecast totals against measured production.
- Run via the project test command (see [`../build-and-release.md`](../build-and-release.md)).
