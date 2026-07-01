# Energy Management System — AI Workflow (the build contract)

Read this before any change. It is how new functionality is built and how the docs stay in sync.

## The loop
1. **Locate the contract.** Find the relevant FSD rule(s) in the FSD of the add-on you are touching
   (the *Components* table in [`../STRUCTURE.md`](../STRUCTURE.md) routes you to it). If no rule
   exists, the work starts by defining the **WHAT** (a new FSD rule), not by writing code.
2. **Build per the Harness.** Follow [`standards/`](standards/) + [`project/`](project/) rules —
   conventions, constraints, prohibitions. Reuse an existing helper/skill before adding new code;
   make the smallest change that satisfies the rule. Apply the conventions in
   [`project/code-style.md`](project/code-style.md).
3. **Test — the gate (not optional).** A change is **not done** until its test case exists and its
   test passes. Per [`standards/testing.md`](standards/testing.md) and the index in
   [`project/testing.md`](project/testing.md): define/update the **test case** in the owning FSD
   chapter (energymanager Chapter 6, ocpp-server §8, etc.), add/update the **test** beside the code
   in `tests/`, and run the suite green (commands in
   [`project/build-and-release.md`](project/build-and-release.md), which also carries the version
   bump). A bug fix adds its **regression test first**.
4. **Reconcile the docs** (the `documentation` skill, Procedure B):
   - the **FSD** absorbs new/changed behaviour — *verify, don't transcribe* (if the code deviates
     from the intended spec, fix the code, don't enshrine the defect);
   - the **Handbook** absorbs operator-facing changes;
   - the **Harness stays put** unless the change taught a *universally valid* rule.
   All present-state, no history.
5. **Verify.** Confirm the implementation matches the FSD (compliant / deviation / missing, both
   directions). Deviations fix the code; gaps get documented; contradictions get escalated. Run the
   doc-linter (`tools/doc_lint.py`) before commit.

## Roles
- The **add-on FSD** is authoritative for that add-on's behaviour; behaviour is never described in
  the Harness, the Handbook, or `CLAUDE.md`.
- The **Harness** owns build method and conventions; a new rule lands here only if it is durable
  across future work, not specific to one feature.
- The **`documentation` skill** owns every doc edit and runs the doc-linter as the commit gate.
