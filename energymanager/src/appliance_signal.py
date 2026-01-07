"""
Appliance signal calculation for washing machine / dishwasher.

Signal logic:
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: Base simulation shows enough surplus at tomorrow 21:00 (>= appliance energy)
- RED: Otherwise (would require grid import)
"""

import logging
from dataclasses import dataclass
from typing import Tuple

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ApplianceSignal:
    """Appliance signal result."""
    signal: str  # "green", "orange", or "red"
    reason: str
    excess_power_w: float
    forecast_surplus_wh: float


def calculate_appliance_signal(
    current_pv_w: float,
    current_load_w: float,
    current_soc_percent: float,
    forecast: pd.DataFrame,
    capacity_wh: float,
    appliance_power_w: float = 2500,
    appliance_energy_wh: float = 1500,
) -> ApplianceSignal:
    """
    Calculate appliance signal based on current state and forecast.

    Args:
        current_pv_w: Current PV power in watts
        current_load_w: Current load power in watts
        current_soc_percent: Current battery SOC (0-100)
        forecast: DataFrame with net_energy_wh column (PV - Load per period)
        capacity_wh: Battery capacity in Wh
        appliance_power_w: Power needed for green signal (default 2500W)
        appliance_energy_wh: Energy per cycle for orange threshold (default 1500Wh)

    Returns:
        ApplianceSignal with signal, reason, and details
    """
    excess_power = current_pv_w - current_load_w

    # GREEN: Current PV excess > appliance power
    if excess_power > appliance_power_w:
        return ApplianceSignal(
            signal="green",
            reason=f"PV Überschuss {int(excess_power)}W",
            excess_power_w=excess_power,
            forecast_surplus_wh=0,
        )

    # Calculate forecast surplus using base simulation (no optimization)
    forecast_surplus_wh = calculate_forecast_surplus(
        current_soc_percent=current_soc_percent,
        forecast=forecast,
        capacity_wh=capacity_wh,
    )

    # ORANGE: Forecast shows enough surplus
    if forecast_surplus_wh >= appliance_energy_wh:
        return ApplianceSignal(
            signal="orange",
            reason=f"Prognose +{int(forecast_surplus_wh)}Wh",
            excess_power_w=excess_power,
            forecast_surplus_wh=forecast_surplus_wh,
        )

    # RED: Otherwise
    return ApplianceSignal(
        signal="red",
        reason="Kein Überschuss",
        excess_power_w=excess_power,
        forecast_surplus_wh=forecast_surplus_wh,
    )


def calculate_forecast_surplus(
    current_soc_percent: float,
    forecast: pd.DataFrame,
    capacity_wh: float,
) -> float:
    """
    Run base simulation (battery always ON) until end of forecast.

    Returns unclamped SOC in Wh at end of forecast.
    Positive = surplus, Negative = deficit.
    """
    if forecast.empty:
        logger.warning("No forecast data for appliance signal")
        return 0

    # Start with current SOC
    soc_wh = current_soc_percent / 100 * capacity_wh

    # Simple energy balance: sum all net energy
    # net_energy_wh = PV - Load (positive = surplus)
    if "net_energy_wh" in forecast.columns:
        total_net_energy = forecast["net_energy_wh"].sum()
    else:
        logger.warning("No net_energy_wh in forecast")
        return 0

    # Unclamped SOC at end = current SOC + net energy
    unclamped_soc = soc_wh + total_net_energy

    logger.debug(f"Appliance signal: SOC={current_soc_percent:.1f}%, "
                 f"net_energy={total_net_energy:.0f}Wh, "
                 f"unclamped_soc={unclamped_soc:.0f}Wh")

    return unclamped_soc
