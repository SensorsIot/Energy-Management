"""EV charging power calculation for opportunistic solar mode.

Clamps excess power to wallbox min/max limits:
  excess >= min_power_w → charge at min(excess, max_power_w)
  excess <  min_power_w → pause (0W, transaction stays alive)

Phase-gap handling:
  The wallbox has a dead zone between 1φ max (3680 W) and 3φ min (4140 W).
  If the target lands in this gap it is snapped to the nearest achievable power.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

_PHASE_GAP_LO = 3680  # single-phase maximum (W) — 16A × 230V
_PHASE_GAP_HI = 4140  # three-phase minimum (W)

# Valid 3-phase power steps — M-Bus ground truth (2026-03-04 calibration)
# 6A=3962, 7A=4354, 8A=5117, 9A=5727, 10A=6288, 11A=7034, 12A=7624
POWER_STEPS_3P = [3962, 4354, 5117, 5727, 6288, 7034, 7624]

# Valid single-phase power steps — 230 W/A (live single-phase MeterValues,
# 2026-07-09, ~230 W/A linear through origin), 6A..16A. The wallbox draws only
# the connected phase, so these are ~1/3 of the 3-phase watts at the same amp
# and cover the whole 1φ range (1380–3680 W). Which table applies is chosen from
# the OCPP server's detected phase count (`sensor.wallbox_phases`).
POWER_STEPS_1P = [1380, 1610, 1840, 2070, 2300, 2530, 2760, 2990, 3220, 3450, 3680]


def power_steps_for_phases(phases: int) -> list[int]:
    """Return the power-step table for the connected cable's phase count."""
    return POWER_STEPS_1P if phases == 1 else POWER_STEPS_3P


def solar_start_threshold(
    phases: int,
    ev_min_solar_power: float | None,
    wallbox_min_power: float,
) -> float:
    """Minimum surplus (W) required to start solar charging.

    Single-phase power is inherently small (max 3680 W / 16 A), so the
    `ev_min_solar_power` "don't bother below X" gate — sized for 3-phase, where
    the minimum is already 4140 W — is **not honored in 1φ mode**; there we
    charge from the wallbox minimum (6 A) so the whole 1φ range is usable.
    """
    if phases == 1:
        return wallbox_min_power
    return ev_min_solar_power or wallbox_min_power


@dataclass
class EVChargingResult:
    """Result of EV charging power calculation."""

    target_power_w: float       # Power to set on wallbox (0 = pause)
    available_excess_w: float   # Computed solar excess
    reason: str


def resolve_phase_gap(target_w: float, battery_full: bool) -> float:
    """Snap target out of the 1φ/3φ dead zone (3680–4140 W).

    Battery not full → prefer 1φ (3680) so surplus charges battery.
    Battery full     → prefer 3φ (4140) to use power in car.
    """
    if _PHASE_GAP_LO < target_w < _PHASE_GAP_HI:
        return _PHASE_GAP_HI if battery_full else _PHASE_GAP_LO
    return target_w


def calculate_ev_power(
    excess_w: float,
    min_power_w: float = 1400,
    max_power_w: float = 11000,
    battery_full: bool = False,
) -> EVChargingResult:
    """Calculate target EV charging power from solar excess.

    Args:
        excess_w: Available solar excess in watts
        min_power_w: Minimum wallbox power (below this → pause)
        max_power_w: Maximum wallbox power (clamp ceiling)
        battery_full: Whether the home battery is full — affects phase-gap resolution

    Returns:
        EVChargingResult with target power, excess, and reason

    """
    if excess_w >= min_power_w:
        target = min(excess_w, max_power_w)
        target = resolve_phase_gap(target, battery_full)
        return EVChargingResult(
            target_power_w=target,
            available_excess_w=excess_w,
            reason=f"Charging {target:.0f}W (excess {excess_w:.0f}W)",
        )

    return EVChargingResult(
        target_power_w=0,
        available_excess_w=excess_w,
        reason=f"Pause: excess {excess_w:.0f}W < {min_power_w:.0f}W minimum",
    )


def snap_to_power_step(
    surplus_w: float,
    min_power_w: float = 3962,
    max_power_w: float = 7624,
    steps: list[int] | None = None,
) -> int:
    """Snap surplus to the best discrete power step within [min, max].

    Picks highest step ≤ surplus from `steps` (default: the 3-phase table).
    If surplus is below all steps, returns the minimum step
    (battery covers the difference).
    Returns 0 only if no step fits within [min, max].
    """
    steps = steps if steps is not None else POWER_STEPS_3P
    valid = [s for s in steps if min_power_w <= s <= max_power_w]
    if not valid:
        return 0
    # Highest step that fits within surplus
    best = [s for s in valid if s <= surplus_w]
    if best:
        return best[-1]
    # Surplus below all steps — return minimum (battery covers gap)
    return valid[0]


def simulate_house_and_car(
    steps_wh: Iterable[tuple[datetime, float]],
    *,
    house_kwh: float,
    house_cap_kwh: float,
    house_ceil_kwh: float,
    car_efficiency: float,
) -> Iterator[tuple[datetime, float, float]]:
    """Allocate forecast net energy to the house battery, then the car.

    The house battery is a buffer with priority: surplus first refills it up to
    `house_ceil_kwh` (its dynamic charge target, FSD 4.2.4); the overflow past
    that goes to the car at `car_efficiency`. Deficits drain the house battery
    only — the car is never discharged.

    Yields `(timestamp, house_kwh, car_kwh_added)` per forecast step, where
    `car_kwh_added` is the cumulative energy delivered to the car so far. It is
    reported as **energy, not SOC**, because the split depends only on the house
    battery: the same curve therefore applies to any car SOC, which is what lets
    the step-up suppression gate (Section 4.3.7) re-evaluate against a *live*
    car SOC between the 15-min simulation runs. Callers convert with their own
    SOC and capacity. Both outputs are monotonic over a surplus run, so the last
    point at or before a cutoff is that cutoff's value.
    """
    car_kwh_added = 0.0
    for ts, net_wh in steps_wh:
        net_kwh = net_wh / 1000
        if net_kwh >= 0:
            headroom = max(0.0, house_ceil_kwh - house_kwh)
            to_house = min(net_kwh, headroom)
            house_kwh += to_house
            car_kwh_added += (net_kwh - to_house) * car_efficiency
        else:
            house_kwh = max(0.0, house_kwh + net_kwh)
        house_kwh = min(house_kwh, house_cap_kwh)
        yield ts, house_kwh, car_kwh_added


def build_solar_candidates(
    candidate_power: int,
    threshold: float,
    step_up_allowed: bool,
    target_reachable: bool = True,
    steps: list[int] | None = None,
    both_full_by_evening: bool = False,
) -> tuple[list[int], str]:
    """Decide solar-mode power-step candidates (Topics 1 & 2).

    Topic 1 target gate (FSD 4.3.6) — the home battery has priority over the
    car. `target_reachable` is the car-excluded forecast of the battery reaching
    its charge target today (`will_battery_hit_full`):

    - **target_reachable=False**: the battery can no longer reach its charge
      target today, so the car yields *all* surplus to the battery — no
      candidates, no charging. Re-evaluated each cycle from the (car-suppressed)
      current SOC, so it self-corrects: once the car stops, the battery climbs
      and reaches (nearly) the target.
    - **target_reachable=True**: proceed to the Topic 2 step decision below.

    Topic 2 step decision (FSD 4.3.7) — the "step-up" step (one level above
    candidate_power) draws the gap to the next amp step from the home battery:

    - **step_up_allowed=True**: the battery is still protected — both the 48 h
      forecast min **and** the current SOC are `>= no_buy_floor_percent` — so
      bridging the gap from the battery is fine. Include the step-up step.
    - **step_up_allowed=False**: stay at-or-below surplus (snap-down only) so the
      EV never pulls the home battery below the protection floor. (The current-SOC
      condition matters because the 48 h forecast excludes the wallbox load and so
      reads optimistically high while the car is draining the real battery.)

    `both_full_by_evening` suppresses step-up even when it is permitted: when the
    **conservative p10** forecast says the home battery *and* the car both reach
    their targets by the end of today, stepping up buys nothing — the car ends the
    day at the same SOC either way — while routing the gap through the home battery
    pays a round-trip loss. Step-up is then pointless, so stay at/below surplus and
    let the surplus reach the car directly. Overridden by nothing: it only ever
    *removes* the draining step, so it cannot endanger the battery.

    Returns (candidates, gate_reason) where candidates is the ordered list
    passed to the home-battery safety loop.
    """
    steps = steps if steps is not None else POWER_STEPS_3P
    if not target_reachable:
        return [], "battery won't reach charge target → car yields surplus to battery"
    if both_full_by_evening:
        snap_up_step = []
        gate_reason = (
            "battery & car both full by evening (p10) → no step-up (avoid round-trip loss)"
        )
    elif step_up_allowed:
        snap_up = [
            s for s in steps
            if s > candidate_power and s >= threshold
        ]
        snap_up_step = [snap_up[0]] if snap_up else []
        gate_reason = "protected (SOC & min48h >= floor) → step-up allowed"
    else:
        snap_up_step = []
        gate_reason = "not protected → stay at/below surplus (preserve battery)"
    snap_down = [
        s for s in reversed(steps)
        if s <= candidate_power and s >= threshold
    ]
    return snap_up_step + snap_down, gate_reason
