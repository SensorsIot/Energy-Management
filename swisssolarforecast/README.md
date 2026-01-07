# SwissSolarForecast

![Version](https://img.shields.io/badge/version-1.0.2-blue)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green)
![Supports armv7](https://img.shields.io/badge/armv7-yes-green)
![License](https://img.shields.io/badge/license-MIT-blue)

Swiss PV power forecast using MeteoSwiss ICON ensemble data with pvlib.

## Features

- **Ensemble Forecasts**: Uses 11 ensemble members from ICON-CH1/CH2 for uncertainty quantification
- **P10/P50/P90 Percentiles**: Provides pessimistic, median, and optimistic power forecasts
- **Hybrid Model**: Combines ICON-CH1 (1km, 33h) and ICON-CH2 (2.1km, 48h) for optimal accuracy
- **Per-Inverter Forecasts**: Supports multiple plants, inverters, and string configurations
- **15-Minute Resolution**: Aligned timestamps for MPC/optimization integration
- **InfluxDB Output**: Writes forecasts to InfluxDB for visualization and further processing

## Data Source

Weather data is fetched from the [MeteoSwiss Open Data](https://www.meteoswiss.admin.ch/services-and-publications/applications/opendata.html) STAC API:

| Model | Resolution | Horizon | Members | Update |
|-------|------------|---------|---------|--------|
| ICON-CH1 | 1 km | 33h | 11 | Every 3h |
| ICON-CH2 | 2.1 km | 120h | 21 | Every 6h |

## Installation

1. Add this repository to Home Assistant:
   ```
   https://github.com/SensorsIot/Energy-Management
   ```

2. Install "SwissSolarForecast" from the Add-on Store

3. Configure your PV system in the add-on settings

## Configuration

### InfluxDB Connection

```yaml
influxdb:
  host: "192.168.0.203"
  port: 8087
  token: "your-influxdb-token"
  org: "your-org"
  bucket: "pv_forecast"
```

### PV System Configuration

Create `/config/swisssolarforecast.yaml`:

```yaml
panels:
  - id: "Panel400"
    model: "Generic 400W"
    pdc0: 400
    gamma_pdc: -0.0035

plants:
  - name: "House"
    location:
      latitude: 47.475
      longitude: 7.767
      altitude: 330
      timezone: "Europe/Zurich"
    inverters:
      - name: "MainInverter"
        max_power: 10000
        efficiency: 0.85
        strings:
          - name: "South"
            azimuth: 180
            tilt: 30
            panel: "Panel400"
            count: 20
```

## Output Data

Writes to InfluxDB measurement `pv_forecast`:

| Field | Description |
|-------|-------------|
| `power_w_p10` | Power in watts (10th percentile) |
| `power_w_p50` | Power in watts (median) |
| `power_w_p90` | Power in watts (90th percentile) |
| `energy_wh_p10` | Energy per 15-min period (10th percentile) |
| `energy_wh_p50` | Energy per 15-min period (median) |
| `energy_wh_p90` | Energy per 15-min period (90th percentile) |
| `ghi` | Global Horizontal Irradiance (W/m²) |
| `temp_air` | Air temperature (°C) |

Tags: `inverter`, `model`, `run_time`

## Schedule

Default fetch and calculation schedule:

- **ICON-CH1**: Every 3 hours (30 min after model run)
- **ICON-CH2**: Every 6 hours (45 min after model run)
- **Forecast Calculation**: Every 15 minutes

## Related Add-ons

- **LoadForecast**: Statistical load prediction for consumption forecasting
- **EnergyManager**: (Coming soon) Combines PV and load forecasts for optimization

## License

MIT License - See LICENSE file for details.
