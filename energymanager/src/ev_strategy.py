"""
Forecast-based EV charging strategy (FSD 4.5.7).

Runs every 15 min in run_optimization(). Uses SOC simulation to find the
optimal wallbox amp level so that the battery acts as buffer between coarse
amp steps (690 W on 3-phase) and actual surplus.
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

    def calculate(
        self,
        current_soc: float,
        forecast: pd.DataFrame,
        now: datetime,
        min_solar_power_w: float = 3500,
        min_amps: int = 6,
        max_amps: int = 16,
        phases: int = 3,
        protection_soc_percent: float = 80,
    ) -> EVStrategyResult:
        """Calculate optimal EV charging power level.

        Args:
            current_soc: Current battery SOC (0-100).
            forecast: DataFrame with net_energy_wh column (15-min slots).
            now: Current UTC time.
            min_solar_power_w: Minimum PV production for viability check.
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

        # Step 1: baseline SOC at 21:00 without EV
        sim_no_ev = self._optimizer.simulate_soc(current_soc, forecast)
        baseline_soc = self._soc_at_target(sim_no_ev, target_time)

        # Dynamic protection: min(configured ceiling, baseline without EV)
        protection_target = min(protection_soc_percent, baseline_soc)

        # Step 2: minimum viability check
        min_power_w = min_amps * 230 * phases
        fc_min = forecast.copy()
        fc_min["net_energy_wh"] = fc_min["net_energy_wh"].astype(float)
        fc_min.iloc[0, fc_min.columns.get_loc("net_energy_wh")] -= min_power_w * 0.25
        sim_min = self._optimizer.simulate_soc(current_soc, fc_min)
        min_soc = self._soc_at_target(sim_min, target_time)

        if min_soc < protection_target:
            return EVStrategyResult(
                0,
                0,
                protection_target,
                baseline_soc,
                f"viability fail: {min_soc:.0f}% < {protection_target:.0f}% target",
            )

        # Step 3: bottom-up amp search
        best_amps = min_amps
        for amps in range(min_amps, max_amps + 1):
            wallbox_w = amps * 230 * phases
            fc_test = forecast.copy()
            fc_test["net_energy_wh"] = fc_test["net_energy_wh"].astype(float)
            fc_test.iloc[0, fc_test.columns.get_loc("net_energy_wh")] -= wallbox_w * 0.25
            sim_test = self._optimizer.simulate_soc(current_soc, fc_test)
            soc_at_target = self._soc_at_target(sim_test, target_time)
            max_soc = float(sim_test["soc_percent"].max()) if not sim_test.empty else 0.0

            if soc_at_target < protection_target:
                # This amp level drops below target — use previous best
                break

            best_amps = amps

            if max_soc < 100:
                # No clipping — this is optimal, no need to push higher
                break

        best_power = best_amps * 230 * phases
        return EVStrategyResult(
            best_power,
            best_amps,
            protection_target,
            baseline_soc,
            f"{best_amps}A × {phases}ph = {best_power}W",
        )
