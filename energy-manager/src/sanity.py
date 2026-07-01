"""Runtime sanity checks for power sensor readings (FSD 1.9)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_BOUNDS: dict[str, tuple[float, float]] = {
    "pv_w": (0, 15_000),
    "load_w": (0, 15_000),
    "wallbox_w": (0, 12_000),
    "grid_w": (-15_000, 15_000),
    "battery_w": (-6_000, 6_000),
}


def validate_power_readings(
    *,
    grid_w: float | None = None,
    pv_w: float | None = None,
    load_w: float | None = None,
    wallbox_w: float | None = None,
    battery_w: float | None = None,
) -> list[str]:
    """Check power readings against known physical bounds.

    Returns a list of warning strings (empty means all OK).
    Warnings are also logged at WARNING level.
    This function never raises — it is purely advisory and must not block control.
    """
    readings = {
        "grid_w": grid_w,
        "pv_w": pv_w,
        "load_w": load_w,
        "wallbox_w": wallbox_w,
        "battery_w": battery_w,
    }
    warnings: list[str] = []

    for name, value in readings.items():
        if value is None:
            continue
        lo, hi = _BOUNDS[name]
        if value < lo or value > hi:
            msg = f"Sanity: {name}={value:.0f} W outside bounds [{lo}, {hi}]"
            warnings.append(msg)
            logger.warning(msg)

    return warnings
