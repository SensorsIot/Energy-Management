# SwissSolarForecast Add-on - Functional Specification

**Last updated:** 2026-04-27

## 1. Purpose

SwissSolarForecast produces PV power and energy forecasts for a Swiss PV installation using MeteoSwiss ICON ensemble weather data and pvlib modeling. The output is written to InfluxDB for dashboards and for EnergyManager optimization.

## 2. Scope

In scope:

- Fetch MeteoSwiss ICON-CH1 and ICON-CH2 GRIB data.
- Parse weather variables needed for PV modeling.
- Model PV output per plant, inverter, and string.
- Produce P10/P50/P90 forecast bands.
- Write PV forecast data to InfluxDB.
- Track forecast accuracy and optional shading correction.

Out of scope:

- Load forecasting.
- Battery or EV optimization.
- Direct wallbox control.

## 3. Architecture

SwissSolarForecast runs as a Home Assistant add-on with Home Assistant API access enabled. It stores downloaded weather data under `/share/swisssolarforecast`, reads user PV configuration from add-on config storage, and writes forecasts to InfluxDB.

Primary modules:

| Module | Responsibility |
|--------|----------------|
| `run.py` / `run.sh` | Add-on entry point |
| `src/scheduler.py` | Fetch/calculation scheduling |
| `src/icon_fetcher.py` | MeteoSwiss ICON file discovery and download |
| `src/grib_parser.py` | GRIB parsing and weather variable extraction |
| `src/pv_model.py` | PV production modeling |
| `src/influxdb_writer.py` | Forecast writes |
| `src/accuracy_tracker.py` | Forecast accuracy tracking |
| `src/shading_tracker.py` | Shading observation/factor handling |
| `src/config.py` | PV system configuration model |

## 4. Data Sources

Weather source:

| Model | Resolution | Horizon | Use |
|-------|------------|---------|-----|
| ICON-CH1 | 1 km | Short horizon | High-resolution near-term forecast |
| ICON-CH2 | 2.1 km | Longer horizon | Extended forecast coverage |

Forecasts use ensemble members to produce uncertainty bands.

## 5. Configuration

Secrets configured in the Home Assistant add-on Configuration tab:

| Secret | Purpose |
|--------|---------|
| `influxdb_token` | InfluxDB write/read access |
| `telegram_bot_token` | Optional notifications |
| `telegram_chat_id` | Optional notification target |

User configuration is stored in add-on config storage, normally `/addon_configs/swisssolarforecast/swisssolarforecast.yaml`.

Important sections:

| Section | Purpose |
|---------|---------|
| `influxdb` | Host, port, org, bucket |
| `panels` | PV panel models and temperature coefficients |
| `plants` | Location, timezone, inverters, strings |
| `schedule` | Fetch and calculation schedules |
| `shading` | Optional shading correction behavior |
| `accuracy` | Optional forecast accuracy tracking |

## 6. Forecast Output

InfluxDB output:

| Bucket | Measurement | Purpose |
|--------|-------------|---------|
| `pv_forecast` | `pv_forecast` | PV power/energy forecast |
| `pv_forecast` | `pv_forecast_snapshot` | Forecast snapshot for accuracy comparison |
| `pv_forecast` | `pv_accuracy` | Forecast accuracy metrics |
| `pv_forecast` | `shading_observations` | Observed shading data |

Common forecast fields:

| Field | Description |
|-------|-------------|
| `power_w_p10` | Pessimistic power forecast |
| `power_w_p50` | Median power forecast |
| `power_w_p90` | Optimistic power forecast |
| `energy_wh_p10` | Pessimistic 15-minute energy |
| `energy_wh_p50` | Median 15-minute energy |
| `energy_wh_p90` | Optimistic 15-minute energy |
| `ghi` | Global horizontal irradiance |
| `temp_air` | Air temperature |

Tags commonly include inverter, model, and run metadata.

## 7. Runtime Behavior

The scheduler coordinates:

| Task | Cadence |
|------|---------|
| ICON-CH1 fetch | Cron schedule for model availability |
| ICON-CH2 fetch | Cron schedule for model availability |
| Forecast calculation | Regular interval, typically 15 minutes |
| Accuracy tracking | Optional scheduled evaluation |
| Shading update | Optional scheduled update |

Typical flow:

1. Download latest GRIB files.
2. Parse weather variables at plant location/grid point.
3. Run PV model for configured strings/inverters.
4. Aggregate per-inverter and total forecast.
5. Write forecast points to InfluxDB.
6. Optionally update accuracy/shading measurements.

## 8. Dependencies

- Home Assistant add-on runtime.
- InfluxDB 2.x.
- Network access to MeteoSwiss Open Data.
- pvlib and GRIB parsing dependencies.
- Correct PV plant metadata.
- Optional HA entity access for actual production/SOC context.

## 9. Failure Handling

Expected behavior:

- Missing weather files should be logged and skipped.
- Existing forecast data is overwritten by timestamp/tag identity rather than deleted first.
- Missing InfluxDB token logs a warning and prevents writes.
- Notification failures must not stop forecast calculation.
- Shading/accuracy features should fail independently of core forecast generation.

## 10. Tests and Validation

Validation assets:

- `swisssolarforecast/test_pipeline.py`
- `swisssolarforecast/testdata/`
- Grafana dashboard JSON for visual validation.

Recommended validation:

- Confirm successful GRIB fetch.
- Confirm forecast points in `pv_forecast`.
- Check 15-minute spacing and P10/P50/P90 ordering.
- Compare daily forecast totals against measured production.

## 11. Consumers

Primary consumers:

- EnergyManager for optimization.
- Grafana/HA dashboards for visualization.
- Accuracy/shading trackers for model improvement.
