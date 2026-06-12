"""Validate that downloaded weather data is complete and fresh.

Guards the forecast against partial/stale inputs (e.g. during a WAN outage):
instead of judging the forecast *output* curve (which can vary legitimately on
unusual weather days), we confirm the *input* — the fetcher's per-run
``metadata.json`` — downloaded fully and recently. This cannot false-reject a
genuinely odd-but-correct forecast.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path


def weather_run_complete(
    data_dir: Path,
    model_dir: str,
    max_run_age_hours: float,
    max_failed: int = 0,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Confirm the latest downloaded weather run is complete and fresh.

    Reads the fetcher's per-run ``metadata.json`` (written by IconFetcher):

    - ``files_failed`` must be ≤ max_failed — a partial download yields gaps
      that corrupt de-accumulated radiation.
    - the run's model reference time (the run directory name, YYYYMMDDHHMM)
      must be within max_run_age_hours — a stale run means we never got fresh
      weather.

    Args:
        data_dir: Base storage directory (contains per-model subdirs).
        model_dir: Model subdirectory name, e.g. "icon-ch1".
        max_run_age_hours: Reject runs whose reference time is older than this.
        max_failed: Tolerated number of failed file downloads (default 0).
        now: Reference time (defaults to current UTC; injectable for tests).

    Returns:
        (ok, reason). ok=True when the run is complete and fresh.

    """
    if now is None:
        now = datetime.now(UTC)

    base = Path(data_dir) / model_dir
    if not base.is_dir():
        return False, f"{model_dir}: no data directory"
    runs = sorted(d for d in base.iterdir() if d.is_dir() and d.name.isdigit())
    if not runs:
        return False, f"{model_dir}: no downloaded runs"
    latest = runs[-1]

    meta_file = latest / "metadata.json"
    if not meta_file.exists():
        return False, f"{model_dir} run {latest.name}: no metadata.json"
    try:
        with open(meta_file) as f:
            meta = json.load(f)
    except (OSError, ValueError) as e:
        return False, f"{model_dir} run {latest.name}: unreadable metadata ({e})"

    failed = int(meta.get("files_failed", 0))
    if failed > max_failed:
        return False, (
            f"{model_dir} run {latest.name}: incomplete download "
            f"({failed} files failed)"
        )

    try:
        run_dt = datetime.strptime(latest.name, "%Y%m%d%H%M").replace(tzinfo=UTC)
    except ValueError:
        return False, f"{model_dir}: unparseable run name {latest.name}"
    age_h = (now - run_dt).total_seconds() / 3600.0
    if age_h > max_run_age_hours:
        return False, (
            f"{model_dir} run {latest.name}: stale weather "
            f"({age_h:.1f} h old > {max_run_age_hours} h)"
        )

    return True, f"{model_dir} run {latest.name}: complete ({age_h:.1f} h old)"
