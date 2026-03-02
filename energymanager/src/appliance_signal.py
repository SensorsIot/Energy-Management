"""
Appliance signal dataclass for washing machine / dishwasher.

Signal logic (implemented in run.py using InfluxDB forecast functions):
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: Forecast min SOC with appliance load stays above reserve%
- RED: Otherwise (running the appliance would deplete battery below reserve)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApplianceSignal:
    """Appliance signal result."""
    signal: str  # "green", "orange", or "red"
    reason: str
    excess_power_w: float
    min_soc_percent: float
