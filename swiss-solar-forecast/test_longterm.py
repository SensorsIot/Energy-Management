"""Tests for the daily long-term summary (FSD §8.1)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.longterm import compute_summary


def _series(values, start="2026-07-04 06:00"):
    idx = pd.date_range(start, periods=len(values), freq="15min", tz="UTC")
    return pd.Series(values, index=idx)


def test_production_and_specific_yield():
    f = compute_summary(
        production_kwh={"huawei": 44.5, "enphase": 11.1},
        power_15m=_series([5000.0, 8000.0, 6000.0]),
        enphase_15m=_series([500.0, 900.0, 700.0]),
        clearsky_total_kwh=65.0,
        sunny_ratios=[0.98, 1.0, 0.99, 1.01, 0.97],
        gains={"East": 0.997, "West": 0.997, "South": 1.014},
        forecast_p50_kwh=60.0,
        total_dc_wp=9520.0,
    )
    assert f["production_total_kwh"] == 55.6
    assert abs(f["specific_yield_kwh_kwp"] - 55.6 / 9.52) < 0.001
    assert f["peak_power_w"] == 8000.0
    assert f["performance_ratio"] == round(55.6 / 65.0, 3)
    assert f["pr_sunny"] == 0.99
    assert f["gain_east"] == 0.997
    assert f["forecast_bias"] == round(55.6 / 60.0, 3)


def test_clipping_hours():
    # 4 intervals at the Enphase cap (>= 96% of 1500 = 1440 W) -> 1.0 h
    enphase = _series([1450.0, 1500.0, 1490.0, 1445.0, 800.0])
    total = enphase + 5000.0  # Huawei part stays below its cap
    f = compute_summary(
        production_kwh={"huawei": 30.0, "enphase": 8.0},
        power_15m=total, enphase_15m=enphase,
        clearsky_total_kwh=50.0, sunny_ratios=[], gains={},
        forecast_p50_kwh=None, total_dc_wp=9520.0,
    )
    assert f["clipping_hours_south"] == 1.0
    assert f["clipping_hours_huawei"] == 0.0


def test_absent_optionals_stay_absent():
    f = compute_summary(
        production_kwh={"huawei": 10.0, "enphase": 2.0},
        power_15m=_series([1000.0]), enphase_15m=_series([100.0]),
        clearsky_total_kwh=0.0,   # no reference -> no PR
        sunny_ratios=[1.0, 1.0],  # below min obs -> no pr_sunny
        gains={}, forecast_p50_kwh=None, total_dc_wp=9520.0,
    )
    assert "performance_ratio" not in f
    assert "pr_sunny" not in f
    assert "forecast_bias" not in f
