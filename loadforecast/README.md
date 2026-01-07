# LoadForecast

![Version](https://img.shields.io/badge/version-1.0.1-blue)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green)
![Supports armv7](https://img.shields.io/badge/armv7-yes-green)
![License](https://img.shields.io/badge/license-MIT-blue)

Statistical load forecast using historical consumption data from Home Assistant.

## Features

- **Time-of-Day Profiles**: Builds consumption patterns from historical data
- **P10/P50/P90 Percentiles**: Provides low, median, and high consumption forecasts
- **15-Minute Resolution**: Matches common smart meter intervals
- **48-Hour Horizon**: Configurable forecast length
- **InfluxDB Integration**: Reads from Home Assistant bucket, writes to forecast bucket

## How It Works

1. **Data Collection**: Queries historical load data from InfluxDB (default: 90 days)
2. **Profile Building**: Groups data by 15-minute time slots (96 slots per day)
3. **Percentile Calculation**: Computes P10/P50/P90 for each slot
4. **Forecast Generation**: Projects the profile into the future

## Installation

1. Add this repository to Home Assistant:
   ```
   https://github.com/SensorsIot/Energy-Management
   ```

2. Install "LoadForecast" from the Add-on Store

3. Configure InfluxDB connection and load sensor

## Configuration

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  token: "your-influxdb-token"
  org: "your-org"
  source_bucket: "HomeAssistant"
  target_bucket: "load_forecast"

load_sensor:
  entity_id: "load_power"

forecast:
  history_days: 90
  horizon_hours: 48

schedule:
  cron: "15 * * * *"
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `source_bucket` | InfluxDB bucket with historical data | `HomeAssistant` |
| `target_bucket` | InfluxDB bucket for forecast output | `load_forecast` |
| `entity_id` | Home Assistant entity for load power | `load_power` |
| `history_days` | Days of history to analyze | `90` |
| `horizon_hours` | Forecast horizon in hours | `48` |
| `cron` | Schedule for forecast updates | `15 * * * *` |

## Output Data

Writes to InfluxDB measurement `load_forecast`:

| Field | Description |
|-------|-------------|
| `energy_wh_p10` | Energy per 15-min period (10th percentile - low estimate) |
| `energy_wh_p50` | Energy per 15-min period (median - expected) |
| `energy_wh_p90` | Energy per 15-min period (90th percentile - high estimate) |

Tags: `model`, `run_time`

## Requirements

- InfluxDB 2.x with historical load data
- Home Assistant sensor tracking household power consumption
- At least 7 days of historical data (90+ days recommended)

## Schedule

By default, forecasts are regenerated every hour at minute 15 (`15 * * * *`).

Adjust the cron expression for more or less frequent updates:
- Every 15 minutes: `*/15 * * * *`
- Every 6 hours: `15 */6 * * *`
- Once daily at midnight: `0 0 * * *`

## Related Add-ons

- **SwissSolarForecast**: PV power forecast using MeteoSwiss weather data
- **EnergyManager**: (Coming soon) Combines PV and load forecasts for optimization

## License

MIT License - See LICENSE file for details.
