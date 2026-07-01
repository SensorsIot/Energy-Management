# Naming & Testing — Portable Standards Guide

A self-contained transfer document: the naming conventions and testing structure this project
adopted, plus the **process** for applying them to an existing codebase. Written to be copied into
another project and adapted — the data-store examples (InfluxDB, Home Assistant, MQTT) are
illustrative; map them to your own stack.

> **Provenance.** This guide is a deliberate synthesis of four canonical homes in this repo —
> `Harness/project/naming.md`, `Harness/standards/testing.md`, `Harness/project/testing.md`, and the
> `documentation` skill's placement rule. It restates them on purpose for portability; it is **not**
> a second canonical home. In-project, edit the Harness files, not this one.

---

# Part A — Naming conventions

## A.1 Two tiers (the single most important idea)

Every identifier falls into one of two tiers, and the tier decides how expensive a rename is:

- **Internal identifiers** — code-local names: modules, functions, classes, constants, local
  variables. They carry no stored state and no external consumer. **Rename freely.**
- **External identifiers** — names that cross a boundary and get **stored or read by something
  else**: database keys, API fields, event/topic names, entity IDs, service/component slugs, config
  keys, published file names. These are **contracts**. Dashboards, other services, historical data,
  and integrations bind to the exact string. **Renaming one is a migration, not an edit** (§A.5).

Get this split right first. Most naming mistakes are treating an external identifier as if it were
internal (breaking history/consumers) or vice-versa (over-ceremony on a local variable).

## A.2 Anchor to recognized standards, not to habit

Make the convention *strategic*: cite the authority for each rule so it's defensible and stable, and
treat existing code as a *conforming example*, not the source of the rule.

| Source | Governs | Applies to |
|---|---|---|
| **ISO/IEC 11179-5** — naming & identification of data elements | name structure: object → property → representation term | DB fields/tags, entity IDs, config keys |
| **ISO/IEC 80000 + SI** | canonical unit symbols (`W`, `Wh`, `kWh`, `%`, `s`, `m`) | unit suffixes on physical quantities |
| **ISO 8601** | date/time representation | timestamp fields and values |
| **PEP 8** (or your language's style guide) | identifier casing | modules, functions, classes, constants |
| **Prometheus / OpenTelemetry semantic conventions** | metric naming; **unit-as-suffix** | measurement field suffixes (`_seconds`, `_bytes`, `_w`) |
| **Platform conventions** (e.g. Home Assistant entity naming) | `domain.object_id`, snake_case | UI/automation entities |
| **Protocol/vendor guidance** (e.g. OASIS MQTT topics; DB schema-design guides) | topic hierarchy; schema keys | message topics, table/measurement schema |

## A.3 Principles

1. **Name by role and meaning**, never by type or implementation. No Hungarian notation.
2. **One case convention per namespace.** Prefer `snake_case` for every data / config / entity
   identifier; reserve `PascalCase` and `UPPER_SNAKE` for classes and constants (per your style guide).
3. **Encode the unit** on any physical quantity, using the lowercased SI symbol: `_w`, `_wh`,
   `_kwh`, `_percent`, `_s`. Never a bare `power` / `energy` / `soc` where a unit is meaningful.
4. **Structure data-element names object → property → representation** (ISO 11179): scope prefix,
   then property, then unit/representation term — e.g. `power_w_p50`, `battery_min_soc_forecast_48h`.
5. **Spell words out.** Only established domain acronyms are allowed, and each keeps **one fixed
   case everywhere** (e.g. always `SOC`, `PV`, `EV`, never `Soc`/`Icon` in one place and `SOC`/`ICON`
   in another).
6. **Namespace by scope prefix** on entities/config (`wallbox_`, `battery_`, `grid_`, …).
7. **External names are stable contracts** — a rename is a migration (§A.5).

## A.4 Conventions by identifier class

| Class | Convention | Example |
|---|---|---|
| Module / file | `snake_case.py` | `forecast_reader.py` |
| Function / variable | `snake_case` | `build_solar_candidates` |
| Class | `PascalCase`, fixed-case acronyms | `EVBatteryOptimizer`, `SOCSimulator` |
| Constant / module-level set | `UPPER_SNAKE` | `PROXY_LIVE_STATUSES` |
| Enum member value (if it crosses a boundary) | the exact string the consumer carries | `EVState.SOLAR → "solar"` |
| UI/platform entity ID | `domain.snake_case`, scope-prefixed | `sensor.house_load_power` |
| DB bucket / table | `snake_case` | `pv_forecast` |
| DB measurement | `snake_case` noun | `energy_balance` |
| DB tag / label key | `snake_case`, low cardinality | `model` |
| DB field | `snake_case`, **unit-suffixed** | `power_w_p50`, `car_soc_percent` |
| Timestamp field/value | ISO 8601 string | `run_time` |
| Message topic | lowercase, `/`-delimited, no leading `/` | `mbus-proxy/power` |
| Message / JSON payload key | `snake_case` | `dtsu`, `active` |
| Service / component slug | lowercase `kebab-case` | `ocpp-server`, `energy-manager` |
| Config key | `snake_case`, dotted namespace | `ev_charging.reserve_percent` |
| Doc / spec file | `kebab-case`, `<component>-fsd.md` | `energy-manager-fsd.md` |

## A.5 Stability — external names are contracts

An external identifier is bound by consumers that store or read the exact string (historical data,
dashboards, sibling services, automations). Changing one is a **migration**:

1. Rename it in **every** consumer in the same change.
2. **Migrate existing stored history in place** — rewrite the key/field/measurement on past records so
   the series stays continuous. **Never orphan** old data under the old name.

Internal identifiers hold no stored state and rename freely.

## A.6 Main body vs appendix — placement within a document

A companion rule to naming: where content sits in a spec.

- A **numbered chapter** is part of the contract a human reads to understand what the system is and
  does — behaviour, rules, interfaces, and **test cases** (test cases are spec, never an appendix).
- An **appendix** is reference material an implementer looks up — bulk parameter tables, full config
  listings, raw third-party payloads kept verbatim.

Test: *would a human skip this to understand the system, and only an implementer consult it?* →
appendix. *Is it needed to understand what the system is/does?* → main body. This is a judgment rule;
a linter can't enforce it.

## A.7 Exempt / externally-owned names

Names created and owned by an external system are carried **verbatim** and are out of scope for the
convention — document them as exemptions rather than "fixing" them: e.g. a platform-managed bucket
(`HomeAssistant`), vendor product/model names (`SUN2000`, `DTSU`), or a topic published by
third-party firmware (`MBUS-PROXY/power`).

---

# Part B — Testing structure

## B.1 The pyramid — four levels, each with a home

| Level | Verifies | Spec home (WHAT) | Code home |
|---|---|---|---|
| **Unit** | one function/class in isolation | the component's FSD test chapter | `tests/` beside the component |
| **Integration** | two+ modules cooperating | same chapter, integration section | `tests/` |
| **End-to-end** | the whole system against mocked externals | same chapter, E2E section | `tests/` |
| **Live / observer** | invariants against the *running* system | same chapter | a passive observer test |

## B.2 The rules

- **Every behaviour rule has a test case.** If a rule in the spec has no case, that's a gap to close.
- **Every change ships its test.** No behaviour change lands without the test that pins it.
- **Specs live with the behaviour**, code lives beside the component — WHAT and code stay near what
  they describe.
- **Tests pass before commit.**
- **No silent gaps.** If coverage is bounded (a level not built, a case pending mocks), say so
  explicitly in the index — an unstated gap reads as "covered" when it isn't.

## B.3 The gate — testing is a required build step

Fold testing into the build workflow as a non-optional gate, not an afterthought:

1. **Locate the contract** — find/define the spec rule the change serves.
2. **Build** to the rule.
3. **Test — the gate.** A change is **not done** until its test case exists (in the owning spec
   chapter) and its test passes. **A bug fix adds its regression test first.**
4. **Reconcile the docs** — the spec absorbs the changed behaviour.
5. **Verify** — run the suite green before commit.

## B.4 Where test cases are defined — one pattern, one index

- Each component defines its test cases in a **numbered chapter of its own spec** (not an appendix —
  see §A.6), with stable case IDs.
- A single **testing index** names, per component, the chapter where cases live and the code file
  that implements them, plus the **known gaps** (levels specified but not built). The index is the
  overview; each case's status in its spec chapter is authoritative.
- Surface testing in the top-level doc map so it's first-class, not buried.

---

# Part C — Adopting these in an existing project (the process)

The order that made this safe and reviewable:

**C.1 Write the standard first.** Author the naming and testing docs anchored to the sources (§A.2),
before touching code. Existing code becomes conforming examples, not the rulebook.

**C.2 Audit conformance by identifier class.** Go class by class (§A.4). For each, list where code
conforms and where it deviates. Report deviations with severity = the tier (§A.1): internal
deviations are cheap, external deviations are contract migrations.

**C.3 Split the work by tier.** Group fixes into *internal / safe* (free renames, dead-code removal,
class-casing, doc renames) and *external / contract* (needs history migration). Do the safe tier as
its own commit; treat each contract rename as a scoped migration.

**C.4 Dry-run before renaming.** For any rename, first **enumerate the full blast radius** — every
file and occurrence, grouped by risk — and get sign-off. Renames cascade further than they look (a
service slug is also a directory name, a config path, a spec filename, a service-manager unit).

**C.5 Rename safely — the traps that bite:**
- **A token can be two things at once.** The same string may be a code variable *and* an external
  field/key. Rename only the identifier; **leave the quoted string literal.** A blanket
  find-replace corrupts the contract. (Here: `battery_soc` was simultaneously a Python variable, a
  DB field, and a UI attribute key — surgical rename only.)
- **Substring collisions.** Verify your token isn't a substring of an unrelated name before a global
  replace (here: slug `energymanager` vs org `energymanagement` — they diverge, so it was safe; but
  check every time).
- **Package-name legality.** A `kebab-case` service slug can't be a Python package — imports must go
  through a neutral path (`from src.x`), not the hyphenated directory name.
- **Protect identity webs.** When updating notes/docs that cross-link by ID (wiki-links, index
  targets, front-matter `name:`), replace *content* references but **protect the IDs**, or the links
  break.

**C.6 Verify every rename.** Compile everything, run the **full test suite** (the pyramid pays off
here — imports and contracts are exercised), run the doc linter, and check no cross-references broke.

**C.7 Migrate external names as history-preserving migrations** (§A.5), separately and deliberately —
never bundled into an unrelated change, and never as a blanket replace.

**C.8 Institutionalize it — so it stays enforced after the cleanup.** A one-time audit decays without
governance. Bake the rules into how the project runs going forward:
- **Route new content through a triage** that explicitly names where naming decisions go (the naming
  standard) and where test cases go (the owning spec's numbered test chapter) — so the next author
  places them right without re-deriving the rule.
- **Make testing a build gate** (§B.3), not a review afterthought — a change isn't done until its
  test case and test exist.
- **Surface naming and testing in the top-level doc map** as first-class entries, so they're
  discoverable rather than buried in a subfolder.

## Adapting to your stack

Replace the data-store nouns with yours: *InfluxDB bucket/measurement/tag/field* → your database's
table/column/label; *Home Assistant entity* → your platform's addressable object; *MQTT topic* →
your event/queue name; *add-on slug* → your service/package identifier. The two-tier model (§A.1),
the source anchors (§A.2), the unit-suffix and object→property→representation rules (§A.3), and the
migration discipline (§A.5, C.4–C.7) are stack-independent.
