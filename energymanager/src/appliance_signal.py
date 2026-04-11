"""
Appliance signal dataclass for washing machine / dishwasher.

Signal logic (implemented in run.py):
- GREEN: Current PV excess > appliance power (can run directly from solar)
- ORANGE: Appliance won't cause grid import until 21:00 next evening
- RED: Appliance would require grid import before 21:00
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
