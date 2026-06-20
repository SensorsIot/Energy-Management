"""Tests for the dynamic home-battery charge target (FSD 4.2.4).

`BatteryOptimizer.compute_charge_target` returns the lowest SOC ceiling that
keeps the battery above `reserve` over a worst-case survival simulation, plus a
margin — charging below 100% on most days while never risking grid import.
Efficiencies are set to 1.0 so the expected SOC arithmetic is exact.
"""

from __future__ import annotations

from datetime import datetime, UTC

import pandas as pd

from src.battery_optimizer import BatteryOptimizer

CAP = 10000
NOW = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
COMMON = dict(
    reserve=10.0,
    margin_pct=10.0,
    min_target=20.0,
    horizon_h=48,
    calibration_due=False,
    forecast_fresh=True,
)


def _opt() -> BatteryOptimizer:
    return BatteryOptimizer(
        capacity_wh=CAP,
        min_soc_percent=0,
        charge_efficiency=1.0,
        discharge_efficiency=1.0,
    )


def _fc(periods: int, pv_w: list[float], load_w: list[float]) -> pd.DataFrame:
    """Forecast DataFrame: per-15-min PV/load patterns (W), repeated to length."""
    times = pd.date_range(start=NOW, periods=periods, freq="15min", tz=UTC)
    pv = (pv_w * (periods // len(pv_w) + 1))[:periods]
    ld = (load_w * (periods // len(load_w) + 1))[:periods]
    net = [(p - val) * 0.25 for p, val in zip(pv, ld, strict=False)]
    return pd.DataFrame(
        {
            "pv_energy_wh": [p * 0.25 for p in pv],
            "load_energy_wh": [val * 0.25 for val in ld],
            "net_energy_wh": net,
        },
        index=times,
    )


class TestComputeChargeTarget:
    def test_deficit_day_returns_full(self) -> None:
        """No PV, steady load → battery can't survive → charge to 100%."""
        f = _fc(192, [0], [2000])
        target, reason = _opt().compute_charge_target(50, f, NOW, **COMMON)
        assert target == 100.0
        assert "full battery" in reason

    def test_abundant_constant_surplus_caps_low(self) -> None:
        """PV always > load → battery never discharges → ceiling drops to min."""
        f = _fc(192, [4000], [300])
        target, _ = _opt().compute_charge_target(90, f, NOW, **COMMON)
        # reserve(10) + margin(10) = 20, which equals min_target.
        assert target == 20.0

    def test_min_target_floor_binds(self) -> None:
        """When the need is tiny, the sanity floor min_target wins."""
        f = _fc(192, [4000], [300])
        common = {**COMMON, "min_target": 40.0}
        target, _ = _opt().compute_charge_target(90, f, NOW, **common)
        assert target == 40.0

    def test_floor_80_keeps_headroom(self) -> None:
        """Production floor (80%): an abundant day whose survival need is low
        still charges to 80% — keeps shaving headroom, LFP-safe."""
        f = _fc(192, [4000], [300])  # survival need ~reserve
        common = {**COMMON, "min_target": 80.0}
        target, reason = _opt().compute_charge_target(90, f, NOW, **common)
        assert target == 80.0
        assert "floored to 80%" in reason

    def test_intermediate_night_drain(self) -> None:
        """12h day + 12h night (60% drain) → ceiling lands ~80% (need 70 + margin 10)."""
        pv = [3000] * 48 + [0] * 48  # one day: 12h sun, 12h dark
        load = [500] * 96
        f = _fc(192, pv, load)
        target, _ = _opt().compute_charge_target(50, f, NOW, **COMMON)
        # Need ceiling ≥ ~70% so the post-night trough stays ≥ reserve; +10 margin.
        assert 75.0 <= target <= 85.0

    def test_never_below_reserve(self) -> None:
        """Even in the most abundant case the target stays ≥ reserve."""
        f = _fc(192, [4000], [300])
        target, _ = _opt().compute_charge_target(90, f, NOW, **COMMON)
        assert target >= COMMON["reserve"]

    def test_calibration_due_forces_full(self) -> None:
        """Weekly LFP calibration overrides the worst-case computation."""
        f = _fc(192, [4000], [300])  # would otherwise cap low
        common = {**COMMON, "calibration_due": True}
        target, reason = _opt().compute_charge_target(90, f, NOW, **common)
        assert target == 100.0
        assert "calibration" in reason

    def test_stale_forecast_fails_up(self) -> None:
        """A stale forecast fails UP to 100%, never to a low cap."""
        f = _fc(192, [4000], [300])
        common = {**COMMON, "forecast_fresh": False}
        target, reason = _opt().compute_charge_target(90, f, NOW, **common)
        assert target == 100.0
        assert "stale" in reason or "missing" in reason

    def test_empty_forecast_returns_full(self) -> None:
        """Missing forecast data → 100% fail-safe."""
        empty = pd.DataFrame({"net_energy_wh": []})
        target, _ = _opt().compute_charge_target(50, empty, NOW, **COMMON)
        assert target == 100.0
