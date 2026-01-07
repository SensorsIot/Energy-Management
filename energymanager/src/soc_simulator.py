"""
Battery SOC trajectory simulation.

Implements FSD Section 4.2: SOC Simulation

4.2.1 Basic Loop: net = PV - Load → battery flow
4.2.2 Efficiency: battery flow → SOC change
4.2.3 Output: SOC forecast curve to InfluxDB
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List

import pandas as pd

logger = logging.getLogger(__name__)


class SocSimulator:
    """
    Simulate battery SOC trajectory.

    FSD 4.2: The SOC simulation predicts battery state over the forecast horizon.
    This is the base curve for all energy management decisions.
    """

    def __init__(
        self,
        capacity_wh: float = 10000,
        efficiency: float = 0.95,
        max_power_w: float = 5000,
    ):
        """
        Initialize SOC simulator.

        Args:
            capacity_wh: Battery capacity in Wh (default: 10000)
            efficiency: Efficiency per direction, 0-1 (default: 0.95)
            max_power_w: Max charge/discharge power in W (default: 5000)
        """
        self.capacity_wh = capacity_wh
        self.efficiency = efficiency
        self.max_power_w = max_power_w
        # Convert W to Wh per 15-min timestep
        self.max_wh_per_15min = max_power_w * 0.25

    def simulate(
        self,
        starting_soc_percent: float,
        pv_forecast: pd.DataFrame,
        load_forecast: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Simulate SOC trajectory over forecast horizon.

        FSD 4.2.1: Basic Loop (net = PV - Load → battery flow)
        FSD 4.2.2: Efficiency (battery flow → SOC change)

        Args:
            starting_soc_percent: Current SOC from HA sensor (0-100%)
            pv_forecast: DataFrame with 'power_w_p50' column, indexed by time
            load_forecast: DataFrame with 'power_w_p50' column, indexed by time

        Returns:
            DataFrame with 'soc_percent' column, indexed by time
        """
        if pv_forecast.empty or load_forecast.empty:
            logger.warning("Empty forecast, cannot simulate")
            return pd.DataFrame()

        # Align forecasts to common timestamps
        common_index = pv_forecast.index.intersection(load_forecast.index)
        if len(common_index) == 0:
            logger.warning("No overlapping timestamps in forecasts")
            return pd.DataFrame()

        # Initialize SOC in Wh
        soc_wh = starting_soc_percent / 100 * self.capacity_wh

        results = []

        for t in common_index:
            # 4.2.1 Step 1: Get forecast values (convert W to Wh per 15 min)
            pv_wh = pv_forecast.loc[t, 'power_w_p50'] * 0.25
            load_wh = load_forecast.loc[t, 'power_w_p50'] * 0.25

            # 4.2.1 Step 2: Calculate net energy
            net_wh = pv_wh - load_wh

            # 4.2.1 Step 3: Determine battery flow
            # (battery_flow = net_wh, positive=charge, negative=discharge)
            battery_flow = net_wh

            # 4.2.2: Apply efficiency and update SOC
            if battery_flow > 0:
                # Charging: energy_stored = battery_flow × efficiency
                energy_stored = battery_flow * self.efficiency
                # Cap at max charge rate and available capacity
                energy_stored = min(
                    energy_stored,
                    self.max_wh_per_15min,
                    self.capacity_wh - soc_wh
                )
                soc_wh = soc_wh + energy_stored
            else:
                # Discharging: energy_withdrawn = |battery_flow| ÷ efficiency
                energy_withdrawn = abs(battery_flow) / self.efficiency
                # Cap at max discharge rate and available energy
                energy_withdrawn = min(
                    energy_withdrawn,
                    self.max_wh_per_15min,
                    soc_wh
                )
                soc_wh = soc_wh - energy_withdrawn

            # Convert to percent
            soc_percent = soc_wh / self.capacity_wh * 100

            # 4.2.1 Step 4: Memorize
            results.append({
                'time': t,
                'soc_percent': soc_percent,
                'pv_wh': pv_wh,
                'load_wh': load_wh,
                'net_wh': net_wh,
                'battery_flow': battery_flow,
            })

        df = pd.DataFrame(results)
        df = df.set_index('time')

        logger.info(
            f"SOC simulation: {starting_soc_percent:.0f}% → {soc_percent:.0f}%, "
            f"min={df['soc_percent'].min():.0f}%, max={df['soc_percent'].max():.0f}%"
        )

        return df

    def simulate_unclamped(
        self,
        starting_soc_percent: float,
        pv_forecast: pd.DataFrame,
        load_forecast: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Simulate SOC without clamping to 0-100% range.

        Used for deficit/surplus calculation in optimization.
        Negative SOC = energy deficit (would need grid import)
        SOC > 100% = energy surplus (would export to grid)

        Args:
            starting_soc_percent: Current SOC from HA sensor (0-100%)
            pv_forecast: DataFrame with 'power_w_p50' column
            load_forecast: DataFrame with 'power_w_p50' column

        Returns:
            DataFrame with 'soc_percent' column (can be negative or >100%)
        """
        if pv_forecast.empty or load_forecast.empty:
            logger.warning("Empty forecast, cannot simulate")
            return pd.DataFrame()

        common_index = pv_forecast.index.intersection(load_forecast.index)
        if len(common_index) == 0:
            logger.warning("No overlapping timestamps in forecasts")
            return pd.DataFrame()

        soc_wh = starting_soc_percent / 100 * self.capacity_wh

        results = []

        for t in common_index:
            pv_wh = pv_forecast.loc[t, 'power_w_p50'] * 0.25
            load_wh = load_forecast.loc[t, 'power_w_p50'] * 0.25
            net_wh = pv_wh - load_wh
            battery_flow = net_wh

            # Apply efficiency WITHOUT clamping
            if battery_flow > 0:
                energy_stored = battery_flow * self.efficiency
                soc_wh = soc_wh + energy_stored
            else:
                energy_withdrawn = abs(battery_flow) / self.efficiency
                soc_wh = soc_wh - energy_withdrawn

            soc_percent = soc_wh / self.capacity_wh * 100

            results.append({
                'time': t,
                'soc_percent': soc_percent,
            })

        df = pd.DataFrame(results)
        df = df.set_index('time')

        return df

    # =========================================================================
    # Helper methods for decision making (used by 4.3, 4.4, 4.5)
    # =========================================================================

    def get_soc_at_target(
        self,
        simulation: pd.DataFrame,
        target_time: datetime,
    ) -> float:
        """
        Get SOC at a specific target time.

        Args:
            simulation: DataFrame from simulate() or simulate_unclamped()
            target_time: Target datetime

        Returns:
            SOC percent at target time (or closest time before)
        """
        if simulation.empty:
            return 0.0

        # Find closest time <= target
        valid_times = simulation.index[simulation.index <= target_time]
        if len(valid_times) == 0:
            return simulation['soc_percent'].iloc[0]

        return simulation.loc[valid_times[-1], 'soc_percent']

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

        min_idx = simulation['soc_percent'].idxmin()
        min_soc = simulation['soc_percent'].min()

        return min_soc, min_idx

    def calculate_deficit(
        self,
        soc_at_target_percent: float,
    ) -> float:
        """
        Calculate energy deficit in Wh.

        FSD 4.3.2: deficit_wh = |soc_at_target|/100 × capacity

        Args:
            soc_at_target_percent: Unclamped SOC at target (can be negative)

        Returns:
            Deficit in Wh (0 if no deficit)
        """
        if soc_at_target_percent >= 0:
            return 0.0

        return abs(soc_at_target_percent) / 100 * self.capacity_wh
