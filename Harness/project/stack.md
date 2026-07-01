# Project — Stack & runtime architecture

Energy Management System is a Home Assistant add-on suite for household energy optimization in
Lausen (BL), Switzerland: PV forecasting, load forecasting, battery/EV/appliance control, and OCPP
wallbox integration. It runs as four independent add-ons (see the *Components* table in
[`../../STRUCTURE.md`](../../STRUCTURE.md)).

## Languages & runtime
- **Python 3.11** — Ruff `target-version = "py311"` (`pyproject.toml`).
- **Home Assistant Supervisor add-on** — each add-on builds from the HA Debian Bookworm base
  (`build.yaml`: `ghcr.io/home-assistant/{arch}-base-debian:bookworm`) via `ARG BUILD_FROM`, with an
  **s6-overlay** service under `rootfs/etc/s6-overlay/s6-rc.d/<addon>/`. Architectures: `aarch64`,
  `amd64`, `armv7`.

## Tooling
- **Ruff** — formatter + linter, line length 100, rule set `E,W,F,B,UP,D` (annotation rules `ANN`
  omitted — typing is gradual, not enforced). Config in `pyproject.toml`.
- **mypy** — available in dev requirements.
- **pytest + pytest-cov** — tests.

Dev dependencies are consolidated in `requirements-dev.txt`.

## Per-add-on key libraries
| Add-on | Libraries (beyond the shared `influxdb-client`, `PyYAML`, `requests`) |
|--------|------------------------------------------------------------------------|
| energy-manager | `pandas`, `numpy`, `APScheduler`, `python-dateutil` |
| swiss-solar-forecast | `pvlib`, `xarray`, `cfgrib`, `eccodes`, `numpy`, `pandas`, `APScheduler` |
| load-forecast | `pandas`, `numpy`, `croniter` |
| ocpp-server | `websockets`, `ocpp`, `aiohttp` |

## Data & integration
- **InfluxDB 2.x** (`influxdb-client`) — time-series storage; add-ons cooperate only through its
  buckets and Home Assistant entities.
- **Grafana** — dashboards (operator queries in [`../../Handbook.md`](../../Handbook.md)).
- **OCPP 1.6j** (`ocpp`) — wallbox protocol, in `ocpp-server`.

Naming of buckets, entities, topics, and other identifiers is in [`naming.md`](naming.md).
Per-add-on build/structure detail is in [`modules/`](modules/).
