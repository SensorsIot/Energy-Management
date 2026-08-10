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

### One add-on per pytest session
Each add-on ships its own top-level `run.py` and `src/` package, and its tests import them by those
bare names. More than one add-on in a single pytest session resolves `src` to whichever add-on
imported first, and the rest fail to collect. Add-ons are separate containers at runtime and are
tested the same way, so run one suite per process — `tools/run_tests.sh` does that for all of them.
The repo-root `conftest.py` rejects a mixed or whole-repo session with an explanation rather than
letting it fail obscurely; a bare `pytest` at the repo root is therefore an error, not a full run.

```bash
# Run every add-on's suite, each in its own process (extra args go to pytest)
tools/run_tests.sh
tools/run_tests.sh -q

# Run all energy-manager tests
python -m pytest energy-manager/tests/ -v

# Run a specific test file
python -m pytest energy-manager/tests/test_ev_state_machine.py -v

# Run with coverage
python -m pytest energy-manager/tests/ --cov=energy-manager/src --cov-report=term-missing

# Run swiss-solar-forecast tests
python -m pytest swiss-solar-forecast/test_pipeline.py -v

# Run ocpp-server tests
python -m pytest ocpp-server/tests/ -v

# Lint all add-ons
ruff check energy-manager/ load-forecast/ ocpp-server/ swiss-solar-forecast/

# Format all add-ons
ruff format energy-manager/ load-forecast/ ocpp-server/ swiss-solar-forecast/

# Install all dev dependencies
pip install -r requirements-dev.txt
```

## Host access
Host operations (SSH, InfluxDB, Grafana, Home Assistant, Docker) go through the `remote-connections`
skill — see [`../../Handbook.md`](../../Handbook.md).
