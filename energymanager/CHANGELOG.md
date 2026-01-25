# EnergyManager Changelog

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
