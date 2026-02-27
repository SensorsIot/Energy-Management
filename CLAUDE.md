# Energy Management System

Home Assistant add-on suite for household energy optimization in Lausen (BL), Switzerland.
PV forecasting, load forecasting, battery/EV/appliance control, OCPP wallbox integration.

## Tech Stack

- Python 3.11
- Home Assistant Supervisor Add-on architecture (s6-overlay, Debian Bookworm base)
- InfluxDB 2.x (time-series storage)
- Grafana (dashboards)
- OCPP 1.6j (wallbox protocol)
- Formatter/linter: Ruff
- Tests: pytest + pytest-cov

## Project Structure

```
energymanager/          Main add-on — battery optimizer, EV charging, appliance signals
  run.py                Entry point (scheduler, config loading)
  src/                  Business logic modules
  tests/                pytest tests
  testdata/             Local dev config fixture

loadforecast/           Add-on — statistical load prediction (P10/P50/P90 per 15-min)
  run.py                Entry point
  src/                  Prediction logic

swisssolarforecast/     Add-on — PV production forecast (ICON weather + pvlib model)
  run.py                Entry point (scheduler)
  src/                  GRIB parsing, PV model, shading, accuracy tracking

ocpp-server/            Add-on — OCPP 1.6j wallbox server, publishes HA entities
  run.py                Entry point
  src/                  OCPP handler and HA entity management

scripts/                Standalone utilities (InfluxDB migration, Smart car status)
Documents/              Specifications (see below)
```

## Commands

```bash
# Run all tests
python -m pytest energymanager/tests/ -v

# Run a specific test file
python -m pytest energymanager/tests/test_ev_state_machine.py -v

# Run with coverage
python -m pytest energymanager/tests/ --cov=energymanager/src --cov-report=term-missing

# Run swisssolarforecast tests
python -m pytest swisssolarforecast/test_pipeline.py -v

# Run ocpp-server tests
python -m pytest ocpp-server/tests/ -v

# Lint all add-ons
ruff check energymanager/ loadforecast/ ocpp-server/ swisssolarforecast/

# Format all add-ons
ruff format energymanager/ loadforecast/ ocpp-server/ swisssolarforecast/

# Install all dev dependencies
pip install -r requirements-dev.txt
```

## Code Style

- Use `from __future__ import annotations` in new modules
- Type hints on function signatures (use `X | None`)
- Dataclasses for structured return values (not dicts)
- Enums that inherit `str` for HA-compatible state values (`class EVState(str, Enum)`)
- Logging via `logger = logging.getLogger(__name__)` — no print statements
- All times UTC internally, convert to `Europe/Zurich` only for display/logs

## Specifications

- `Documents/EnergymanagementV2_fsd.md` -- main energy management logic
- `Documents/Home-Installation-fsd.md` -- physical installation details
- `ocpp-server/docs/ocpp-server-fsd.md` -- OCPP wallbox server spec
- `Documents/Smart-Car-Interface.md` -- Smart car API integration

## Key Conventions

- Config loaded from YAML, secrets from environment variables — never hardcode secrets
- HA entities created via REST API (`POST /api/states/`), not platform-backed services
- Wallbox power limit set via `set_sensor_state`, not `number.set_value` (REST-created entity)
- Grid power: prefer M-Bus smart meter (`sensor.grid_power`) if fresh (<30s), fallback to Huawei DTSU
- EV solar charging uses forecast-based strategy: SOC simulation with wallbox load to find optimal amp level
- Battery is the buffer: covers gap between coarse amp steps (690W on 3-phase) and actual surplus
- EV solar entry uses `sensor.surplus_power` (= solar - house_load); no closed-loop formula
- Battery protection: dynamic target = min(80%, baseline SOC at 21:00 without EV)
- Version bumps: update both `config.yaml` version field and `run.py` `__version__`
- **Every add-on change requires a version bump before commit/push.** Bump the patch version in both `config.yaml` and `run.py` for the affected add-on. Without a version bump, HA Supervisor won't detect the update and `ha addons rebuild` will use stale code.

## Gotchas / Do Not

- **Do not use** `number.set_value` service for wallbox — the entity is REST-created, service silently fails
- **Do not cache** HA tokens — `HAClient.token` property re-reads from env each call (token rotation)
- **InfluxDB entity_id** tags use short form: `sensor.smart_battery` → `smart_battery` (strip domain prefix)
- Tests must pass before any commit: `python -m pytest energymanager/tests/ -v`
- **Do not commit add-on changes without bumping the version** — see Key Conventions above

## Host Access

See `remote-connections` skill for SSH, InfluxDB, Grafana, and Docker details.
