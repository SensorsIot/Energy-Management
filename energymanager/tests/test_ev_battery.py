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
    def test_safe_when_min_well_above_floor(self):
        opt = _make_optimizer(min_soc_in_window=50.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=0.0)
        assert safe is True
        assert min_soc == 50.0

    def test_unsafe_when_min_below_floor(self):
        opt = _make_optimizer(min_soc_in_window=8.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=0.0)
        assert safe is False
        assert min_soc == 8.0

    def test_ev_load_subtracts_from_min(self):
        """EV load is subtracted as worst case (one 15-min slot)."""
        # 1000 Wh on a 20 kWh battery = 5%. Min 14% - 5% = 9% < 10% floor.
        opt = _make_optimizer(min_soc_in_window=14.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=1000.0)
        assert safe is False
        assert min_soc == 9.0

    def test_ev_load_keeps_it_safe_if_margin_is_enough(self):
        # 1000 Wh on 20 kWh = 5%. Min 20% - 5% = 15% ≥ 10% floor.
        opt = _make_optimizer(min_soc_in_window=20.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=1000.0)
        assert safe is True
        assert min_soc == 15.0

    def test_min_clamped_at_zero(self):
        """Negative projected min after subtraction is clamped to 0."""
        opt = _make_optimizer(min_soc_in_window=3.0, min_soc_percent=10.0)
        safe, min_soc = opt.check_ev_safe(ev_load_wh=2000.0)  # subtracts 10%
        assert safe is False
        assert min_soc == 0.0

    def test_blocks_when_no_forecast_data(self):
        """Missing forecast → block EV as precaution."""
        opt = _make_optimizer(min_soc_in_window=None)
        safe, min_soc = opt.check_ev_safe()
        assert safe is False
        assert min_soc == 0.0

    def test_blocks_on_query_error(self):
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

    def test_exactly_at_floor_is_safe(self):
        """`>=` comparison — equal to floor is allowed."""
        opt = _make_optimizer(min_soc_in_window=10.0, min_soc_percent=10.0)
        safe, _ = opt.check_ev_safe()
        assert safe is True
