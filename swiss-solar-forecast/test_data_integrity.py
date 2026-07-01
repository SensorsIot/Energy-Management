"""Unit tests for weather data-integrity validation (data_integrity.py)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, UTC
from pathlib import Path

from src.data_integrity import weather_run_complete

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _make_run(base: Path, model_dir: str, run_dt: datetime, files_failed: int) -> None:
    run = base / model_dir / run_dt.strftime("%Y%m%d%H%M")
    run.mkdir(parents=True)
    (run / "metadata.json").write_text(
        json.dumps({"files_downloaded": 100, "files_failed": files_failed})
    )


def test_complete_and_fresh(tmp_path: Path) -> None:
    _make_run(tmp_path, "icon-ch1", NOW - timedelta(hours=4), files_failed=0)
    ok, reason = weather_run_complete(tmp_path, "icon-ch1", 12, now=NOW)
    assert ok is True, reason


def test_incomplete_download_rejected(tmp_path: Path) -> None:
    _make_run(tmp_path, "icon-ch1", NOW - timedelta(hours=2), files_failed=7)
    ok, reason = weather_run_complete(tmp_path, "icon-ch1", 12, now=NOW)
    assert ok is False
    assert "incomplete" in reason


def test_stale_run_rejected(tmp_path: Path) -> None:
    _make_run(tmp_path, "icon-ch1", NOW - timedelta(hours=30), files_failed=0)
    ok, reason = weather_run_complete(tmp_path, "icon-ch1", 12, now=NOW)
    assert ok is False
    assert "stale" in reason


def test_picks_latest_run(tmp_path: Path) -> None:
    # An old complete run and a newer incomplete run → judge the newest.
    _make_run(tmp_path, "icon-ch1", NOW - timedelta(hours=8), files_failed=0)
    _make_run(tmp_path, "icon-ch1", NOW - timedelta(hours=2), files_failed=3)
    ok, reason = weather_run_complete(tmp_path, "icon-ch1", 12, now=NOW)
    assert ok is False
    assert "incomplete" in reason


def test_missing_metadata_rejected(tmp_path: Path) -> None:
    run = tmp_path / "icon-ch1" / (NOW - timedelta(hours=1)).strftime("%Y%m%d%H%M")
    run.mkdir(parents=True)
    ok, reason = weather_run_complete(tmp_path, "icon-ch1", 12, now=NOW)
    assert ok is False
    assert "metadata" in reason


def test_no_data_dir_rejected(tmp_path: Path) -> None:
    ok, reason = weather_run_complete(tmp_path, "icon-ch1", 12, now=NOW)
    assert ok is False
    assert "no data directory" in reason


def test_tolerates_failed_within_threshold(tmp_path: Path) -> None:
    _make_run(tmp_path, "icon-ch1", NOW - timedelta(hours=3), files_failed=2)
    ok, _ = weather_run_complete(tmp_path, "icon-ch1", 12, max_failed=2, now=NOW)
    assert ok is True
