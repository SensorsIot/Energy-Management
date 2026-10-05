"""EV charging step selection for opportunistic solar mode.

The wallbox is commanded in **amps** (the OCPP profile carries amps and the
wallbox applies them per phase), so the steps here are amp levels. Watts appear
only to compare a step against solar surplus, derived with the single
watts-per-amp factor the OCPP server publishes as
`sensor.wallbox_watts_per_amp`. Nothing converts watts back to amps.

A step therefore exists for every amp the wallbox accepts — 6 A to 16 A on one
phase and on three alike.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime

logger = logging.getLogger(__name__)


def amp_steps(min_a: int, max_a: int) -> list[int]:
    """Every whole amp the wallbox accepts, ascending.

    The wallbox only takes integer amps, so this is the complete set of
    commandable levels — there is no coarser table and no unreachable gap.
    """
    return list(range(int(min_a), int(max_a) + 1))


def step_watts(amps: int, watts_per_amp: float) -> int:
    """Convert an amp step to its expected draw (the only direction used here)."""
    return round(amps * watts_per_amp)


def solar_start_threshold(
    phases: int,
    ev_min_solar_power: float | None,
    wallbox_min_power: float,
) -> float:
    """Minimum surplus (W) required to start solar charging.

    Single-phase power is inherently small (max 3680 W / 16 A), so the
    `ev_min_solar_power` "don't bother below X" gate — sized for 3-phase, where
    the minimum draw is far higher — is **not honored in 1φ mode**; there we
    charge from the wallbox minimum (6 A) so the whole 1φ range is usable.
    """
    if phases == 1:
        return wallbox_min_power
    return ev_min_solar_power or wallbox_min_power


def snap_to_amp_step(
    surplus_w: float,
    steps: list[int],
    watts_per_amp: float,
    threshold_w: float = 0.0,
) -> int:
    """Highest amp step whose expected draw fits within `surplus_w`.

    Steps below `threshold_w` are not offered. If the surplus is below every
    remaining step the lowest one is returned (the home battery covers the
    difference). Returns 0 when no step clears the threshold.
    """
    valid = [a for a in steps if step_watts(a, watts_per_amp) >= threshold_w]
    if not valid:
        return 0
    affordable = [a for a in valid if step_watts(a, watts_per_amp) <= surplus_w]
    return affordable[-1] if affordable else valid[0]


def build_solar_candidates(
    surplus_w: float,
    threshold: float,
    step_up_allowed: bool,
    steps: list[int],
    watts_per_amp: float,
    target_reachable: bool = True,
    both_full_by_evening: bool = False,
) -> tuple[list[int], str]:
    """Decide solar-mode amp-step candidates (Topics 1 & 2).

    Topic 1 target gate (FSD 4.3.6) — the home battery has priority over the
    car. `target_reachable` is the car-excluded forecast of the battery reaching
    its charge target today (`reaches_target_today`):

    - **target_reachable=False**: the battery can no longer reach its charge
      target today, so the car yields *all* surplus to the battery — no
      candidates, no charging. Re-evaluated each cycle from the (car-suppressed)
      current SOC, so it self-corrects: once the car stops, the battery climbs
      and reaches (nearly) the target.
    - **target_reachable=True**: proceed to the Topic 2 step decision below.

    Topic 2 step decision (FSD 4.3.7) — the "step-up" step (one amp above
    `surplus_w`) draws the gap to the next amp from the home battery. Compare
    against actual surplus, including when it is below the minimum step: that
    minimum is itself a step-up and requires permission.

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

    Returns (candidates, gate_reason) where candidates is the ordered list of amp
    levels passed to the home-battery safety loop.
    """
    if not target_reachable:
        return [], "battery won't reach charge target → car yields surplus to battery"
    if both_full_by_evening:
        snap_up_step: list[int] = []
        gate_reason = (
            "battery & car both full by evening (p10) → no step-up (avoid round-trip loss)"
        )
    elif step_up_allowed:
        snap_up = [
            a for a in steps
            if step_watts(a, watts_per_amp) > surplus_w
            and step_watts(a, watts_per_amp) >= threshold
        ]
        snap_up_step = [snap_up[0]] if snap_up else []
        gate_reason = "protected (SOC & min48h >= floor) → step-up allowed"
    else:
        snap_up_step = []
        gate_reason = "not protected → stay at/below surplus (preserve battery)"
    snap_down = [
        a for a in reversed(steps)
        if step_watts(a, watts_per_amp) <= surplus_w
        and step_watts(a, watts_per_amp) >= threshold
    ]
    return snap_up_step + snap_down, gate_reason


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
