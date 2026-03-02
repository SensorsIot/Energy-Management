"""
Appliance signal calculation for washing machine / dishwasher.

Signal logic:
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: SOC with appliance load simulated never drops below reserve%
- RED: Otherwise (running the appliance would deplete battery below reserve)

The simulation passed to this module already accounts for battery efficiency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ApplianceSignal:
    """Appliance signal result."""
    signal: str  # "green", "orange", or "red"
    reason: str
    excess_power_w: float
    min_soc_percent: float


def simulate_with_appliance(
    simulation: pd.DataFrame,
    appliance_energy_wh: float,
    capacity_wh: float,
) -> float:
    """Subtract appliance energy from SOC trajectory and return min SOC.

    The appliance draws power immediately. We subtract its energy as a
    percentage of battery capacity from the entire SOC trajectory (worst
    case: appliance runs at the SOC minimum). Returns the minimum SOC
    across the adjusted trajectory.
    """
    if simulation.empty or "soc_percent" not in simulation.columns:
        return 0.0

    appliance_percent = appliance_energy_wh / capacity_wh * 100
    adjusted_soc = simulation["soc_percent"] - appliance_percent
    adjusted_soc = adjusted_soc.clip(lower=0)
    return float(adjusted_soc.min())


def calculate_appliance_signal(
    current_pv_w: float,
    current_load_w: float,
    simulation: pd.DataFrame,
    appliance_power_w: float = 2500,
    appliance_energy_wh: float = 1500,
    capacity_wh: float = 10000,
    reserve_percent: float = 10,
    evening_hour: int = 18,
    local_timezone: str = "Europe/Zurich",
) -> ApplianceSignal:
    """
    Calculate appliance signal based on current state and simulation.

    Args:
        current_pv_w: Current PV power in watts
        current_load_w: Current load power in watts
        simulation: DataFrame with soc_percent column (from BatteryOptimizer.simulate_soc)
        appliance_power_w: Power needed for green signal (default 2500W)
        appliance_energy_wh: Energy needed by appliance (default 1500Wh)
        capacity_wh: Battery capacity in Wh (default 10000Wh)
        reserve_percent: Minimum SOC reserve in % (default 10%)
        evening_hour: Hour considered "evening" for export calculation (default 18:00)
        local_timezone: Timezone for evening calculation (default Europe/Zurich)

    Returns:
        ApplianceSignal with signal, reason, and details
    """
    excess_power = current_pv_w - current_load_w

    # GREEN: Current PV excess > appliance power
    if excess_power > appliance_power_w:
        return ApplianceSignal(
            signal="green",
            reason=f"PV excess {int(excess_power)}W > {int(appliance_power_w)}W",
            excess_power_w=excess_power,
            min_soc_percent=0,
        )

    # Simulate SOC with appliance load subtracted
    min_soc_with_appliance = simulate_with_appliance(
        simulation, appliance_energy_wh, capacity_wh
    )
    appliance_percent = appliance_energy_wh / capacity_wh * 100

    # ORANGE: SOC with appliance load never drops below reserve
    if min_soc_with_appliance >= reserve_percent:
        # Add export context if available
        export_wh = calculate_grid_export_before_evening(
            simulation, evening_hour, local_timezone
        )
        export_note = ""
        if export_wh >= appliance_energy_wh:
            export_note = f", export {export_wh:.0f}Wh available"
        return ApplianceSignal(
            signal="orange",
            reason=(
                f"SOC with appliance ≥ {reserve_percent:.0f}% "
                f"(min {min_soc_with_appliance:.0f}% after "
                f"−{appliance_percent:.0f}%{export_note})"
            ),
            excess_power_w=excess_power,
            min_soc_percent=min_soc_with_appliance,
        )

    # RED: running appliance would drop SOC below reserve
    return ApplianceSignal(
        signal="red",
        reason=(
            f"SOC with appliance {min_soc_with_appliance:.0f}% "
            f"< reserve {reserve_percent:.0f}% "
            f"(−{appliance_percent:.0f}% appliance load)"
        ),
        excess_power_w=excess_power,
        min_soc_percent=min_soc_with_appliance,
    )


def calculate_grid_export_before_evening(
    simulation: pd.DataFrame,
    evening_hour: int = 18,
    local_timezone: str = "Europe/Zurich",
) -> float:
    """
    Calculate total grid export (Wh) before evening.

    Grid export occurs when:
    - Battery is full (SOC >= 99.9%)
    - AND net energy is positive (PV > Load)

    Args:
        simulation: DataFrame with soc_percent, net_wh columns
        evening_hour: Hour considered "evening" (default 18:00)
        local_timezone: Timezone for evening calculation

    Returns:
        Total grid export in Wh before evening
    """
    if simulation.empty:
        return 0.0

    if "soc_percent" not in simulation.columns or "net_wh" not in simulation.columns:
        logger.warning("Simulation missing required columns for export calculation")
        return 0.0

    local_tz = ZoneInfo(local_timezone)
    total_export = 0.0

    for t, row in simulation.iterrows():
        if hasattr(t, 'astimezone'):
            local_time = t.astimezone(local_tz)
        else:
            local_time = t

        if local_time.hour >= evening_hour:
            continue

        soc = row["soc_percent"]
        net = row["net_wh"]

        if soc >= 99.9 and net > 0:
            total_export += net

    logger.debug(f"Grid export before {evening_hour}:00: {total_export:.0f}Wh")
    return total_export


def get_min_soc_percent(simulation: pd.DataFrame) -> float:
    """Get minimum SOC in percent from simulation."""
    if simulation.empty:
        return 0

    if "soc_percent" not in simulation.columns:
        return 0

    return float(simulation["soc_percent"].min())


def get_final_soc_percent(simulation: pd.DataFrame) -> float:
    """Get final SOC in percent from simulation."""
    if simulation.empty:
        logger.warning("No simulation data for appliance signal")
        return 0

    if "soc_percent" not in simulation.columns:
        logger.warning("No soc_percent column in simulation")
        return 0

    final_soc_percent = float(simulation["soc_percent"].iloc[-1])
    logger.debug(f"Appliance signal: final_soc_percent={final_soc_percent:.0f}%")
    return final_soc_percent
