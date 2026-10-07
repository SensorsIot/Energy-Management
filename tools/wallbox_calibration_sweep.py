#!/usr/bin/env python3
"""Measure what each commanded amp actually draws.

Commands each amp level in turn and measures the resulting draw from the house
meters, so it yields both calibrations the OCPP server needs:

- `WATTS_PER_AMP` — watts delivered per commanded amp.
- `METER_SCALE` / `METER_SCALE_1P` — how far the wallbox's own meter is out.

The true draw comes from an energy balance over meters that do not involve the
wallbox, so it is independent of the figure being checked:

    true_ev = (PV - grid - battery) - house

`sensor.house_load_power` is a Shelly 3EM clamp on the house circuits, separate
from the wallbox, so the balance is not circular. Validated on 2026-10-04: over
three wallbox-idle windows it closed to -35 / +15 / -30 W.

**Run it at night.** PV is the dominant noise term; with PV at zero and the house
at its most stable the balance reduces to `-grid - house`. Charging from the grid
costs the tariff difference on 1-3 kWh and is the method FSD 7.1 already uses.
Daytime runs work but every step carries more spread — watch the `sd` column and
the rejected-sample count.

**Stop the energy-manager add-on first.** It writes the current limit every cycle
and will fight this script:

    ssh root@<ha-host> 'ha addons stop 8d023bea_energy-manager'
    # ... run the sweep ...
    ssh root@<ha-host> 'ha addons start 8d023bea_energy-manager'

Pin the home battery too, so it neither sources nor sinks during the sweep:
set `number.battery_maximum_discharging_power` and
`number.battery_maximum_charging_power` to 0, and restore them afterwards.

Drives the live wallbox, so it is an operator tool rather than part of the test
suite. Reads HA_URL and HA_TOKEN from the environment (never from the file — see
Harness/project/build-and-release.md). In the devcontainer they are already
exported; elsewhere source the secrets file first.

Usage: python3 tools/wallbox_calibration_sweep.py [first_amp] [last_amp]
"""

import json
import os
import statistics
import sys
import time
import urllib.request

HA_URL = os.environ["HA_URL"]
HA_TOKEN = os.environ["HA_TOKEN"]

CURRENT_LIMIT_ENTITY = "number.wallbox_current_limit"
WALLBOX_POWER_ENTITY = "sensor.wallbox_power"      # OCPP MeterValues, corrected
WALLBOX_STATUS_ENTITY = "sensor.wallbox_status"
WALLBOX_PHASES_ENTITY = "sensor.wallbox_phases"
CAR_READY_ENTITY = "binary_sensor.car_ready"

# The balance. Signs follow the entities: grid + = export, battery + = charging.
PV_ENTITY = "sensor.solar_pv_total_ac_power"
GRID_ENTITY = "sensor.grid_power"                  # EBL M-Bus via gPlug
BATTERY_ENTITY = "sensor.battery_charge_discharge_power"
HOUSE_ENTITY = "sensor.house_load_power"           # Shelly 3EM, wallbox excluded
DTSU_ENTITY = "sensor.power_meter_active_power"    # what the inverter is told

# The wallbox reports MeterValues about once a minute, so a step needs a long
# settle before its reading reflects the new limit.
SETTLE_TIME_S = 75
STATUS_WAIT_TRIES = 6
STATUS_WAIT_S = 5
SAMPLE_COUNT = 6
SAMPLE_INTERVAL_S = 10

# A sample is only trusted when the terms outside our control hold still across
# it. Anything noisier is dropped rather than averaged in.
MAX_PV_DRIFT_W = 150
MAX_HOUSE_DRIFT_W = 150
MIN_GOOD_SAMPLES = 3
# The neighbour test only catches a load switching *during* the window. A load
# that stays on across several samples looks locally stable, so the spread of
# the derived draw is gated too — that is what a kettle or a coffee maker
# running through part of a step looks like.
MAX_STEP_SD_W = 150


def ha_get(entity_id: str) -> str:
    """Get entity state from HA."""
    url = f"{HA_URL}/api/states/{entity_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {HA_TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["state"]


def ha_get_float(entity_id: str) -> float:
    """Get an entity state as a float, or raise if it is not numeric."""
    raw = ha_get(entity_id)
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{entity_id} is '{raw}', not a number") from exc


def ha_set_state(entity_id: str, value: str) -> None:
    """Set entity state via direct POST (preserves attributes)."""
    url = f"{HA_URL}/api/states/{entity_id}"
    current = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"Authorization": f"Bearer {HA_TOKEN}"}),
        timeout=10,
    ).read())
    payload = json.dumps({
        "state": value, "attributes": current.get("attributes", {}),
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def read_sample() -> dict:
    """One simultaneous read of every meter, plus the derived true draw."""
    pv = ha_get_float(PV_ENTITY)
    grid = ha_get_float(GRID_ENTITY)
    batt = ha_get_float(BATTERY_ENTITY)
    house = ha_get_float(HOUSE_ENTITY)
    return {
        "pv_w": pv,
        "grid_w": grid,
        "battery_w": batt,
        "house_w": house,
        "dtsu_w": ha_get_float(DTSU_ENTITY),
        "wallbox_w": ha_get_float(WALLBOX_POWER_ENTITY),
        "true_ev_w": (pv - grid - batt) - house,
    }


def collect(n: int, interval: float) -> tuple[list[dict], int]:
    """Take n samples, keeping only those whose uncontrolled terms held still.

    Returns (kept, rejected). A sample is judged against its neighbours, so the
    first and last are always kept — they have only one neighbour to compare to.
    """
    raw = []
    for i in range(n):
        raw.append(read_sample())
        if i < n - 1:
            time.sleep(interval)
    kept, rejected = [], 0
    for i, s in enumerate(raw):
        nb = [raw[j] for j in (i - 1, i + 1) if 0 <= j < len(raw)]
        drift_pv = max((abs(x["pv_w"] - s["pv_w"]) for x in nb), default=0.0)
        drift_h = max((abs(x["house_w"] - s["house_w"]) for x in nb), default=0.0)
        if drift_pv > MAX_PV_DRIFT_W or drift_h > MAX_HOUSE_DRIFT_W:
            rejected += 1
            continue
        kept.append(s)
    return kept, rejected


def mean(samples: list[dict], key: str) -> float:
    """Mean of one field across samples."""
    return statistics.fmean(s[key] for s in samples)


def restore(limit: str = "0") -> None:
    """Return the wallbox to a known-safe commanded current."""
    try:
        ha_set_state(CURRENT_LIMIT_ENTITY, limit)
        print(f"\nRestored {CURRENT_LIMIT_ENTITY} = {limit} A")
    except Exception as exc:                                   # noqa: BLE001
        print(f"\n!! COULD NOT RESTORE the current limit: {exc}")
        print(f"!! Set {CURRENT_LIMIT_ENTITY} to 0 by hand.")


def preflight() -> int:
    """Check the sweep can run, and return the connected phase count."""
    print("Pre-flight:")
    ready = ha_get(CAR_READY_ENTITY)
    status = ha_get(WALLBOX_STATUS_ENTITY)
    phases = int(ha_get_float(WALLBOX_PHASES_ENTITY))
    pv = ha_get_float(PV_ENTITY)
    batt = ha_get_float(BATTERY_ENTITY)
    print(f"  car_ready       {ready}")
    print(f"  wallbox status  {status}")
    print(f"  phases          {phases}")
    print(f"  PV              {pv:.0f} W")
    print(f"  battery         {batt:+.0f} W")
    if ready != "on":
        raise SystemExit("car_ready is not 'on' — plug the car in first.")
    if abs(pv) > 300:
        print("  NOTE: PV is running. Night runs are cleaner; watch the sd column.")
    if abs(batt) > 100:
        print("  NOTE: the battery is moving. Pin both battery power limits to 0.")
    return phases


def main() -> None:
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    phases = preflight()

    print(f"\nSweeping {first}-{last} A on {phases} phase(s). Ctrl-C restores 0 A.")
    print("Pausing for an idle reference...")
    ha_set_state(CURRENT_LIMIT_ENTITY, "0")
    time.sleep(30)
    idle, idle_rej = collect(SAMPLE_COUNT, SAMPLE_INTERVAL_S)
    if len(idle) < MIN_GOOD_SAMPLES:
        raise SystemExit("Could not get a steady idle reference — too much drift.")
    idle_resid = mean(idle, "true_ev_w")
    print(f"  idle residual {idle_resid:+.0f} W over {len(idle)} samples "
          f"({idle_rej} rejected) — this is the balance's own error, "
          f"subtracted from every step.")

    results = []
    print(f"\n{'A':>3} | {'true W':>7} {'sd':>4} {'n':>3} | {'meter W':>8} "
          f"| {'W/A':>6} | {'meter err':>9}")
    print("-" * 62)
    for amps in range(first, last + 1):
        ha_set_state(CURRENT_LIMIT_ENTITY, str(amps))
        print(f"  {amps:>2} A settling", end="", flush=True)
        time.sleep(SETTLE_TIME_S)
        status = ha_get(WALLBOX_STATUS_ENTITY)
        for _ in range(STATUS_WAIT_TRIES):
            if status == "Charging":
                break
            print(".", end="", flush=True)
            time.sleep(STATUS_WAIT_S)
            status = ha_get(WALLBOX_STATUS_ENTITY)
        print(f" {status}")
        if status != "Charging":
            print(f"      skipped — wallbox is {status}, not Charging")
            continue

        kept, rejected = collect(SAMPLE_COUNT, SAMPLE_INTERVAL_S)
        if len(kept) < MIN_GOOD_SAMPLES:
            print(f"      skipped — only {len(kept)} steady samples")
            continue
        true_ev = mean(kept, "true_ev_w") - idle_resid
        sd = statistics.pstdev([s["true_ev_w"] for s in kept]) if len(kept) > 1 else 0.0
        if sd > MAX_STEP_SD_W:
            print(f"      skipped — spread too wide (sd {sd:.0f} W > {MAX_STEP_SD_W} W); "
                  f"something else on the house was switching. Re-run this amp.")
            continue
        meter = mean(kept, "wallbox_w")
        results.append({
            "amps": amps, "true_w": true_ev, "sd": sd, "n": len(kept),
            "rejected": rejected, "meter_w": meter,
            "dtsu_w": mean(kept, "dtsu_w"),
        })
        print(f"{amps:>3} | {true_ev:>7.0f} {sd:>4.0f} {len(kept):>3} | {meter:>8.0f} "
              f"| {true_ev / amps:>6.1f} | {meter - true_ev:>+9.0f}")

    restore("0")

    if not results:
        print("\nNo usable steps. Nothing to fit.")
        return

    print(f"\n=== RESULT, {phases} phase(s) ===")
    print(f"{'A':>3} | {'true W':>7} {'±sd':>5} | {'W/A':>6} | {'meter W':>8} "
          f"| {'meter/true':>10}")
    print("-" * 60)
    for r in results:
        print(f"{r['amps']:>3} | {r['true_w']:>7.0f} {r['sd']:>5.0f} "
              f"| {r['true_w'] / r['amps']:>6.1f} | {r['meter_w']:>8.0f} "
              f"| {r['meter_w'] / r['true_w']:>10.3f}")

    # Step-to-step increment is the sharpest contamination check: one more amp
    # must add one amp's worth of power. If the increments scatter, the balance
    # is not cancelling a moving house load and the absolute figures are junk —
    # on 2026-10-07 a morning run showed +150 W per amp instead of ~230 because
    # the house was swinging over a 2 kW range.
    print("\nPer-amp increments (each should be about one amp's worth):")
    bad = 0
    suspect: set[int] = set()
    for prev, cur in zip(results, results[1:], strict=False):
        if cur["amps"] - prev["amps"] != 1:
            continue
        incr = cur["true_w"] - prev["true_w"]
        rough = cur["true_w"] / cur["amps"]
        off = abs(incr - rough) / rough
        flag = "  <-- off" if off > 0.25 else ""
        if off > 0.25:
            bad += 1
            # Either end of a bad increment may be the culprit.
            suspect.update((prev["amps"], cur["amps"]))
        print(f"  {prev['amps']:>2} -> {cur['amps']:<2} A  {incr:>+7.0f} W{flag}")
    if bad:
        print(f"\n  {bad} increment(s) off by more than 25 %. Suspect steps: "
              f"{sorted(suspect)} A. Repeat with the house quiet (at night).")

    # Only steps whose increment is sane may feed the fit. A step can have a
    # tiny sd and still be wrong: on 2026-10-07 the 16 A step read 3228 W with
    # sd 15 W, below the 15 A step — stable but systematically off.
    trusted = [r for r in results if r["amps"] not in suspect]
    if len(trusted) < 4 or bad > len(results) / 3:
        print(f"\nNOT FITTING: only {len(trusted)} of {len(results)} steps are "
              f"trustworthy and {bad} increment(s) are off. Repeat with the house "
              f"quiet — do not change any constant from this run.")
        return

    # WATTS_PER_AMP: least-squares through the origin, since 0 A draws 0 W.
    wpa = (sum(r["amps"] * r["true_w"] for r in trusted)
           / sum(r["amps"] ** 2 for r in trusted))
    # METER_SCALE_1P / METER_SCALE: the gain that maps the meter onto the truth.
    gain = (sum(r["meter_w"] * r["true_w"] for r in trusted)
            / sum(r["meter_w"] ** 2 for r in trusted))
    worst = max(abs(r["true_w"] - wpa * r["amps"]) for r in trusted)
    print(f"\nFitted from {len(trusted)} trusted step(s): "
          f"{', '.join(str(r['amps']) for r in trusted)} A")
    print(f"WATTS_PER_AMP[{phases}]  = {wpa:.1f}   "
          f"(fit through origin, worst step off by {worst:.0f} W)")
    print(f"meter residual gain  = {gain:.4f}  (true = this x sensor.wallbox_power)")
    print("\nNOTE: sensor.wallbox_power ALREADY carries the deployed correction "
          "(METER_SCALE / METER_SCALE_1P), so the gain above is a *residual*: "
          "multiply the deployed constant by it, do not replace it.")
    print("WATTS_PER_AMP is absolute and does replace the deployed value.")
    print("Record the method and the date in ocpp-server FSD 7.1 and 7.2.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        restore("0")
    except SystemExit:
        restore("0")
        raise
    except Exception:
        restore("0")
        raise
