# Energy Management System — Harness (HOW) — Overview

The Harness is the **AI build contract**: the rules for how this project is built and changed.
Entry point for any task: **[`AI-Workflow.md`](AI-Workflow.md)**.

## Layout
- **`standards/`** — *portable* rules, reusable on any project ([documentation governance](standards/documentation.md),
  [engineering conventions](standards/engineering.md), [testing](standards/testing.md)).
- **`project/`** — *Energy-Management-specific* bindings:
  - [`stack.md`](project/stack.md) — languages, frameworks, runtime architecture.
  - [`build-and-release.md`](project/build-and-release.md) — version-bump, test, secrets, command reference.
  - [`code-style.md`](project/code-style.md) — coding conventions for new code.
  - [`naming.md`](project/naming.md) — naming conventions (code, entities, InfluxDB, MQTT, slugs, files), anchored to ISO/IEC 11179, ISO 80000, PEP 8.
  - [`design-principles.md`](project/design-principles.md) — project-wide design principles.
  - [`testing.md`](project/testing.md) — testing structure + the index of where each add-on's test cases are defined.
  - [`addon-architecture.md`](project/addon-architecture.md) — HA add-on config architecture.
  - [`modules/`](project/modules/) — per-add-on build/architecture HOW.

## Authority order
1. **Harness (HOW)** — how to build (this directory).
2. **FSD (WHAT)** — what must be true (the add-on FSDs indexed in [`../STRUCTURE.md`](../STRUCTURE.md)).
3. **Handbook (OPERATE)** — how to run ([`../Handbook.md`](../Handbook.md)).

On conflict, the **FSD defines the target**; the **Harness defines the method**. Neither carries
history or rationale narrative — those live in `git log`.

## Documentation standard
All docs are present-state and one-canonical-home ([`standards/documentation.md`](standards/documentation.md)).
The `documentation` skill enforces this and manages every change to the three planes. `CLAUDE.md`
is the AI-assistant config — it references this Harness and the FSDs; it is never the canonical
home for a project rule.
