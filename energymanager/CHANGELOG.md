# EnergyManager Changelog

## [1.6.9] - 2026-02-13

### Changed
- Skip battery protection when battery SOC = 100%. Battery is full, all
  excess PV available for EV.

## [1.6.8] - 2026-02-13

### Fixed
- Set wallbox power limit via REST API (`POST /api/states/`) instead of `number.set_value`
  service. The wallbox entity is created by the OCPP server via REST API, not as a
  platform-backed number entity, so the service call silently did nothing.

## [1.6.7] - 2026-02-13

### Fixed
- Skip EV charging control immediately when wallbox entity doesn't exist (OCPP server
  not running). Previously made 2 API calls per minute that both returned 404.
- Log missing entities (404) at debug level instead of error.

## [1.6.5] - 2026-02-13

### Fixed
- InfluxDB entity_id query: strip domain prefix (`sensor.smart_battery` → `smart_battery`)
  to match how HA's InfluxDB integration stores entity_id tags in `HomeAssistant` bucket

## [1.6.4] - 2026-02-13

### Fixed
- Restore sensor values from InfluxDB after HA restart. REST API sensors are lost
  on restart; now queries last known value from `HomeAssistant` bucket (7-day window)
  before falling back to defaults. `sensor.smart_battery` shows last known SOC (e.g.
  73%) instead of "unknown" even when the Smart car API is temporarily down.

## [1.6.2] - 2026-02-13

### Changed
- EV solar mode uses PV-based excess (`pv_power - load_power`) instead of grid-based
  formula. The Huawei inverter hides available PV by greedily charging the battery,
  so grid export understates true excess. When battery protection confirms the battery
  will reach 80% at 21:00, EV starts immediately from total PV instead of waiting
  for grid export to rise. Battery protection re-checks every minute for safety.
- `calculate_ev_power()` interface simplified: single `excess_w` parameter replaces
  `grid_power_w` + `wallbox_power_w`

### Fixed
- Battery protection now checks forecast SOC **at** cheap tariff start (21:00) using
  a ±15min query window, instead of peak SOC between now and 21:00. Previously
  `max()` could pass even when the battery would discharge back below 80% by evening.

## [1.6.0] - 2026-02-13

### Changed
- Migrated EV charging from two `input_boolean` entities to `input_select.ev_charging_mode`
  - Replaces `input_boolean.ev_goal_charge` + `input_boolean.ev_charge_now`
  - Single entity with options: `solar`, `immediate`, `cheap`
- Immediate and Cheap modes now auto-revert to `solar` when charging completes
  - Previously buttons stayed on; now mode switches back after 5 min idle
- Renamed `calculate_goal_mode()` → `calculate_charging_mode()` with mode string interface

### Added
- `get_input_select()` and `set_input_select()` methods in HAClient
- `revert_to_solar` flag in `ChargingModeResult` for auto-revert logic

## [1.5.16] - 2026-02-09

### Fixed
- Added `net_wh` column to simulation DataFrame output
  - Required for grid export calculation in appliance signal
  - Export check was returning 0Wh because simulation was missing this column

## [1.5.15] - 2026-02-09

### Added
- New ORANGE condition for appliance signal: grid export before evening >= 1.5kWh
  - If we're going to export energy to the grid anyway, might as well use it
  - Checks if battery is full (SOC >= 99.9%) AND has excess PV before 18:00
  - Uses `appliance_energy_wh` (default 1500Wh) as threshold
- Added `evening_hour` and `local_timezone` parameters to `calculate_appliance_signal()`
- Added `calculate_grid_export_before_evening()` helper function

### Changed
- Appliance signal now shows export amount in RED reason when below threshold

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
