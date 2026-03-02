"""
Appliance signal calculation for washing machine / dishwasher.

Signal logic:
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: One of:
  - Min SOC% >= reserve% + appliance% (SOC never drops below threshold)
  - Grid export before evening > appliance energy (we'd waste the energy anyway)
- RED: Otherwise

The simulation passed to this module already accounts for battery efficiency.
"""

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
    final_soc_percent: float


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
            final_soc_percent=0,
        )

    # Get minimum SOC% from simulation (efficiency already applied)
    min_soc_percent = get_min_soc_percent(simulation)

    # Calculate appliance energy as percentage of battery capacity
    appliance_percent = appliance_energy_wh / capacity_wh * 100

    # ORANGE condition 1: Min SOC >= reserve% + appliance%
    orange_threshold_percent = reserve_percent + appliance_percent

    if min_soc_percent >= orange_threshold_percent:
        return ApplianceSignal(
            signal="orange",
            reason=f"Min SOC {min_soc_percent:.0f}% >= {orange_threshold_percent:.0f}% (reserve {reserve_percent:.0f}% + appliance {appliance_percent:.0f}%)",
            excess_power_w=excess_power,
            final_soc_percent=min_soc_percent,
        )

    # ORANGE condition 2: Grid export before evening > appliance energy
    # If we're going to export energy anyway, might as well use it.
    # Guard: SOC must never drop below reserve% — even with export headroom,
    # the appliance draws power NOW before the export window.
    export_wh = calculate_grid_export_before_evening(
        simulation, evening_hour, local_timezone
    )

    if export_wh >= appliance_energy_wh and min_soc_percent >= reserve_percent:
        return ApplianceSignal(
            signal="orange",
            reason=f"Grid export {export_wh:.0f}Wh >= {appliance_energy_wh:.0f}Wh before {evening_hour}:00",
            excess_power_w=excess_power,
            final_soc_percent=min_soc_percent,
        )

    # RED: SOC drops below threshold and not enough export (or SOC below reserve)
    if min_soc_percent < reserve_percent:
        reason = (
            f"Min SOC {min_soc_percent:.0f}% < reserve {reserve_percent:.0f}%"
        )
    else:
        reason = (
            f"Min SOC {min_soc_percent:.0f}% < {orange_threshold_percent:.0f}%, "
            f"export {export_wh:.0f}Wh < {appliance_energy_wh:.0f}Wh"
        )
    return ApplianceSignal(
        signal="red",
        reason=reason,
        excess_power_w=excess_power,
        final_soc_percent=min_soc_percent,
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
        # Convert timestamp to local time to check if before evening
        if hasattr(t, 'astimezone'):
            local_time = t.astimezone(local_tz)
        else:
            # Handle naive timestamps
            local_time = t

        if local_time.hour >= evening_hour:
            continue  # Past evening, stop counting

        # Export occurs when battery is full and we have excess PV
        soc = row["soc_percent"]
        net = row["net_wh"]

        if soc >= 99.9 and net > 0:
            total_export += net

    logger.debug(f"Grid export before {evening_hour}:00: {total_export:.0f}Wh")
    return total_export


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
