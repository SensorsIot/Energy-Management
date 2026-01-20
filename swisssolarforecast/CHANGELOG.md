# Changelog

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
