# Energy Management Add-ons

[![Home Assistant Add-ons](https://img.shields.io/badge/Home%20Assistant-Add--ons-blue?logo=home-assistant)](https://www.home-assistant.io/addons/)
[![GitHub](https://img.shields.io/badge/GitHub-SensorsIot-black?logo=github)](https://github.com/SensorsIot/Energy-Management)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Home Assistant add-ons for energy forecasting and optimization.

## Add-ons

| Add-on | Description | Status |
|--------|-------------|--------|
| [SwissSolarForecast](swisssolarforecast/) | PV power forecast using MeteoSwiss ICON data | ![Version](https://img.shields.io/badge/v1.1.1-stable-green) |
| [LoadForecast](loadforecast/) | Statistical load prediction from historical data | ![Version](https://img.shields.io/badge/v1.2.0-stable-green) |
| [EnergyManager](energymanager/) | Battery/EV/appliance optimization using forecasts | ![Version](https://img.shields.io/badge/v1.4.1-stable-green) |

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐
│  SwissSolarForecast │     │    LoadForecast     │
│  ─────────────────  │     │  ─────────────────  │
│  MeteoSwiss ICON    │     │  Historical Data    │
│  → PV Forecast      │     │  → Load Forecast    │
│  (P10/P50/P90)      │     │  (P10/P50/P90)      │
└──────────┬──────────┘     └──────────┬──────────┘
           │                           │
           │     ┌─────────────┐       │
           └────►│  InfluxDB   │◄──────┘
                 │  ─────────  │
                 │  pv_forecast│
                 │  load_forecast
                 └──────┬──────┘
                        │
                        ▼
              ┌─────────────────┐
              │  EnergyManager  │
              │  ─────────────  │
              │  Battery Control│
              │  Appliance Signal
              │  SOC Forecast   │
              └─────────────────┘
```

## Installation

1. **Add Repository**

   In Home Assistant, go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**

   Add: `https://github.com/SensorsIot/Energy-Management`

2. **Install Add-ons**

   Find "Energy Management Add-ons" in the store and install the add-ons you need.

3. **Configure**

   Each add-on requires a user configuration file in `/config/`:
   - `/config/swisssolarforecast.yaml`
   - `/config/loadforecast.yaml`
   - `/config/energymanager.yaml`

   At minimum, each needs the InfluxDB token:
   ```yaml
   influxdb:
     token: "your-influxdb-token"
   ```

## Requirements

- Home Assistant OS or Supervised installation
- InfluxDB 2.x (for data storage)
- For SwissSolarForecast: Location in Switzerland (MeteoSwiss data coverage)
- For LoadForecast: Historical power consumption data in InfluxDB
- For EnergyManager: PV and load forecasts from the other add-ons

## Data Flow

### SwissSolarForecast
```
MeteoSwiss STAC API → GRIB files → pvlib → InfluxDB (pv_forecast bucket)
```

### LoadForecast
```
InfluxDB (HomeAssistant bucket) → Statistical Model → InfluxDB (load_forecast bucket)
```

### EnergyManager
```
InfluxDB (pv_forecast + load_forecast) → Optimization → Battery/Appliance Control
```

## Output Format

All add-ons produce **15-minute resolution** forecasts with **P10/P50/P90 percentiles**:

- **P10**: Conservative estimate (10th percentile)
- **P50**: Expected value (median)
- **P90**: Optimistic estimate (90th percentile)

This format is designed for integration with Model Predictive Control (MPC) and optimization systems.

## Documentation

See [Energymanagement_fsd.md](Documents/Energymanagement_fsd.md) for the complete Functional Specification Document.

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Credits

- [MeteoSwiss](https://www.meteoswiss.admin.ch/) for open weather data
- [pvlib](https://pvlib-python.readthedocs.io/) for PV modeling
- [Home Assistant](https://www.home-assistant.io/) community
