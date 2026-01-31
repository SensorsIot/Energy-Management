# EnergyManager Changelog

## [1.5.14] - 2026-01-31

### Fixed
- Appliance signal uses full simulation (not filtered to expensive periods)
  - ORANGE means enough battery energy to avoid grid import, regardless of tariff
  - Weekend filtering only applies to battery discharge decision, not appliance signal
- Refactored `filter_expensive_periods()` method in BatteryOptimizer (DRY)

## [1.5.13] - 2026-01-30

### Fixed
- Weekend/holiday daytime no longer treated as expensive hours
  - The expensive hours mask only checked time-of-day (06:15-21:00), not day type
  - Saturday/Sunday SOC dips were incorrectly blocking discharge on Friday night
  - Now skips weekend/holiday days when checking min SOC during expensive hours

## [1.5.12] - 2026-01-30

### Changed
- Simplified appliance signal ORANGE rule to use only min SOC
  - ORANGE: min SOC >= reserve% + appliance% (single condition)
  - If min SOC is above threshold, final SOC is guaranteed to be too
  - Updated FSD to v2.9

## [1.5.11] - 2026-01-30

### Fixed
- Appliance signal ORANGE now checks min SOC against full threshold (reserve% + appliance%)
  - Previously only final SOC was checked, allowing orange when SOC dipped to 0% mid-day

## [1.5.10] - 2026-01-25

### Fixed
- Proper expensive hours boundary using hours AND minutes
  - Include: 06:15, 06:30, ..., 20:45, 21:00
  - Exclude: 06:00 (result of cheap), 21:15+ (cheap time)
  - Previous fix using only hours incorrectly included 21:15, 21:30, 21:45

## [1.5.9] - 2026-01-25

### Fixed
- Correct expensive hours boundary: `hour > 6 AND hour <= 21`
  - 06:00 SOC = state AFTER cheap period → exclude (>)
  - 21:00 SOC = state AFTER expensive period → include (<=)

## [1.5.8] - 2026-01-25

### Fixed
- Include 21:00 in expensive hours check (hour <= 21 instead of hour < 21)
  - The SOC at 21:00 represents state AFTER last expensive period discharged
  - This fixes off-by-one error where min SOC check missed the boundary

## [1.5.7] - 2026-01-25

### Added
- Forecast snapshot for accuracy tracking (`soc_forecast_snapshot` measurement)
  - Accumulates over time: each run overwrites from NOW onwards
  - Earlier predictions preserved for comparison with actual SOC
  - Enables retrospective analysis of forecast accuracy

## [1.5.6] - 2026-01-25

### Added
- Write both SOC forecast scenarios to InfluxDB:
  - `with_strategy`: What will happen (with discharge blocking)
  - `without_strategy`: What would happen without blocking
- Use `scenario` tag in InfluxDB to differentiate curves

## [1.5.5] - 2026-01-25

### Changed
- Version now uses `__version__` constant in run.py (baked at build time)
- Removed runtime config.yaml reading for version display

## [1.5.4] - 2026-01-25

### Fixed
- Include config.yaml in Docker container so version can be read at runtime

## [1.5.3] - 2026-01-25

### Fixed
- Version in log banner now reads from config.yaml (was hardcoded)

## [1.5.2] - 2026-01-25

### Fixed
- Fixed InfluxDB field name: `final_soc_wh` → `final_soc_percent` (matching dataclass change)

## [1.5.1] - 2026-01-25

### Changed
- Simplified battery discharge algorithm (FSD v2.6)
  - Replaced switch-on time calculation with rolling 15-minute threshold check
  - Self-correcting behavior: re-evaluates every cycle based on current SOC
  - Cleaner decision logic: 3 simple branches (expensive tariff, SOC OK, SOC not OK)

- Fixed appliance signal ORANGE threshold calculation
  - Now correctly uses: `reserve% + appliance%` (not just `appliance%`)
  - Example: 10% reserve + 15% appliance = 25% threshold
  - Works in SOC% for consistency with simulation

### Added
- Test suite for battery optimizer (14 tests)
- Test suite for appliance signal (19 tests)
- Total: 33 tests passing

### Removed
- `switch_on_time` field from DischargeDecision (no longer calculated)
- `saved_wh` field from DischargeDecision (no longer calculated)
- `deficit_wh` field replaced by `min_soc_percent`

## [1.5.0] - 2026-01-24

### Added
- Initial simplified battery discharge optimization
- Appliance signal calculation

## [1.4.x] - Previous versions

- Battery discharge with switch-on time calculation
- SOC simulation with efficiency
- Tariff handling (cheap/expensive, weekends, holidays)
