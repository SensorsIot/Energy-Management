"""Regression tests for ICON radiation handling (FSD §4/§9).

ICON radiation fields arrive as running time-means since forecast start; the
parser must recover per-interval means (deaccumulate_avg), drop the
accumulation anchor, stamp values at interval midpoints
(radiation_to_midpoints), and compensate ASOB_S net→global via GROUND_ALBEDO.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.grib_parser import (
    GROUND_ALBEDO,
    deaccumulate_avg,
    radiation_to_midpoints,
)


def _running_avg(interval_means: np.ndarray, hours: np.ndarray) -> np.ndarray:
    """Build an ICON-style running average from known per-interval means."""
    spans = np.diff(np.concatenate([[0.0], hours]))
    cumulative = np.cumsum(interval_means * spans)
    return cumulative / hours


def test_deaccumulate_recovers_interval_means():
    hours = np.arange(1.0, 11.0)
    true_means = np.array([0, 50, 150, 300, 450, 550, 600, 550, 450, 300], dtype=float)
    running = _running_avg(true_means, hours)

    recovered = deaccumulate_avg(running, hours)

    np.testing.assert_allclose(recovered, true_means, atol=1e-9)


def test_deaccumulate_first_element_is_anchor_only():
    # Series starting mid-run (CH2 at h33): element 0 is a since-run-start
    # average, not an interval value — callers drop it.
    hours = np.array([33.0, 34.0, 35.0])
    running = np.array([400.0, 395.0, 390.0])

    recovered = deaccumulate_avg(running, hours)

    assert recovered[0] == 400.0  # anchor passthrough, meaningless as interval
    np.testing.assert_allclose(recovered[1], 395.0 * 34 - 400.0 * 33)
    np.testing.assert_allclose(recovered[2], 390.0 * 35 - 395.0 * 34)


def test_midpoints_drop_anchor_and_shift_half_interval():
    hours = np.arange(0.0, 4.0)  # h0 anchor + three hourly steps
    index = pd.date_range("2026-07-03 06:00", periods=4, freq="1h", tz="UTC")
    weather = pd.DataFrame({"ghi": [0.0, 100.0, 200.0, 300.0]}, index=index)

    shifted = radiation_to_midpoints(weather, hours)

    assert len(shifted) == 3  # anchor dropped
    expected = pd.date_range("2026-07-03 06:30", periods=3, freq="1h", tz="UTC")
    assert list(shifted.index) == list(expected)
    assert list(shifted["ghi"]) == [100.0, 200.0, 300.0]


def test_midpoints_no_morning_lag():
    # Regression for the morning under-forecast: interval means of a rising
    # ramp stamped at interval ENDS undershoot the true curve; at midpoints
    # they match it. True GHI here is linear in time, so the interval mean
    # equals the instantaneous value at the interval midpoint exactly.
    hours = np.arange(0.0, 6.0)
    def true_ghi_at(h):
        return 100.0 * h  # rising morning ramp
    interval_means = np.array(
        [0.0] + [(true_ghi_at(h - 1) + true_ghi_at(h)) / 2 for h in hours[1:]]
    )
    index = pd.date_range("2026-07-03 05:00", periods=6, freq="1h", tz="UTC")
    weather = pd.DataFrame({"ghi": interval_means}, index=index)

    shifted = radiation_to_midpoints(weather, hours)

    for ts, row in shifted.iterrows():
        h = (ts - index[0]).total_seconds() / 3600
        assert abs(row["ghi"] - true_ghi_at(h)) < 1e-9


def test_ground_albedo_compensation_factor():
    # ASOB_S is net shortwave = GHI * (1 - albedo); the parser divides by
    # (1 - GROUND_ALBEDO) to recover GHI. Guard the constant and direction.
    assert 0.0 < GROUND_ALBEDO < 0.5
    net = 400.0
    ghi = net / (1.0 - GROUND_ALBEDO)
    assert ghi > net  # compensation must raise, never lower


def test_midpoints_single_row_returns_empty():
    hours = np.array([0.0])
    index = pd.date_range("2026-07-03 06:00", periods=1, freq="1h", tz="UTC")
    weather = pd.DataFrame({"ghi": [0.0]}, index=index)

    assert len(radiation_to_midpoints(weather, hours)) == 0
