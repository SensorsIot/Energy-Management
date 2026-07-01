# Energy Management System — documentation map

Three planes, one index. This file is the **doc index** and the **overview that links to every
distributed doc**: it names the canonical home of each kind of documentation, and is where the
`documentation` skill resolves its role bindings. (The AI-assistant config — `CLAUDE.md` — is
**not** project documentation; don't treat it as the canonical source.)

| Plane | Question it answers | Canonical home |
|---|---|---|
| **WHAT** | what the system is / does | *Components* below (distributed, per add-on) |
| **HOW** | how it is built / changed | `Harness/` — index `00-Overview.md`, entry `AI-Workflow.md`, project-wide rules in `project/`, module-specific HOW in `project/modules/<addon>.md` |
| **OPERATE** | how it is run | `Handbook.md` |

**Testing spans planes** and is a first-class part of the spec: the strategy and levels are HOW
([`Harness/standards/testing.md`](Harness/standards/testing.md)), each add-on's test-case specs are
WHAT (its FSD test chapter — see the *Tests* column in *Components*), and both are indexed together in
[`Harness/project/testing.md`](Harness/project/testing.md). Every behaviour rule has a test case;
every change ships its test (`AI-Workflow.md` step 3).

## Single source of truth (SSOT registry)

Each kind of content has **exactly one canonical home** (its SSOT). Every other doc that needs it
**links** to that home — it never restates it. There are no copies, only references; the doc-linter
(`tools/doc_lint.py`) flags any duplicated sentence (a copy that should be a link) and any broken
reference.

| Content kind | SSOT (canonical home) | Who references it |
|---|---|---|
| Suite overview / landing | `README.md` | — |
| This doc map / SSOT registry | `STRUCTURE.md` | the `documentation` skill, `CLAUDE.md` |
| An add-on's behaviour (WHAT) — entities, algorithms, schemas, config contract | that add-on's FSD (*Components* below) | the other add-ons' FSDs; the Harness module HOW |
| External interface contract owned by one add-on (e.g. Smart-car API) | a section of the owning add-on's FSD | — |
| Project-wide build contract & conventions (HOW) | `Harness/AI-Workflow.md`, `Harness/standards/`, `Harness/project/` | every FSD's *Build* reference |
| Testing strategy + test-case index (HOW) | `Harness/standards/testing.md` (levels/rules) + `Harness/project/testing.md` (index → the FSD chapter each add-on's cases live in) | `AI-Workflow.md` step 3; the FSD test chapters |
| Test-case specs (WHAT — acceptance criteria) | the owning add-on's FSD test chapter (the *Tests* column in *Components*) | `Harness/project/testing.md` indexes them |
| Naming conventions (HOW) — code, entities, InfluxDB, MQTT, slugs, files | `Harness/project/naming.md` (anchored to ISO/IEC 11179, ISO 80000, PEP 8) | `code-style.md`, `stack.md` |
| Module-specific build / architecture / file-layout / test-invocation (HOW) | `Harness/project/modules/<addon>.md` | that add-on's FSD |
| Operator procedures (OPERATE) — install, troubleshoot, dashboards | `Handbook.md` | the FSDs' *Operations* references |
| Documentation governance rules | `Harness/standards/documentation.md` + the `documentation` skill | `STRUCTURE.md` |
| Domain vocabulary | the owning add-on's FSD (no separate glossary) | — |

## Components (distributed WHAT)

One row per add-on that owns its own FSD beside its code. This is the overview's link-out: the
`documentation` skill routes a change to the FSD of the add-on it touches.

| Component | FSD (WHAT) | Test cases (WHAT) | Owns |
|---|---|---|---|
| `energy-manager` | [`energy-manager/Documents/energy-manager-fsd.md`](energy-manager/Documents/energy-manager-fsd.md) | [Chapter 6](energy-manager/Documents/energy-manager-fsd.md#chapter-6-test-cases) | Battery optimizer, EV charging, appliance signals. Includes the Smart-car API contract (`energy-manager/Documents/hello-smart-api.md`). |
| `load-forecast` | [`load-forecast/Documents/load-forecast-fsd.md`](load-forecast/Documents/load-forecast-fsd.md) | [§14](load-forecast/Documents/load-forecast-fsd.md#14-tests-and-validation) *(no code yet)* | Statistical load prediction (P10/P50/P90 per 15-min). |
| `swiss-solar-forecast` | [`swiss-solar-forecast/Documents/swiss-solar-forecast-fsd.md`](swiss-solar-forecast/Documents/swiss-solar-forecast-fsd.md) | [§16](swiss-solar-forecast/Documents/swiss-solar-forecast-fsd.md#16-tests-and-validation) | PV production forecast (ICON weather + pvlib model). |
| `ocpp-server` | [`ocpp-server/Documents/ocpp-server-fsd.md`](ocpp-server/Documents/ocpp-server-fsd.md) | [§8](ocpp-server/Documents/ocpp-server-fsd.md#8-test-cases) | OCPP 1.6j wallbox server; publishes HA entities. |

Interfaces stay where the contract lives: the Smart-car API contract is a doc owned by
`energy-manager`, not a top-level file. There is no cross-component interface that needs a shared
home.

## External specs (depended on, not owned)

Out-of-repo or third-party specs the project relies on. Linked, never copied in; changes go to
their owner, not here.

| Spec | Location | Owner |
|---|---|---|
| OCPP 1.6j | Open Charge Alliance specification | Open Charge Alliance |
| Home Assistant Supervisor add-on API | developers.home-assistant.io | Home Assistant |

## Top-level areas

Every top-level directory, what it holds, and its **lifecycle**.

| Path | Lifecycle | Purpose |
|---|---|---|
| `energy-manager/`, `load-forecast/`, `swiss-solar-forecast/`, `ocpp-server/` | Permanent/runtime | The four add-ons — code (`run.py`, `src/`, `tests/`) and each add-on's own `Documents/` FSD (the WHAT plane). |
| `Harness/` | Permanent | The HOW plane — the AI build contract (`AI-Workflow.md`), portable `standards/`, project-wide `project/` rules, and module-specific HOW in `project/modules/<addon>.md`. |
| `tools/` | Permanent | Tracked tooling — the doc-linter (`doc_lint.py`). |
| `scripts/` | Local-only (gitignored) | Standalone utilities (InfluxDB migration, Smart car status). Not tracked — never the source of truth. |
| `.claude/skills/` | Permanent | Agent skills, incl. the `documentation` skill. **Not documentation** (see below). |

`Handbook.md` (root) is the OPERATE plane.

## Not documentation

Referenced but never authoritative for current state: **agent skills** (`.claude/skills/`),
generated output, and build artifacts. `README.md` is the root landing page / suite overview;
every other doc is named for its function and indexed here. The AI-assistant config (`CLAUDE.md`)
points at this index — it is not a doc plane.

## Not tracked

Exists locally but gitignored — never the source of truth:

| Path | What |
|---|---|
| `CLAUDE.md` | AI-assistant config (references-only; points at this index, the Harness, and the FSDs) |
| `Documents/` working files | Migration scripts, dashboard backups, secrets (per `.gitignore`) |

---

The `documentation` skill (`.claude/skills/documentation/`) manages every change to these planes
and runs the doc-linter (`tools/doc_lint.py`) before commit; the Harness `AI-Workflow.md` is the
build contract for new functionality.
