# Changelog

## [1.2.3] - 2026-02-09

### Fixed
- Removed redundant "EastWest" combined entry from accuracy tracking
  - Was comparing DC forecast to AC actual (inverter_active_power)
  - Now uses per-string East/West with DC measurements (inverter_pv_1_power, inverter_pv_2_power)
  - More accurate comparison: DC forecast vs DC actual

## [1.2.2] - 2026-02-09

### Fixed
- Timezone bug in evaluate_forecast: was using 21:00 UTC instead of 21:00 local time
  - Snapshot is created at 21:00 local (20:00 UTC in winter), but evaluation was querying 21:00-21:00 UTC
  - Now correctly uses local timezone to calculate snapshot period
- Added `local_timezone` parameter to AccuracyTracker for proper timezone handling

### Added
- Backfill script `scripts/backfill_accuracy.py` for recovering missing evaluation data
- Backfilled accuracy data for Feb 2-8 (was broken due to timezone bug)

## [1.2.1] - 2026-02-02

### Added
- Two-layer calibration: decompose forecast error into weather error and model error
- `weather_factor` field in `pv_accuracy` — ratio of actual/forecast across all strings
- `weather_error_wh` field — error attributable to MeteoSwiss GHI forecast
- `model_error_wh` field — per-string residual error from PV model parameters
- `weather_adjusted_wh` field — forecast scaled by weather factor
- Grafana panels: Weather vs Model Error, Weather Factor, MeteoSwiss curve on error panel
- Backfilled calibration data for Jan 26 - Feb 1

### Changed
- Corrected all panel azimuths by +13.3° for building orientation (measured from aerial)
  - East 90° → 103.3°, West 270° → 283.3°, South 180° → 193.3°
- South inverter efficiency 0.80 → 0.96 (Enphase micro-inverter actual efficiency)

## [1.2.0] - 2026-02-02

### Added
- Forecast accuracy evaluation Phase 2 (FSD 5.3)
- 21:15 daily evaluation comparing snapshot forecast with actual PV production
- Cross-bucket query to HomeAssistant InfluxDB bucket for actual PV data
- Per-inverter accuracy tracking (total, EastWest, South)
- `pv_accuracy` measurement with forecast/actual/error fields
- Grafana panels: Forecast Error, Daily Accuracy Summary, Daily MAPE Trend

### Changed
- Scheduler supports evaluate callback with 21:15 cron job
- AccuracyTracker now includes evaluation methods alongside snapshot

## [1.1.6] - 2026-01-21

### Fixed
- Add `homeassistant_api: true` to enable HA entity state access for battery monitoring

## [1.1.5] - 2026-01-21

### Added
- Forecast accuracy tracking Phase 1 (FSD 5.3)
- 21:00 daily snapshot of forecast for next 24h period
- Continuous battery state recording with every forecast write
  - battery_soc: Current battery state of charge (%)
  - discharge_power_limit: Max discharge power setting (W)
- Home Assistant API integration for battery entity readings
- AccuracyTracker class with snapshot functionality
- Configurable local timezone for decision time scheduling

### Changed
- Scheduler now supports local timezone for accuracy snapshot job
- InfluxDB writer includes battery state fields in pv_forecast measurement

## [1.0.0] - 2026-01-06

### Added
- Initial release of SwissSolarForecast add-on
- ICON-CH1 ensemble data fetching (1km, 33h, 11 members)
- ICON-CH2 ensemble data fetching (2.1km, 120h, 21 members)
- Hybrid CH1+CH2 forecast for 48h coverage
- P10/P50/P90 uncertainty quantification
- InfluxDB storage for forecast data
- APScheduler for automated fetching and calculation
- YAML configuration for PV system
- HACS repository support
