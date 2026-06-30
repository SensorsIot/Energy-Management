# Project — Build & release rules

Binding rules for changing and shipping any add-on. These hold for every change.

## Version bump (required on every add-on change)
Every add-on change requires a version bump **before commit/push**. Bump the patch version in
**both** `config.yaml` and `run.py` (`__version__`) for the affected add-on. Without it, HA
Supervisor does not detect the update and `ha addons rebuild` uses stale code.

## Tests pass before commit
Tests must pass before any commit. Secrets are never hardcoded — load them from environment
variables (the location of a credential may be documented; the secret never is).

## Command reference

```bash
# Run all energymanager tests
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

## Host access
Host operations (SSH, InfluxDB, Grafana, Home Assistant, Docker) go through the `remote-connections`
skill — see [`../../Handbook.md`](../../Handbook.md).
