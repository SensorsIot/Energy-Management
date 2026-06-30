# Standard — Engineering conventions (portable)

Project-agnostic build rules; each is a single present-state rule. Project-specific bindings live in
[`../project/`](../project/).

- **Reuse before adding** — search for an existing helper/skill/module before writing new code.
- **Smallest change that satisfies the FSD rule** — no speculative scope, no drive-by refactors.
- **Verification** — every change is verified before commit. The test/lint commands and the
  required version bump are in [`../project/build-and-release.md`](../project/build-and-release.md).
- **Security** — secrets never in code or docs (only their location is documented); least
  privilege; validate external input.
- **Errors** — fail loudly with actionable messages; no silent catches.
- **Commits** — never commit generated output, build artifacts, or secrets.
