# Changelog

## [1.6.1] - 2026-07-04

### Fixed
- Calibration sunny gate now judges clear sky from the sky, not the power
  level: a clear day (stable high-sun midday ratio) records every interval, so
  the shade map can learn whole-roof horizon shading that drops the system
  ratio below the old 0.75 power floor. Non-clear days keep the smooth+high
  per-interval fallback. `clear_sky_mask()` + tests (FSD §10.2).

### Changed
- FSD §10.8 shading table: documents the eastern-horizon whole-roof morning
  obstruction and the front-row-shades-back-row inter-row shading on South.

## [1.6.0] - 2026-07-03

### Added
- Daily long-term summary (FSD §8.1): one `pv_daily` point per day written at
  23:55 local to the infinite-retention `energy_longterm` bucket — production
  per inverter, specific yield, peak power, clear-sky performance ratio,
  sunny-interval PR, calibration gains, clipping hours, forecast P50 + bias.
  `src/longterm.py` + `test_longterm.py`.

## [1.5.1] - 2026-07-03

### Fixed
- Ground albedo for ASOB_S→GHI compensation corrected 0.2 → 0.13 (ICON's
  effective surface albedo at this grid cell). With 0.2, clear-day GHI
  integrated to 8.7% above the Ineichen clear-sky ceiling, making cloudless
  forecasts ~9% optimistic (71 kWh vs a ~65 kWh physical ceiling).

## [1.5.0] - 2026-07-03

### Added
- Calibration model (FSD §10): forecast = physics × shade × eff × gain
  - shade per string over sun-position bins — fixed infrastructure shading as a
    static map (10°×5° bins, median, min 5 obs, normalized to the unshaded
    level) — season-proof by construction
  - eff per string over power-fraction bins — time-invariant model/efficiency
    deviation curve (10 bins, min 20 obs, normalized at the 40–60% band)
  - gain per string — soiling/snow EWMA (7-day time constant) with a
    below-0.93 cleaning notification
  - Learning compares actuals against a clear-sky physics reference (pvlib
    Ineichen) — independent of forecast quality; clouds are never calibrated
  - Per-interval sunny gate: system ratio > 0.75 AND rolling smoothness;
    clipping intervals excluded from eff/gain
  - `src/calibration.py` + `test_calibration.py`; cache `calibration.yaml`
    lives in the data dir (persists across image rebuilds)

### Removed
- Hour-keyed shading factors (`src/shading_tracker.py`, `shading_factors.yaml`)
  — replaced by the sun-position calibration model

## [1.4.0] - 2026-07-03

### Fixed
- ICON radiation handling (root cause of ~2× clear-sky morning under-forecast):
  - `ASOB_S` net shortwave is now albedo-compensated (÷0.8) to true GHI
  - De-accumulated interval means are stamped at interval midpoints (was: interval
    end, lagging the diurnal ramp by 30 min — low mornings, high evenings)
  - The de-accumulation anchor point is dropped (was: written as a zero-power
    point at every run reference time, and a garbage since-run-start average at
    the CH2 h33 series start)
- MeteoSwiss local point forecast hourly means shifted to interval midpoints

### Changed
- Panel definitions replaced with manufacturer datasheet ground truth
  (AXITEC AC-455MH/144V ×17, LONGi LR4-60HPB-350M ×4, Meyer Burger White 385 ×1;
  corrected tilts incl. SouthBack 30°; per-module gamma) — FSD Appendix A
- Calibration separated from panel data: hardcoded default shading factors
  removed (neutral until relearned from sunny-day observations); inverter
  efficiency set to datasheet-plausible values (0.98 Huawei / 0.97 Enphase)

### Added
- `test_radiation.py` — regression tests for de-accumulation, anchor drop,
  midpoint stamping, and albedo compensation (FSD §16)

## [1.2.4] - 2026-02-09

### Added
- Adaptive shading correction for PV forecast
  - Learns shading patterns from actual vs forecast ratios on sunny days
  - Per-string (East, West, South), per-hour correction factors
  - Stores all observations to InfluxDB `shading_observations` measurement
  - Only recalculates factors from days with weather_factor >= 0.90
  - Rolling average of last 10 sunny days for stable corrections
- `shading_tracker.py` module for shading observation and factor calculation
- `shading_factors.yaml` for storing calculated correction factors
- Shading update callback in scheduler (runs after accuracy evaluation)

### Changed
- Huawei inverter efficiency: 0.82 → 0.95 (measured actual efficiency)

## [1.2.3] - 2026-02-09

### Fixed
- Removed "EastWest" combined and "total" from accuracy tracking
  - EastWest was comparing DC forecast to AC actual - now uses per-string East/West (DC)
  - Total mixed AC/DC measurements - removed entirely
  - Now tracks: East (DC), West (DC), South (AC from Enphase)

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
