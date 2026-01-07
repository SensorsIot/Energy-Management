"""
Battery SOC trajectory simulation.

Simulates battery state of charge over time based on PV and Load forecasts.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class SocSimulator:
    """Simulate battery SOC trajectory."""

    def __init__(
        self,
        capacity_wh: float = 10000,
        reserve_percent: float = 10,
        charge_efficiency: float = 0.95,
        discharge_efficiency: float = 0.95,
        max_charge_w: float = 5000,
        max_discharge_w: float = 5000,
    ):
        self.capacity_wh = capacity_wh
        self.reserve_percent = reserve_percent
        self.charge_efficiency = charge_efficiency
        self.discharge_efficiency = discharge_efficiency
        self.max_charge_wh_per_15min = max_charge_w * 0.25  # W to Wh per 15 min
        self.max_discharge_wh_per_15min = max_discharge_w * 0.25

    def simulate_trajectory(
        self,
        soc_percent: float,
        forecast: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Simulate battery SOC trajectory over forecast period.

        Args:
            soc_percent: Current SOC (0-100%)
            forecast: DataFrame with columns:
                - pv_energy_wh: PV energy per period
                - load_energy_wh: Load energy per period
                - net_energy_wh: PV - Load

        Returns:
            DataFrame with columns:
                - soc_percent: Simulated SOC at each time
                - soc_wh: Simulated energy in battery
                - pv_energy_wh: PV energy (from input)
                - load_energy_wh: Load energy (from input)
                - net_energy_wh: Net energy (from input)
                - battery_flow_wh: Actual battery charge/discharge
                - grid_flow_wh: Energy to/from grid (positive = import)
        """
        if forecast.empty:
            logger.warning("Empty forecast, cannot simulate")
            return pd.DataFrame()

        # Initialize
        e_bat = soc_percent / 100 * self.capacity_wh
        results = []

        for t, row in forecast.iterrows():
            net_wh = row["net_energy_wh"]

            if net_wh > 0:
                # Surplus: charge battery
                # Apply charging efficiency and cap at max charge rate
                charge_available = net_wh * self.charge_efficiency
                charge_possible = min(
                    charge_available,
                    self.max_charge_wh_per_15min,
                    self.capacity_wh - e_bat,  # Don't exceed capacity
                )
                battery_flow = -charge_possible  # Negative = charging
                e_bat += charge_possible
                # Excess goes to grid (export)
                grid_flow = -(net_wh - charge_possible / self.charge_efficiency)
            else:
                # Deficit: discharge battery
                # Need more energy due to discharge efficiency
                discharge_needed = -net_wh / self.discharge_efficiency
                discharge_possible = min(
                    discharge_needed,
                    self.max_discharge_wh_per_15min,
                    e_bat,  # Can't discharge below 0
                )
                battery_flow = discharge_possible * self.discharge_efficiency  # Positive = discharging
                e_bat -= discharge_possible
                # Shortfall comes from grid (import)
                grid_flow = -net_wh - battery_flow  # Positive = import

            soc_pct = e_bat / self.capacity_wh * 100

            results.append({
                "time": t,
                "soc_percent": soc_pct,
                "soc_wh": e_bat,
                "pv_energy_wh": row["pv_energy_wh"],
                "load_energy_wh": row["load_energy_wh"],
                "net_energy_wh": net_wh,
                "battery_flow_wh": battery_flow,
                "grid_flow_wh": grid_flow,
            })

        df = pd.DataFrame(results)
        df = df.set_index("time")

        logger.info(
            f"Simulated SOC trajectory: {soc_percent:.0f}% -> "
            f"{df['soc_percent'].iloc[-1]:.0f}%, "
            f"min={df['soc_percent'].min():.0f}%, "
            f"max={df['soc_percent'].max():.0f}%"
        )

        return df

    def find_minimum_soc(
        self,
        simulation: pd.DataFrame,
    ) -> Tuple[float, datetime]:
        """
        Find the minimum SOC point in the simulation.

        Returns:
            (min_soc_percent, time_of_minimum)
        """
        if simulation.empty:
            return 0.0, datetime.now(timezone.utc)

        min_idx = simulation["soc_percent"].idxmin()
        min_soc = simulation["soc_percent"].min()

        return min_soc, min_idx

    def check_discharge_allowed(
        self,
        simulation: pd.DataFrame,
    ) -> Tuple[bool, str, float, datetime]:
        """
        Check if battery discharge should be allowed based on simulation.

        Returns:
            (allowed, reason, min_soc_percent, min_soc_time)
        """
        if simulation.empty:
            return True, "No forecast data", 0.0, datetime.now(timezone.utc)

        min_soc, min_time = self.find_minimum_soc(simulation)

        if min_soc >= self.reserve_percent:
            return (
                True,
                f"Safe: min SOC {min_soc:.0f}% at {min_time:%H:%M}",
                min_soc,
                min_time,
            )
        else:
            return (
                False,
                f"Block: would hit {min_soc:.0f}% at {min_time:%H:%M}",
                min_soc,
                min_time,
            )

    def calculate_summary(
        self,
        simulation: pd.DataFrame,
    ) -> dict:
        """
        Calculate summary statistics from simulation.

        Returns:
            dict with summary values
        """
        if simulation.empty:
            return {}

        return {
            "soc_start_percent": simulation["soc_percent"].iloc[0],
            "soc_end_percent": simulation["soc_percent"].iloc[-1],
            "soc_min_percent": simulation["soc_percent"].min(),
            "soc_max_percent": simulation["soc_percent"].max(),
            "pv_total_wh": simulation["pv_energy_wh"].sum(),
            "load_total_wh": simulation["load_energy_wh"].sum(),
            "grid_import_wh": simulation["grid_flow_wh"].clip(lower=0).sum(),
            "grid_export_wh": (-simulation["grid_flow_wh"]).clip(lower=0).sum(),
            "battery_charge_wh": (-simulation["battery_flow_wh"]).clip(lower=0).sum(),
            "battery_discharge_wh": simulation["battery_flow_wh"].clip(lower=0).sum(),
        }
