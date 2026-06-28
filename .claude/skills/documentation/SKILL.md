---
name: documentation
description: Triage where a piece of documentation belongs (WHAT vs HOW vs operate) and write it to its one canonical home in the Energy-Management project — for BOTH authoring new docs AND updating existing ones after a change. Use whenever creating, updating, refreshing, cleaning up, scrubbing, or rewriting any project documentation — the FSDs, CLAUDE.md build rules, interface contracts, or skill SKILL.md files. Triggers on "write the docs", "update the docs", "document this change", "scrub history", "no-history pass", "current-state rewrite", "doc hygiene pass", "where does this belong", or when handed any .md to revise.
---

# documentation — place it, write it, keep it present-state (Energy-Management)

Every doc states **WHAT** the system is or **HOW** it is built / operated — in present tense, in
**one canonical home**, with **no content living in two places**. These rules hold identically
whether you are **authoring** a new doc or **updating** an existing one; the only difference is
the procedure (A vs B at the bottom).

## Project bindings (resolved)

| Role | This project | Notes |
|---|---|---|
| `[SPEC]` | the FSD that owns the subsystem — in-repo: `Documents/EnergymanagementV2_fsd.md` (battery/EV/appliance logic) and `ocpp-server/Documents/ocpp-server-fsd.md` (OCPP wallbox). Physical-install spec `Home-Installation-fsd.md` is maintained **outside the repo** (`/workspaces/HomeAssistant/Documents/`). | the **WHAT**; authoritative per CLAUDE.md → *Specifications* |
| `[INTERFACES]` | a **section of `[SPEC]`** — ocpp-server FSD §3.6 (External Interface) | route by section. No standalone Smart-car interface doc exists; don't invent one |
| `[HARNESS]` | `CLAUDE.md` | the **HOW** — only how to *work on* the repo; references the FSD, never restates behaviour |

Roles with no file here — `[HANDBOOK]`, `[GLOSSARY]`, `[DOC-LINTER]` — do not exist; never invent them.

## The rules (apply to authoring AND updating)

### Triage — where content belongs

Ask: *is this WHAT the system is, or HOW it is built / operated?*

| If the content is… | It is | Home |
|---|---|---|
| a current-state fact, rule, or contract about what the system **is / does** | **WHAT** | `[SPEC]` — the FSD that owns the subsystem |
| an external interface contract (e.g. OCPP) | **WHAT** | `[INTERFACES]` (ocpp-server FSD §3.6) |
| *why* a rule is what it is | — | **not recorded** — capture the live constraint as a present-tense rule; the running system is the decision |
| a prescriptive rule for **how** to build / change / operate the repo correctly | **HOW** | `[HARNESS]` (`CLAUDE.md`) |
| a step-by-step procedure for one operation (deploy, remote access, …) | **HOW (procedure)** | the relevant skill (e.g. `remote-connections`) |
| the definition of a domain term | **WHAT (vocabulary)** | `[SPEC]` |

Within the HOW: cross-cutting rules → `CLAUDE.md`; **scoped to one operation → that operation's
own skill**, never `CLAUDE.md`.

### One canonical home

A doc **links** to the canonical source, never restates it. `CLAUDE.md` and the auto-memory
reference the FSD; they do not copy behaviour text. Reference docs by FSD name + section
(e.g. "FSD §4.3.6", "ocpp-server FSD §3.6.2").

### Present-state only

A doc body describes the system **as it is now** — never how it got here, never why it was chosen:

- **No history** — no changelogs in prose, no "we used to / previously / formerly / as of
  <date> / migrated from / v1 vs v2 / legacy".
- **No rationale narrative** — no "we chose this because", "intentionally simpler than", "the
  trade-off is". A rule states what holds; it does not argue for itself.
- **No separate "why" doc** — a still-binding decision is a **present-tense rule** (e.g. "all
  times are UTC internally, converted to Europe/Zurich only for display"), never a record of what
  was rejected or when. `git log` holds the history.

**This project's sanctioned history home** is the `## Changelog` section at the bottom of each FSD
— one terse entry per add-on version bump (`vX.YY: … (1.8.x -> 1.8.y)`). History goes there or in
`git log`; the body stays present-state. The full pattern catalogue, the Changelog exception, and
worked before/after examples live in **`references/present-state-scrub.md`** — read it when
running Procedure B.

### Editorial

Present-state + positive + no fabrication: unbuilt behaviour goes under a `## Target` heading with
a `Check:` line naming a real command (or marked `(Target)`). Quotes stored straight; TODOs
deleted when done (not struck through); entities named by ID/version.

---

## Procedure A — authoring new or relocated content

1. **Triage** the content to its one home (table above).
2. **Write** it there in present tense; if it belongs elsewhere, move it and leave a reference,
   never a copy.
3. **Link** related docs by name + section.
4. **Run the doc gate** (see *Before you commit*) and repair findings.

## Procedure B — updating after a change

A doc update is driven by a **change in the system** (new or changed functionality). The trap is
that a change *is* history — and history must not reach the doc body.

First, scope and read: **confirm the target** if it's unclear (just this section, or every spec
chapter?), and **read each target end-to-end before editing** — note where the `## Changelog`
section sits so the scrub doesn't strip it. Then apply these, in order:

1. **WHAT absorbs the change.** New or changed functionality is described in `[SPEC]` (and the
   ocpp-server FSD §3.6 interface section if a contract moved) — stated as the current behaviour,
   as if it had always been so. This is the primary target of almost every update.
2. **Operate, if operators are affected.** If the change alters how an operator does something
   (deploy, remote access, …), update the relevant skill too. If it doesn't, leave them.
3. **HOW stays put.** An update **does not** touch `CLAUDE.md` — *unless* the change taught a
   **universally valid** rule (a durable convention for future work, not this one feature).
   Default: leave the harness unchanged.
4. **Re-triage what's already there.** While in the file, check the surrounding content still sits
   in the right plane and isn't duplicated elsewhere; move or delete drift you find.
5. **Present-state scrub.** Rewrite to current state — "the system does X", never "changed from Y
   to X", "previously", "as of <date>". Sweep with the catalogue in
   **`references/present-state-scrub.md`**; preserve only the `## Changelog` section (add one
   terse dated bullet / version entry there for the change).
6. **Show the diff**, flag any deletion > 5 lines, run the doc gate, repair, then commit.

The present-state rule is stated in step 5 specifically because an update is the only time it's
*tempting* to break it — authoring from scratch has no history to carry forward.

---

## Before you commit

This project has no doc-linter. The doc gate is the **version bump**: any add-on behaviour change
requires bumping the patch version in **both** `config.yaml` and `run.py` (`__version__`) for that
add-on, plus a `## Changelog` entry in the owning FSD (see `CLAUDE.md` → *Development Workflow*).
Run the add-on's tests before committing.

## Out of scope

- The behaviour catalogue itself → the FSD owns it; this skill only routes content.
- Operational procedures → the matching operational skill (`remote-connections`, etc.).
- Code comments → the `clean-comments` skill, not this one.
- Genuine historical records (FSD `## Changelog`, `git log`) → left intact; they are historical by
  design.
