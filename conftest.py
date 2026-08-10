"""Repo-root pytest guard: one add-on per pytest session.

Every add-on ships its own top-level `run.py` and `src/` package, and its tests
import them by those bare names. Collecting more than one add-on in a single
pytest session therefore resolves `src` to whichever add-on landed in
`sys.modules` first, and the rest fail with a confusing pile of
ModuleNotFoundError collection errors that says nothing about the real cause.

Fail immediately with an explanation instead. Add-ons are separate containers at
runtime; they are tested the same way. `tools/run_tests.sh` runs every suite,
each in its own process.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.resolve()


def _is_addon(name: str) -> bool:
    """Report whether a top-level dir is an add-on (has both `run.py` and `src/`)."""
    return (_ROOT / name / "run.py").is_file() and (_ROOT / name / "src").is_dir()


def _addon_of(arg: str) -> str | None:
    """Return the add-on a pytest path argument belongs to, if any."""
    # Strip the `::test_name` part of a node id before treating it as a path.
    path = Path(arg.split("::", 1)[0])
    try:
        rel = (path if path.is_absolute() else _ROOT / path).resolve().relative_to(_ROOT)
    except ValueError:
        return None
    if not rel.parts or rel.parts[0] == ".":
        return None
    return rel.parts[0] if _is_addon(rel.parts[0]) else None


def pytest_configure(config: pytest.Config) -> None:
    """Reject a session that would span more than one add-on."""
    args = [a for a in config.args if not a.startswith("-")]

    # A bare run at the repo root would collect every add-on at once.
    resolved = {Path(a.split("::", 1)[0]).resolve() for a in args} if args else {_ROOT}
    if _ROOT in resolved:
        raise pytest.UsageError(
            "Running pytest across the whole repository collects every add-on at "
            "once, and they cannot share one session (each has its own top-level "
            "`run.py` and `src/`). Run one add-on's suite, or use "
            "`tools/run_tests.sh` to run them all."
        )

    addons = {a for a in (_addon_of(arg) for arg in args) if a}
    if len(addons) > 1:
        raise pytest.UsageError(
            "Cannot test multiple add-ons in one pytest session: "
            + ", ".join(sorted(addons))
            + ". Each ships its own top-level `run.py` and `src/`, so the first "
            "`src` imported wins and the others fail to collect. Run them "
            "separately, or use `tools/run_tests.sh`."
        )
