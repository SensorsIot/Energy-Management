"""
Forecast-based EV charging strategy (FSD 4.5.6).

Runs every 15 min in run_optimization(). Snaps the current surplus power
to the next wallbox amp step, then verifies via SOC simulation that the
battery stays above protection target at 21:00.  Steps down if not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from src.battery_optimizer import BatteryOptimizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EVStrategyResult:
    """Result of the EV charging strategy calculation."""

    power_w: float  # 0 or amps × 230 × phases
    amps: int  # 0 or min_amps..max_amps
    protection_target: float  # dynamic SOC target at 21:00
    baseline_soc_2100: float  # SOC at 21:00 without EV
    reason: str


class EVChargingStrategy:
    """Forecast-based EV charging strategy.

    Determines the optimal wallbox amp level by simulating the battery SOC
    trajectory with various EV load levels subtracted from the forecast.
    """

    def __init__(self, optimizer: BatteryOptimizer) -> None:
        self._optimizer = optimizer

    def _soc_at_target(self, sim: pd.DataFrame, target: datetime) -> float:
        """Extract SOC at the target time from a simulation DataFrame."""
        if sim.empty:
            return 0.0
        valid = sim.index[sim.index <= target]
        if len(valid) == 0:
            return float(sim["soc_percent"].iloc[0])
        return float(sim.loc[valid[-1], "soc_percent"])

    def _snap_to_amp_step(
        self,
        surplus_w: float,
        min_amps: int,
        max_amps: int,
        phases: int,
    ) -> int:
        """Snap surplus power up to the next wallbox amp level.

        Returns amps clamped to [min_amps, max_amps].
        """
        step_w = 230 * phases
        # Ceiling division: next amp level at or above surplus
        raw_amps = int(-(-surplus_w // step_w))  # math.ceil without import
        return max(min_amps, min(raw_amps, max_amps))

    def calculate(
        self,
        current_soc: float,
        forecast: pd.DataFrame,
        now: datetime,
        surplus_power_w: float = 0.0,
        min_amps: int = 6,
        max_amps: int = 16,
        phases: int = 3,
        protection_soc_percent: float = 80,
    ) -> EVStrategyResult:
        """Calculate optimal EV charging power level.

        Uses the current surplus to pick a wallbox amp level, then verifies
        via SOC simulation that the battery stays above protection target.

        Args:
            current_soc: Current battery SOC (0-100).
            forecast: DataFrame with net_energy_wh column (15-min slots).
            now: Current UTC time.
            surplus_power_w: Current surplus power (solar - house_load).
            min_amps: Wallbox minimum amps.
            max_amps: Wallbox maximum amps.
            phases: Number of charging phases.
            protection_soc_percent: Static SOC protection ceiling.

        Returns:
            EVStrategyResult with chosen power, amps, and reasoning.
        """
        if forecast.empty:
            return EVStrategyResult(0, 0, protection_soc_percent, 0.0, "no forecast")

        tariff = self._optimizer.get_tariff_periods(now)
        target_time = tariff.cheap_start  # 21:00 boundary

        # Step 1: baseline SOC at 21:00 without EV → dynamic protection target
        sim_no_ev = self._optimizer.simulate_soc(current_soc, forecast)
        baseline_soc = self._soc_at_target(sim_no_ev, target_time)
        protection_target = min(protection_soc_percent, baseline_soc)

        # Step 2: snap surplus to next wallbox amp step
        candidate_amps = self._snap_to_amp_step(
            surplus_power_w, min_amps, max_amps, phases
        )

        # Step 3: simulate with candidate, step down until SOC target met
        for amps in range(candidate_amps, min_amps - 1, -1):
            wallbox_w = amps * 230 * phases
            fc_test = forecast.copy()
            fc_test["net_energy_wh"] = fc_test["net_energy_wh"].astype(float)
            fc_test.iloc[0, fc_test.columns.get_loc("net_energy_wh")] -= wallbox_w * 0.25
            sim_test = self._optimizer.simulate_soc(current_soc, fc_test)
            soc_at_target = self._soc_at_target(sim_test, target_time)

            if soc_at_target >= protection_target:
                power_w = wallbox_w
                return EVStrategyResult(
                    power_w,
                    amps,
                    protection_target,
                    baseline_soc,
                    f"{amps}A × {phases}ph = {power_w}W",
                )

        # Even min_amps fails protection → return 0
        return EVStrategyResult(
            0,
            0,
            protection_target,
            baseline_soc,
            f"protection fail: all levels drop below {protection_target:.0f}%",
        )
