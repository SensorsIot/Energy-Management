"""Tests for EVBatteryOptimizer — the 48-h min-SOC safety gate."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from src.ev_battery import EVBatteryOptimizer


def _make_optimizer(
    *,
    min_soc_in_window: float | None,
    capacity_wh: float = 20_000,
    min_soc_percent: float = 10.0,
    horizon: timedelta = timedelta(hours=48),
) -> EVBatteryOptimizer:
    """Build an EVBatteryOptimizer whose influx mock returns min_soc_in_window."""
    client = MagicMock()
    record = MagicMock()
    record.get_value.return_value = min_soc_in_window
    table = MagicMock()
    table.records = [record] if min_soc_in_window is not None else []
    client.query_api.return_value.query.return_value = (
        [table] if min_soc_in_window is not None else []
    )
    return EVBatteryOptimizer(
        influx_client=client,
        bucket="energy_manager",
        capacity_wh=capacity_wh,
        min_soc_percent=min_soc_percent,
        horizon=horizon,
    )


class TestSafetyGate:
    def test_safe_when_min_well_above_floor(self) -> None:
        opt = _make_optimizer(min_soc_in_window=50.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=0.0)
        assert safe is True
        assert min_soc == 50.0

    def test_unsafe_when_min_below_floor(self) -> None:
        opt = _make_optimizer(min_soc_in_window=8.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=0.0)
        assert safe is False
        assert min_soc == 8.0

    def test_ev_load_is_ignored_gate_is_power_independent(self) -> None:
        """ev_load_wh is ignored — Rule 4 is yes/no on the home SOC forecast."""
        opt = _make_optimizer(min_soc_in_window=14.0, min_soc_percent=10.0)
        # Same min returned regardless of the (ignored) ev_load_wh.
        safe0, m0 = opt.check_ev_safe(ev_load_wh=0.0)
        safe1, m1 = opt.check_ev_safe(ev_load_wh=2000.0)
        assert (safe0, m0) == (True, 14.0)
        assert (safe1, m1) == (True, 14.0)

    def test_unsafe_uses_raw_forecast_min(self) -> None:
        """Below-floor forecast min → unsafe, reported as-is (no subtraction)."""
        opt = _make_optimizer(min_soc_in_window=8.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=2000.0)
        assert safe is False
        assert min_soc == 8.0

    def test_blocks_when_no_forecast_data(self) -> None:
        """Missing forecast → block EV as precaution."""
        opt = _make_optimizer(min_soc_in_window=None)
        safe, min_soc = opt.check_ev_safe()
        assert safe is False
        assert min_soc == 0.0

    def test_blocks_on_query_error(self) -> None:
        client = MagicMock()
        client.query_api.return_value.query.side_effect = RuntimeError("boom")
        opt = EVBatteryOptimizer(
            influx_client=client,
            bucket="energy_manager",
            capacity_wh=20_000,
            min_soc_percent=10.0,
        )
        safe, min_soc = opt.check_ev_safe()
        assert safe is False
        assert min_soc == 0.0

    def test_exactly_at_floor_is_safe(self) -> None:
        """`>=` comparison — equal to floor is allowed."""
        opt = _make_optimizer(min_soc_in_window=10.0, min_soc_percent=10.0)
        safe, _ = opt.check_ev_safe()
        assert safe is True


class TestWillBatteryHitFull:
    """Dashboard diagnostic — not a gate."""

    def test_below_threshold(self) -> None:
        """Peak SOC 85% → not full."""
        client = MagicMock()
        record = MagicMock()
        record.get_value.return_value = 85.0
        table = MagicMock()
        table.records = [record]
        client.query_api.return_value.query.return_value = [table]
        opt = EVBatteryOptimizer(
            influx_client=client,
            bucket="energy_manager",
            capacity_wh=20_000,
            min_soc_percent=10.0,
        )
        hits_full, peak, full_time, _ = opt.will_battery_hit_full()
        assert hits_full is False
        assert peak == 85.0
        assert full_time is None

    def test_no_records(self) -> None:
        client = MagicMock()
        client.query_api.return_value.query.return_value = []
        opt = EVBatteryOptimizer(
            influx_client=client,
            bucket="energy_manager",
            capacity_wh=20_000,
            min_soc_percent=10.0,
        )
        hits_full, peak, full_time, _ = opt.will_battery_hit_full()
        assert hits_full is False
        assert peak is None
        assert full_time is None
