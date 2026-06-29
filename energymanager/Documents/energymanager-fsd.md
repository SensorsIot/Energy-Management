# EnergyManager Add-on — Functional Specification

EnergyManager is the optimization brain of the suite. It consumes the PV and load forecasts plus
live Home Assistant state and tariff windows, and controls the home battery, EV wallbox, and
high-power appliances.

**The canonical, self-contained FSD for this add-on is
[EnergymanagementV2_fsd.md](../../Documents/EnergymanagementV2_fsd.md)** (repo root). It holds the
full battery / EV / appliance control logic, the system architecture, the Home Assistant entity
registry, the InfluxDB schema, the configuration reference, and the changelog. This file is a
pointer — spec content is not duplicated here.

## Where to find things in EnergymanagementV2_fsd.md

| Topic | Location |
|-------|----------|
| System overview, architecture, data flow, HA entities, sign conventions, config reference | Chapter 1 |
| Home-battery discharge, charge ceiling, export-peak-shaving (Topics 3–5) | §4.2 |
| EV charge decision & charge power (Topics 1–2) | §4.3 |
| Appliance signal (Topic 6) | §4.4 |
| InfluxDB output (`energy_manager` bucket, `soc_forecast`, …) | §4.5 |
| Forecast accuracy tracking | Chapter 5 |
| Changelog (per add-on version) | bottom of the file |

## Interfaces

- **Consumes:** the `pv_forecast` bucket (SwissSolarForecast) and the `load_forecast` bucket
  (LoadForecast) from InfluxDB; live state and tariff/SOC entities from Home Assistant.
- **Produces:** Home Assistant control entities (home-battery discharge power, wallbox power
  setpoint, appliance signal) and the `energy_manager` bucket. The wallbox link is bridged by the
  **OCPP Server** add-on, which turns the setpoint into OCPP commands.

## Code & tests

- `energymanager/run.py` — entry point (15-min optimization loop + 10-s EV control loop)
- `energymanager/src/` — business logic modules
- `energymanager/tests/` — pytest suite (`python -m pytest energymanager/tests/ -v`)

## Changelog

- 2026-06-29: Reduced to a pointer to `EnergymanagementV2_fsd.md` (the canonical EnergyManager FSD).
  Removed the parallel partial spec that had drifted from it.
