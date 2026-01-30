"""
Appliance signal calculation for washing machine / dishwasher.

Signal logic:
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: Min SOC% >= reserve% + appliance% (SOC never drops below threshold)
- RED: Otherwise

The simulation passed to this module already accounts for battery efficiency.
"""

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ApplianceSignal:
    """Appliance signal result."""
    signal: str  # "green", "orange", or "red"
    reason: str
    excess_power_w: float
    final_soc_percent: float


def calculate_appliance_signal(
    current_pv_w: float,
    current_load_w: float,
    simulation: pd.DataFrame,
    appliance_power_w: float = 2500,
    appliance_energy_wh: float = 1500,
    capacity_wh: float = 10000,
    reserve_percent: float = 10,
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
            final_soc_percent=0,
        )

    # Get minimum SOC% from simulation (efficiency already applied)
    min_soc_percent = get_min_soc_percent(simulation)

    # Calculate appliance energy as percentage of battery capacity
    appliance_percent = appliance_energy_wh / capacity_wh * 100

    # ORANGE: Min SOC >= reserve% + appliance% (SOC never drops below threshold)
    orange_threshold_percent = reserve_percent + appliance_percent

    if min_soc_percent >= orange_threshold_percent:
        return ApplianceSignal(
            signal="orange",
            reason=f"Min SOC {min_soc_percent:.0f}% >= {orange_threshold_percent:.0f}% (reserve {reserve_percent:.0f}% + appliance {appliance_percent:.0f}%)",
            excess_power_w=excess_power,
            final_soc_percent=min_soc_percent,
        )

    # RED: SOC drops below threshold at some point
    return ApplianceSignal(
        signal="red",
        reason=f"Min SOC {min_soc_percent:.0f}% < {orange_threshold_percent:.0f}% (reserve {reserve_percent:.0f}% + appliance {appliance_percent:.0f}%)",
        excess_power_w=excess_power,
        final_soc_percent=min_soc_percent,
    )


def get_min_soc_percent(simulation: pd.DataFrame) -> float:
    """
    Get minimum SOC in percent from simulation.

    Args:
        simulation: DataFrame with soc_percent column

    Returns:
        Minimum SOC in %, or 0 if simulation is empty
    """
    if simulation.empty:
        return 0

    if "soc_percent" not in simulation.columns:
        return 0

    return float(simulation["soc_percent"].min())


def get_final_soc_percent(simulation: pd.DataFrame) -> float:
    """
    Get final SOC in percent from simulation.

    The simulation DataFrame comes from BatteryOptimizer.simulate_soc and
    already accounts for charge/discharge efficiency.

    Args:
        simulation: DataFrame with soc_percent column

    Returns:
        Final SOC in %, or 0 if simulation is empty
    """
    if simulation.empty:
        logger.warning("No simulation data for appliance signal")
        return 0

    if "soc_percent" not in simulation.columns:
        logger.warning("No soc_percent column in simulation")
        return 0

    final_soc_percent = float(simulation["soc_percent"].iloc[-1])

    logger.debug(f"Appliance signal: final_soc_percent={final_soc_percent:.0f}%")

    return final_soc_percent
