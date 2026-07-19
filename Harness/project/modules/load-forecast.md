# Module HOW — load-forecast

How the LoadForecast add-on is built and structured. Behaviour (algorithm, schemas, config contract)
is in its FSD: [`load-forecast/Documents/load-forecast-fsd.md`](../../../load-forecast/Documents/load-forecast-fsd.md).

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                       LoadForecast Add-on                            │
├─────────────────────────────────────────────────────────────────────┤
│  FORECAST CYCLE (every hour at :15)                                 │
│    1. Query 90 days of house_load_power from the HomeAssistant bucket│
│    2. Build a time-of-day profile:                                  │
│         • group into 96 daily slots (15-min periods)                │
│         • compute P10/P50/P90 per slot                              │
│    3. Project the profile over the forecast horizon                 │
│    4. Write to the load_forecast bucket                             │
└─────────────────────────────────────────────────────────────────────┘
```

Primary modules:

| Module | Responsibility |
|--------|----------------|
| `run.py` | Entry point, config loading, cron scheduling loop |
| `src/load_predictor.py` | Historical query + percentile forecast generation |
| `src/influxdb_writer.py` | Bucket creation and forecast writes |

## Source files

| File | Purpose |
|------|---------|
| `run.py` | Entry point, scheduler loop |
| `src/load_predictor.py` | Statistical forecasting algorithm |
| `src/influxdb_writer.py` | InfluxDB forecast writer |

## Dependencies

```
pandas>=2.0.0              # data manipulation
numpy>=1.24.0             # numerical computing
influxdb-client>=1.36.0   # InfluxDB client
croniter>=1.3.0           # cron expression parsing
```

## Tests and validation

Confirm an InfluxDB query against known history; check forecast output shape and 15-minute spacing;
check P10 ≤ P50 ≤ P90 ordering; verify bucket creation/write behavior and cron scheduling. Run via
the project test command (see [`../build-and-release.md`](../build-and-release.md)).
