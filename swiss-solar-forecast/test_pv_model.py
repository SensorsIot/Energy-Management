"""Tests for pv_model AC-clip topology (FSD §6.3): per-panel vs aggregate."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.pv_model import inverter_ac_power


def _series(v):
    return pd.Series([float(v)], index=pd.DatetimeIndex(["2026-07-04T11:00:00Z"]))


def test_microinverter_clips_each_panel_independently():
    # One micro per panel, 300 W cap. String A is well-lit (400 W/panel -> clips
    # to 300), string B is poorly lit (100 W/panel -> no clip). The aggregate
    # cap would let B's headroom absorb A's excess; per-panel clipping must not.
    inverter = {
        "efficiency": 1.0,
        "max_power": 1500,
        "micro_ac_cap": 300,
        "strings": [
            {"name": "A", "count": 3},   # 3 * 400 = 1200 DC
            {"name": "B", "count": 2},   # 2 * 100 = 200 DC
        ],
    }
    string_dc = {"A": _series(1200), "B": _series(200)}

    ac = inverter_ac_power(string_dc, inverter)

    # A: 3 * min(400, 300) = 900 ; B: 2 * min(100, 300) = 200
    assert abs(float(ac.iloc[0]) - 1100.0) < 1e-6
    # An aggregate cap would have given clip(1400, 0, 1500) = 1400 — strictly more
    assert float(ac.iloc[0]) < 1400.0


def test_microinverter_no_clip_when_all_panels_below_cap():
    inverter = {
        "efficiency": 1.0, "max_power": 1500, "micro_ac_cap": 300,
        "strings": [{"name": "A", "count": 3}, {"name": "B", "count": 2}],
    }
    string_dc = {"A": _series(600), "B": _series(400)}  # 200 W/panel both
    ac = inverter_ac_power(string_dc, inverter)
    assert abs(float(ac.iloc[0]) - 1000.0) < 1e-6


def test_string_inverter_uses_aggregate_cap():
    # No micro_ac_cap -> strings share one DC bus, clip once on summed AC.
    inverter = {
        "efficiency": 1.0, "max_power": 1000,
        "strings": [{"name": "E", "count": 8}, {"name": "W", "count": 9}],
    }
    string_dc = {"E": _series(800), "W": _series(600)}  # 1400 total
    ac = inverter_ac_power(string_dc, inverter)
    assert abs(float(ac.iloc[0]) - 1000.0) < 1e-6  # aggregate clip at max_power


def test_microinverter_applies_efficiency_before_cap():
    inverter = {
        "efficiency": 0.9, "max_power": 1500, "micro_ac_cap": 300,
        "strings": [{"name": "A", "count": 1}],
    }
    # 350 DC * 0.9 = 315 -> clips to 300
    assert abs(float(inverter_ac_power({"A": _series(350)}, inverter).iloc[0]) - 300.0) < 1e-6
    # 320 DC * 0.9 = 288 -> no clip
    assert abs(float(inverter_ac_power({"A": _series(320)}, inverter).iloc[0]) - 288.0) < 1e-6
