# Project — Naming conventions

How every identifier in this suite is named. Names are strategic, anchored to **international
standards** (sources below); the repo's identifiers are conforming examples, not the source of the
rules.

Two tiers, with different stakes:

- **Internal identifiers** — Python modules, functions, classes, constants. Carry no stored state;
  rename freely.
- **External identifiers** — HA `entity_id`s, InfluxDB buckets / measurements / tags / fields, MQTT
  topics, add-on slugs, config keys, published doc names. These are **contracts**: dashboards, other
  add-ons, and stored history bind to the exact string. Renaming one is a **migration** — see
  [Stability](#stability--external-names-are-contracts).

## Sources

| Source | Governs | Applied to |
|---|---|---|
| **ISO/IEC 11179-5** — Naming and identification principles for data elements | data-element name structure (object → property → representation term) | entity_ids, InfluxDB fields/tags, config keys |
| **ISO/IEC 80000** + SI brochure | canonical unit symbols (`W`, `Wh`, `kWh`, `%`) | unit suffixes on physical quantities |
| **ISO 8601** | date/time representation | timestamp fields and values |
| **PEP 8** | Python identifier style | modules, functions, classes, constants |
| **Prometheus / OpenTelemetry semantic conventions** | metric naming; unit-as-suffix pattern | `_w` / `_wh` / `_percent` field suffixes |
| **Home Assistant entity naming** (HA developer docs) | `domain.object_id`, snake_case object_ids | HA entities |
| **OASIS MQTT** + topic best practice | topic hierarchy (lowercase, `/`-delimited, no leading `/`) | MQTT topics |
| **InfluxData schema-design guidance** | snake_case, low tag cardinality, no reserved keywords | InfluxDB schema |

## Principles

1. **Name by role and meaning**, never by type or implementation — no Hungarian notation, no
   `str`/`dict` in a name.
2. **One case convention per namespace.** snake_case for every data, config, and entity identifier;
   PascalCase and UPPER_SNAKE only for Python classes and constants (PEP 8).
3. **Encode the unit** on any physical quantity, using the lowercased SI symbol (ISO 80000):
   `_w`, `_wh`, `_kwh`, `_percent`. Never a bare `power` / `energy` / `soc` where a unit is meaningful.
4. **Structure a data-element name object → property → representation** (ISO/IEC 11179): scope prefix,
   then property, then unit/representation term — e.g. `power_w_p50`, `battery_min_soc_forecast_48h`.
5. **Spell words out.** Only established domain acronyms are allowed: `PV`, `EV`, `SOC`, `OCPP`,
   `DTSU`, `MQTT`, `HA`, `SUN2000`. An acronym keeps one fixed case everywhere it appears.
6. **Namespace by scope prefix** on entities and config keys: `wallbox_`, `battery_`, `ev_`,
   `car_` / `smart_`, `grid_`.
7. **External names are stable contracts** — treat a rename as a migration, not an edit (below).

## Conventions by identifier class

| Class | Convention | Example (in-repo) | Source |
|---|---|---|---|
| Python module / file | `snake_case.py` | `forecast_reader.py` | PEP 8 |
| Python function / variable | `snake_case` | `build_solar_candidates` | PEP 8 |
| Python class | `PascalCase` | `EVBatteryOptimizer` | PEP 8 |
| Python constant / module-level set | `UPPER_SNAKE` | `_PROXY_LIVE_STATUSES` | PEP 8 |
| `StrEnum` member value | the exact string the HA entity carries | `EVState.SOLAR → "solar"` | PEP 8 + HA |
| HA `entity_id` | `domain.snake_case`, scope-prefixed | `sensor.house_load_power`, `number.wallbox_power_limit` | HA + 11179 |
| InfluxDB bucket | `snake_case` | `pv_forecast`, `energy_manager` | InfluxData + 11179 |
| InfluxDB measurement | `snake_case` noun | `load_forecast`, `energy_balance` | InfluxData |
| InfluxDB tag key | `snake_case`, low cardinality | `model` | InfluxData + 11179 |
| InfluxDB field | `snake_case`, **unit-suffixed** | `power_w_p50`, `car_soc_percent` | 11179 + 80000 + Prometheus/OTel |
| Timestamp field / value | ISO 8601 string | `run_time` | ISO 8601 |
| MQTT topic | lowercase, `/`-delimited, no leading `/` | `mbus-proxy/power` *(target)* | OASIS MQTT |
| MQTT / JSON payload key | `snake_case` | `dtsu`, `wallbox`, `sun2000`, `active` | 11179 |
| Add-on slug | lowercase `kebab-case` | `ocpp-server` | HA add-on |
| Config key (YAML) | `snake_case`, dotted namespace | `ev_charging.reserve_percent` | 11179 |
| Doc / spec file | `kebab-case`, `<addon>-fsd.md` | `energy-manager-fsd.md` | project |

## Stability — external names are contracts

An external identifier is bound by consumers that store or read the exact string: InfluxDB history,
Grafana panels, other add-ons, HA automations, dashboards. Changing one is a **migration**:

1. Rename it in **every** consumer in the same change.
2. **Migrate existing InfluxDB history in place** — rewrite the bucket/measurement/tag/field on past
   points so the series stays continuous. Never orphan old data under the old name.

Internal Python identifiers hold no stored state and rename freely.

## Conforming and exempt names

**Owned by an external system** — carried verbatim, out of scope for this convention:

- `HomeAssistant` bucket — created and named by Home Assistant.
- `SUN2000`, `DTSU` — Huawei product/model names.
- MQTT topic `MBUS-PROXY/power` — published by the ESP32 Modbus-proxy firmware, not this repo. The
  repo's own OCPP topic (`wallbox`) is lowercase and conforms.

Code style beyond naming is in [`code-style.md`](code-style.md); the data-store architecture these
identifiers live in is in [`stack.md`](stack.md).
