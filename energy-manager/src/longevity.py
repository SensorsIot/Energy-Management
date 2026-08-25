"""Home-battery longevity ceiling (FSD 4.2.4).

The end-of-charge SOC is a *goal*, not a derived quantity: holding an LFP pack
below full reduces high-SOC dwell. The only exception is the periodic
calibration charge — the pack must reach the top often enough for the BMS to
re-anchor its SOC estimate against the flat LFP voltage curve.

`battery_longevity()` is pure: it takes three scalars and returns the desired
constraint. It performs no I/O and writes nothing, so the caller owns the
register and there is exactly one place that talks to the inverter.

`Disabled` is a value, not an absence: the constraint returned is an explicit
100 % (no cap) that the caller applies like any other, so turning the feature
off can never strand a lower ceiling in the register.
"""

from dataclasses import dataclass

NO_CAP = 100.0


@dataclass(frozen=True)
class LongevityConstraint:
    """The desired end-of-charge SOC and the reason for it.

    Attributes:
        soc_ceiling: Target SOC (%); 100.0 means "no cap".
        reason: Human-readable explanation, published for the dashboard.

    """

    soc_ceiling: float
    reason: str


def battery_longevity(
    *,
    enabled: bool,
    calibration_due: bool,
    ceiling: float,
) -> LongevityConstraint:
    """Return the end-of-charge SOC constraint (Section 4.2.4).

    Three outcomes, first match wins:

    1. Disabled -> 100 % ("no cap"), so the caller clears any ceiling it left.
    2. Calibration charge due -> 100 %, letting the pack reach the top so the
       LFP BMS re-anchors its SOC estimate.
    3. Otherwise -> `ceiling`, the configured longevity goal.

    Args:
        enabled: The `battery.longevity_enabled` switch.
        calibration_due: True when the pack has not reached >= 99 % within
            `battery.longevity_calibration_days`.
        ceiling: The configured goal (%), `battery.longevity_ceiling`.

    Returns:
        The constraint to apply. Never None — a disabled feature returns the
        neutral 100 % so "off" is written rather than skipped.

    """
    if not enabled:
        return LongevityConstraint(NO_CAP, "longevity disabled")
    if calibration_due:
        return LongevityConstraint(NO_CAP, "LFP calibration charge → 100%")
    return LongevityConstraint(float(ceiling), f"longevity ceiling {ceiling:.0f}%")
