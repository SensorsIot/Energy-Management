"""Tests for power reading sanity checks (FSD 1.9)."""

from __future__ import annotations

from energymanager.src.sanity import validate_power_readings


def test_normal_readings_no_warnings():
    """Typical midday values produce no warnings."""
    warnings = validate_power_readings(
        grid_w=-3000, pv_w=8000, load_w=2000, wallbox_w=3000, battery_w=-2000
    )
    assert warnings == []


def test_negative_pv_warns():
    """PV should never be negative."""
    warnings = validate_power_readings(pv_w=-500)
    assert len(warnings) == 1
    assert "pv_w=-500" in warnings[0]


def test_negative_load_warns():
    """Load should never be negative."""
    warnings = validate_power_readings(load_w=-100)
    assert len(warnings) == 1
    assert "load_w=-100" in warnings[0]


def test_grid_out_of_bounds_warns():
    """Grid power beyond physical limits triggers warning."""
    warnings = validate_power_readings(grid_w=20_000)
    assert len(warnings) == 1
    assert "grid_w=20000" in warnings[0]


def test_none_values_skipped():
    """None readings are silently skipped."""
    warnings = validate_power_readings(
        grid_w=None, pv_w=None, load_w=None, wallbox_w=None, battery_w=None
    )
    assert warnings == []


def test_all_within_bounds():
    """Edge-case boundary values pass without warnings."""
    warnings = validate_power_readings(
        grid_w=-15_000,
        pv_w=0,
        load_w=15_000,
        wallbox_w=12_000,
        battery_w=-6_000,
    )
    assert warnings == []
