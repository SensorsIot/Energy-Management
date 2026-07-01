# Standard — Documentation governance (portable)

All project documentation is **present-state** (no history, no rationale narrative, no temporal
comparisons), lives in **one canonical home** (link, never restate), and is routed by the
**WHAT / HOW / operate** triage.

Within a single doc, placement follows a **main-body-vs-appendix** rule: numbered chapters carry the
contract a human reads to understand the system (behaviour, rules, interfaces, **test cases**);
appendices hold reference material an implementer looks up (bulk parameter tables, full config
listings, raw third-party payloads). The `documentation` skill states the test.

The project's `documentation` skill implements and enforces this standard:
- **Procedure A** — author/place new content in its one home.
- **Procedure B** — update after a change: the WHAT absorbs it (verify-don't-transcribe),
  operate-if-affected, the HOW stays put unless universally valid; then reconcile the doc against
  the implementation (compliant / deviation / missing) and present-state-scrub.

The concrete plane files are named in the project's doc index (`STRUCTURE.md`), never hardcoded
and never resolved from the AI-assistant config (`CLAUDE.md` is **not** project documentation).
