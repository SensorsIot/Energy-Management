"""
Appliance signal calculation for washing machine / dishwasher.

Signal logic:
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: Simulation shows final SOC >= appliance energy (battery can absorb the load)
- RED: Otherwise (would require grid import)

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
    final_soc_wh: float


def calculate_appliance_signal(
    current_pv_w: float,
    current_load_w: float,
    simulation: pd.DataFrame,
    appliance_power_w: float = 2500,
    appliance_energy_wh: float = 1500,
) -> ApplianceSignal:
    """
    Calculate appliance signal based on current state and simulation.

    Args:
        current_pv_w: Current PV power in watts
        current_load_w: Current load power in watts
        simulation: DataFrame with soc_wh column (from BatteryOptimizer.simulate_soc)
        appliance_power_w: Power needed for green signal (default 2500W)
        appliance_energy_wh: Energy threshold for orange signal (default 1500Wh)

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
            final_soc_wh=0,
        )

    # Get final SOC from simulation (efficiency already applied)
    final_soc_wh = get_final_soc_wh(simulation)

    # ORANGE: Final SOC >= appliance energy threshold
    # This means the battery has enough reserve to absorb the appliance load
    if final_soc_wh >= appliance_energy_wh:
        return ApplianceSignal(
            signal="orange",
            reason=f"Final SOC {int(final_soc_wh)}Wh >= {int(appliance_energy_wh)}Wh",
            excess_power_w=excess_power,
            final_soc_wh=final_soc_wh,
        )

    # RED: Otherwise
    return ApplianceSignal(
        signal="red",
        reason=f"Final SOC {int(final_soc_wh)}Wh < {int(appliance_energy_wh)}Wh",
        excess_power_w=excess_power,
        final_soc_wh=final_soc_wh,
    )


def get_final_soc_wh(simulation: pd.DataFrame) -> float:
    """
    Get final SOC in Wh from simulation.

    The simulation DataFrame comes from BatteryOptimizer.simulate_soc and
    already accounts for charge/discharge efficiency.

    Args:
        simulation: DataFrame with soc_wh column

    Returns:
        Final SOC in Wh, or 0 if simulation is empty
    """
    if simulation.empty:
        logger.warning("No simulation data for appliance signal")
        return 0

    if "soc_wh" not in simulation.columns:
        logger.warning("No soc_wh column in simulation")
        return 0

    final_soc_wh = float(simulation["soc_wh"].iloc[-1])

    logger.debug(f"Appliance signal: final_soc_wh={final_soc_wh:.0f}Wh")

    return final_soc_wh
