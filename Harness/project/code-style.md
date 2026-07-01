# Project — Code style

Conventions observed across the add-on source. New code follows them.

- **`from __future__ import annotations`** at the top of modules.
- **Type hints on function signatures**, using `X | None` for optionals. Typing is gradual — Ruff
  does not enforce annotations (`ANN` is omitted from the rule set), so hints are a convention, not
  a gate.
- **Dataclasses for structured return values** (not dicts) — `@dataclass`.
- **`StrEnum` for HA-compatible state values** — e.g. `class EVState(StrEnum)` in
  `energy-manager/src/ev_state_machine.py` (Python 3.11 `enum.StrEnum`; the member value is the
  string the HA entity carries).
- **Logging via `logging.getLogger(__name__)`** — no `print` statements.
- **All times UTC internally**, converted to `Europe/Zurich` only for display/logs.

Identifier naming (code, entities, InfluxDB, MQTT, slugs, files) is a standard of its own — see
[`naming.md`](naming.md).

Formatting and linting run through Ruff (line length 100); see
[`build-and-release.md`](build-and-release.md) for the commands.
