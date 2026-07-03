"""Tests for the calibration model (FSD §10): shade map, eff curve, gain."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.calibration import (
    EFF_NUM_BINS,
    CalibrationTracker,
    eff_bin_index,
    shade_bin_key,
)
from src.pv_model import apply_calibration


def _obs(string, ratio, az, el, pf, day="2026-07-04", clipping=False, n=1):
    return [
        {
            "string": string,
            "snapshot_id": day,
            "ratio": ratio,
            "sun_azimuth": az,
            "sun_elevation": el,
            "power_fraction": pf,
            "is_clipping": clipping,
        }
        for _ in range(n)
    ]


def test_bin_keys():
    assert shade_bin_key(123.4, 37.2) == "120,35"
    assert shade_bin_key(89.9, 4.9) == "80,0"
    assert eff_bin_index(0.0) == 0
    assert eff_bin_index(0.55) == 5
    assert eff_bin_index(1.0) == EFF_NUM_BINS - 1


def test_shade_map_normalized_to_unshaded_level():
    rows = []
    # unshaded bins observe the plain model deviation (0.95)
    for az in (150, 160, 170, 180, 190):
        rows += _obs("East", 0.95, az, 40, 0.5, n=6)
    # one bin blocked by infrastructure: half power
    rows += _obs("East", 0.475, 100, 20, 0.3, n=6)
    obs = pd.DataFrame(rows)

    cal = CalibrationTracker.build_maps(obs)

    shade = cal["East"]["shade"]
    # unshaded bins normalize to 1.0 (deviation goes to eff/gain, not shade)
    assert shade[shade_bin_key(150, 40)] == 1.0
    # the blocked bin carries pure geometry: 0.475/0.95 = 0.5
    assert abs(shade[shade_bin_key(100, 20)] - 0.5) < 0.01


def test_shade_map_requires_min_observations():
    obs = pd.DataFrame(_obs("East", 0.5, 100, 20, 0.3, n=2))  # below SHADE_MIN_OBS
    cal = CalibrationTracker.build_maps(obs)
    assert cal["East"]["shade"] == {}


def test_gain_tracks_daily_ratio():
    rows = []
    for day in range(1, 21):
        rows += _obs("West", 0.90, 200, 40, 0.5, day=f"2026-07-{day:02d}", n=25)
    obs = pd.DataFrame(rows)

    cal = CalibrationTracker.build_maps(obs)

    # EWMA over 20 identical days converges toward the daily level
    assert 0.88 <= cal["West"]["gain"] <= 0.92


def test_clipping_excluded_from_eff_and_gain():
    rows = []
    for day in range(1, 21):
        rows += _obs("South", 0.95, 180, 45, 0.5, day=f"2026-07-{day:02d}", n=25)
    # clipped observations with absurd ratio must not disturb gain/eff
    rows += _obs("South", 0.3, 180, 50, 0.99, clipping=True, n=50)
    obs = pd.DataFrame(rows)

    cal = CalibrationTracker.build_maps(obs)

    assert cal["South"]["gain"] > 0.9


def test_apply_calibration_shade_and_gain():
    dc = np.array([1000.0, 1000.0])
    az = np.array([105.0, 185.0])   # first point in shaded bin, second not
    el = np.array([22.0, 45.0])
    cal = {
        "shade": {shade_bin_key(105, 22): 0.5},
        "eff": [],
        "gain": 0.9,
    }

    out = apply_calibration(dc, az, el, 3640.0, cal)

    assert abs(out[0] - 1000 * 0.5 * 0.9) < 1e-6
    assert abs(out[1] - 1000 * 0.9) < 1e-6


def test_apply_calibration_eff_by_power_fraction():
    dc = np.array([364.0, 1820.0])  # 10% and 50% of rated 3640
    az = np.array([180.0, 180.0])
    el = np.array([30.0, 30.0])
    eff = [1.0] * EFF_NUM_BINS
    eff[1] = 0.8   # 10-20% bin... 364/3640 = 0.1 -> bin 1
    eff[5] = 1.0
    cal = {"shade": {}, "eff": eff, "gain": 1.0}

    out = apply_calibration(dc, az, el, 3640.0, cal)

    assert abs(out[0] - 364.0 * 0.8) < 1e-6
    assert abs(out[1] - 1820.0) < 1e-6


def test_neutral_calibration_is_identity():
    dc = np.array([500.0, 2500.0])
    out = apply_calibration(
        dc, np.array([120.0, 240.0]), np.array([15.0, 40.0]), 3640.0,
        {"shade": {}, "eff": [], "gain": 1.0},
    )
    np.testing.assert_allclose(out, dc)
