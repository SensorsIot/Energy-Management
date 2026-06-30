---
name: documentation
description: Triage where a piece of documentation belongs (WHAT vs HOW vs operate) and write it to its one canonical home in the Energy-Management project — for BOTH authoring new docs AND updating existing ones, including reconciling a doc against the implementation (verifying the spec still matches the code). Use whenever creating, updating, refreshing, cleaning up, scrubbing, rewriting, or fact-checking any project documentation — the per-add-on FSDs, the Harness build rules, interface contracts, the Handbook, READMEs, or skill SKILL.md files. Triggers on "write the docs", "update the docs", "document this change", "scrub history", "no-history pass", "current-state rewrite", "doc hygiene pass", "does the spec still match the implementation", "the docs are out of sync with the code", "where does this belong", or when handed any .md to revise.
---

# documentation — place it, write it, keep it present-state

Every doc states **WHAT** the system is or **HOW** it is built / operated — in present tense, in
**one canonical home**, with **no content living in two places**. These rules hold identically
whether you are **authoring** a new doc or **updating** an existing one; the only difference is
the procedure (A vs B at the bottom).

The *roles* a project's docs play (spec, interfaces, harness, handbook) are universal; the
*concrete files* that play them are resolved below for this project. The AI-assistant config
(`CLAUDE.md`) is **not** project documentation — never treat it as a canonical source; it only
points at the index.

## Project bindings — resolved for Energy-Management

This is a **distributed** repo: each add-on owns its FSD beside its code. The doc index is
[`STRUCTURE.md`](../../../STRUCTURE.md) at the repo root; route every change through it.

| Role | Bound to | Notes |
|---|---|---|
| `[SPEC]` | the add-on's own FSD — `energymanager/Documents/energymanager-fsd.md`, `loadforecast/Documents/loadforecast-fsd.md`, `swisssolarforecast/Documents/swisssolarforecast-fsd.md`, `ocpp-server/Documents/ocpp-server-fsd.md` | **Distributed** — route a change to the FSD of the add-on it touches (see `STRUCTURE.md` *Components*). |
| `[INTERFACES]` | a section of the owning FSD; `energymanager/Documents/Hello Smart API.md` for the Smart-car API | Component-owned. No top-level `Interfaces.md` — don't create one. |
| `[HARNESS]` | `Harness/` — `00-Overview.md`, `AI-Workflow.md`, `standards/`, `project/` | Tracked, versioned. `CLAUDE.md` is **not** the harness. |
| `[HANDBOOK]` | `Handbook.md` (repo root) | OPERATE plane. |
| `[GLOSSARY]` | *none* | This project keeps no separate glossary — don't invent one; define terms in the owning FSD. |
| `[DOC-LINTER]` | `tools/doc_lint.py` | Run before every commit. |

A role with **no file** is skipped — don't invent one. When two roles collapse into one file (spec +
interfaces), bind both and route by *section*.

## The rules (apply to authoring AND updating)

### Triage — where content belongs

Ask: *is this WHAT the system is, or HOW it is built / operated?*

| If the content is… | It is | Home |
|---|---|---|
| a current-state fact, rule, or contract about what an add-on **is / does** | **WHAT** | `[SPEC]` (that add-on's FSD) |
| an external interface contract (an API, a third-party system) | **WHAT** | `[INTERFACES]` |
| *why* a rule is what it is | — | **not recorded** — capture the live constraint as a present-tense rule; the running system is the decision |
| a prescriptive rule for **how** to build / change correctly | **HOW** | `[HARNESS]` |
| a step-by-step procedure for one operation | **HOW (procedure)** | the relevant skill |
| an operator walkthrough | **HOW (operations)** | `[HANDBOOK]` |
| the definition of a domain term | **WHAT (vocabulary)** | the owning `[SPEC]` |

Within the HOW, route to the layer that owns the topic (`standards/` for portable rules,
`project/` for Energy-Management bindings). Cross-cutting → a layer doc; **scoped to one operation →
that operation's own skill**, never a shared layer doc.

### One canonical home

A doc **links** to the canonical source, never restates it. The HOW references the WHAT; it does
not copy the rule text. Reference docs by a stable, unambiguous name + section.

### Present-state only

A doc describes the system **as it is now** — never how it got here, never why it was chosen:

- **No history** — no changelogs in prose, no "we used to / previously / formerly / as of
  <date> / migrated from / v1 vs v2 / legacy".
- **No rationale narrative** — no "we chose this because", "intentionally simpler than", "the
  trade-off is". A rule states what holds; it does not argue for itself.
- **No separate "why" doc** — a still-binding decision is a **present-tense rule** (e.g. "all times
  are stored in UTC"), never a record of what was rejected or when. `git log` holds the history.
- **Coexisting options, not versions** — if two implementations run side by side, describe each as a
  current option, never as a temporal progression.

The full catalogue of patterns to strip, the Changelog exception, and worked before/after examples
live in **`references/present-state-scrub.md`** — read it when running Procedure B.

### Editorial

Present-state + positive + no fabrication: unbuilt tooling goes under a `## Target` heading with a
`Check:` line naming a real command (or marked `(Target)`). Quotes stored straight; TODOs deleted
when done (not struck through); entities linked by ID.

---

## Procedure A — authoring new or relocated content

1. **Triage** the content to its one home (table above).
2. **Write** it there in present tense; if it belongs elsewhere, move it and leave a reference,
   never a copy.
3. **Link** related docs by name + section.
4. **Run the doc-linter** (`tools/doc_lint.py`) and repair findings.

## Procedure B — updating after a change

A doc update is driven by a **change in the system**. The trap is that a change *is* history — and
history must not reach the doc.

First, scope and read: **confirm the target** if it's unclear, and **read each target end-to-end
before editing** — note where any Changelog / Cleanup-notes section sits so the scrub doesn't strip
it. Then apply these, in order:

1. **WHAT absorbs the change — verify, don't transcribe.** New/changed functionality is described in
   the owning add-on `[SPEC]` (and `[INTERFACES]` if a contract moved), stated as current behaviour.
   First confirm it is the *intended* behaviour: if the implementation **deviates** from the spec,
   flag the **code** — don't enshrine a defect by rewriting `[SPEC]` to match it. Cite evidence.
2. **Reconcile `[SPEC]` against the implementation** for the area you touched. Classify each
   affected requirement as **compliant / deviation / missing**, in *both* directions:
   over-implementation (in code, absent from spec → document it) and now-false spec statements
   elsewhere (→ fix). **Escalate, don't guess.**
3. **Operate, if operators are affected.** Update `[HANDBOOK]` / the relevant skill if the change
   alters how an operator does something. Otherwise leave them.
4. **HOW stays put.** An update **does not** touch `[HARNESS]` — *unless* the change taught a
   **universally valid** rule. Default: leave the harness unchanged.
5. **Re-triage what's already there.** Check the surrounding content still sits in the right plane
   and isn't duplicated elsewhere; move or delete drift you find.
6. **Present-state scrub.** Rewrite to current state. Sweep with the pattern catalogue in
   **`references/present-state-scrub.md`**; preserve only an explicit Changelog / Cleanup-notes
   section.
7. **Show the diff**, flag any deletion > 5 lines, run the doc-linter, repair, then commit.

---

## Before you commit

Run the doc-linter (`tools/doc_lint.py`) and repair findings first. A failing audit blocks the
commit — fix it, don't bypass it.

## Out of scope

- The rule catalog itself → the owning FSD owns it; this skill only routes content.
- Operational procedures → the matching operational skill.
- Code comments → a comment-cleanup skill, not this one.
- Genuine historical records (`swisssolarforecast/CHANGELOG.md`, dated audit data) → left intact;
  they are historical by design.
