#!/usr/bin/env python3
"""Measure what each commanded amp actually draws.

Commands each amp level in turn and measures the resulting draw from the house
meters, so it yields both calibrations the OCPP server needs:

- `WATTS_PER_AMP` — watts delivered per commanded amp.
- `METER_SCALE` / `METER_SCALE_1P` — how far the wallbox's own meter is out.

The true draw comes from an energy balance over meters that do not involve the
wallbox, so it is independent of the figure being checked:

    car = (-grid_power) - dtsu_raw

Only the EBL meter sees the car; the DTSU does not. Everything else — house load,
PV and the battery — is common to both and cancels exactly, so none of them has to
be measured and none can disturb the result. That is why a daylight run is valid
for this quantity even though the four-term balance it replaced was not.

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
# The proxy publishes the DTSU's RAW reading as an attribute, before injection
# (`sun2000 = dtsu + wallbox` confirms it). That raw value is what the sweep needs.
PROXY_ENTITY = "sensor.modbus_proxy_correction"

# Optional: EBL per-phase currents, if exposed as entities. Present -> the sweep
# also checks the commanded amps against the measured current on the car's phase,
# which needs no voltage and no watts-per-amp assumption.
GRID_PHASE_CURRENT_ENTITIES = (
    "sensor.grid_phase_1_current",
    "sensor.grid_phase_2_current",
    "sensor.grid_phase_3_current",
)
# The gPlug reports I1/I2/I3 in units of ten amps. Confirmed two ways on
# 2026-10-08: idle I1 = 0.11 is 1.1 A, matching ~250 W on that phase at night;
# and at 16 A the power cross-check gives 3548 W / 232 V / 1.50 = 10.2.
GRID_PHASE_CURRENT_SCALE = 10.0

# Pacing. Do NOT block on the EBL meter "reporting": Home Assistant exposes no
# usable signal for that. Measured 2026-10-10, sensor.grid_power sat at
# -11245.0 W with BOTH last_changed and last_reported frozen for 138 s while the
# meter was publishing every ~16 s — last_reported advances only when the value
# changes, so it cannot distinguish a quiet meter from a steady one. Two nights
# were lost waiting for a report that never came.
#
# The fix is to stop needing one. If the EBL value is constant, reading it again
# is not stale data — the value simply is the value. Staleness only matters
# across a change, and a change always shows up. So: sample on a fixed cadence,
# and report how many distinct EBL values and wallbox reports a step actually
# saw, so thin evidence is visible rather than assumed away.
POLL_INTERVAL_S = 1.0
SAMPLE_EVERY_S = 20.0         # a little over the EBL's ~16 s publish interval
SAMPLES_PER_STEP = 20         # ~400 s, so 6-7 wallbox reports at its 60 s cadence
SETTLE_S = 170.0              # ~3 wallbox reports at the new level before counting
IDLE_SETTLE_S = 190.0         # longer: the car may have been charging until now
STATUS_WAIT_TRIES = 6
STATUS_WAIT_S = 5

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


def ha_get_state(entity_id: str) -> dict:
    """Get the full state object (state, attributes, timestamps)."""
    url = f"{HA_URL}/api/states/{entity_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {HA_TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def ha_get(entity_id: str) -> str:
    """Get entity state from HA."""
    return ha_get_state(entity_id)["state"]


def ha_get_attrs(entity_id: str) -> dict:
    """Get an entity's attributes."""
    return ha_get_state(entity_id)["attributes"]


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


def read_fast() -> dict:
    """Read the fast meters, as close together in time as possible."""
    p = ha_get_attrs(PROXY_ENTITY)
    wb = ha_get_state(WALLBOX_POWER_ENTITY)
    return {
        "dtsu_raw_w": float(p["dtsu"]),
        "injected_w": float(p["wallbox"]),
        "pv_w": ha_get_float(PV_ENTITY),
        "battery_w": ha_get_float(BATTERY_ENTITY),
        "house_w": ha_get_float(HOUSE_ENTITY),
        "wallbox_w": float(wb["state"]),
        # When the wallbox last told us anything. `last_reported` advances on
        # every write even if the value repeats; `last_changed` does not, and a
        # steady charge reports the same number again and again.
        "wallbox_stamp": wb.get("last_reported") or wb["last_updated"],
    }


def read_grid() -> tuple[float, str, list[float]]:
    """Return EBL total power, its report timestamp, and per-phase currents if any.

    The timestamp must be `last_reported`, which advances on every write. A steady
    load makes the meter publish the same rounded watt value repeatedly — on
    2026-10-09 it sent -1668 W five times running during the 6 A step — and
    `last_changed` does not move for those, so pacing on it stalls and looks
    exactly like a dead meter.
    """
    d = ha_get_state(GRID_ENTITY)
    phases = []
    for e in GRID_PHASE_CURRENT_ENTITIES:
        try:
            phases.append(float(ha_get_state(e)["state"]))
        except Exception:                                       # noqa: BLE001
            phases = []
            break
    return float(d["state"]), d.get("last_reported") or d["last_changed"], phases


def collect(n: int) -> tuple[list[dict], int]:
    """Take n samples on a fixed cadence, averaging the fast meters across each.

    Returns (samples, ebl_updates) where ebl_updates is how many distinct EBL
    values were seen. A step where that is 1 is not wrong — a perfectly steady
    load genuinely reads the same — but it means the EBL side contributed one
    independent number, so it is reported rather than hidden.
    """
    samples: list[dict] = []
    ebl_values: list[float] = []
    wb_stamps: set[str] = set()
    pending: list[dict] = []
    next_sample = time.monotonic() + SAMPLE_EVERY_S
    while len(samples) < n:
        fast = read_fast()
        pending.append(fast)
        wb_stamps.add(fast["wallbox_stamp"])
        if time.monotonic() >= next_sample:
            grid_w, _stamp, phase_a = read_grid()
            ebl_values.append(grid_w)
            numeric = [k for k, v in pending[0].items() if isinstance(v, float)]
            avg = {k: statistics.fmean(x[k] for x in pending) for k in numeric}
            avg["grid_w"] = grid_w
            avg["phase_a_list"] = phase_a
            # Both meters describe the same interval: the EBL value read now, and
            # the fast meters averaged over the interval leading up to it.
            #
            # The two meters use OPPOSITE conventions: grid_power is
            # negative-for-import, dtsu is positive-for-import. So the car — which
            # only the EBL sees — is (-grid) - dtsu. Verified live 2026-10-10 on a
            # 3-phase charge: -(-11245) - 471 = 10774 W against the independent
            # four-term balance's 10791 W, agreeing within 17 W.
            #
            # Getting this wrong returned car + 2*house instead of car, and the
            # idle reference then measured 2*house rather than nothing. That is
            # what the unexplained -479 W and +396 W "meter offsets" were, and why
            # they moved between nights: the house load differed.
            avg["car_w"] = -grid_w - avg["dtsu_raw_w"]
            avg["balance_w"] = (avg["pv_w"] - grid_w - avg["battery_w"]) - avg["house_w"]
            avg["wb_reports"] = len(wb_stamps)
            samples.append(avg)
            pending = []
            next_sample = time.monotonic() + SAMPLE_EVERY_S
        time.sleep(POLL_INTERVAL_S)
    return samples, len({round(v) for v in ebl_values})


def settle(seconds: float) -> None:
    """Wait for the wallbox and the meters to reflect a change, with progress."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(10)
        print(".", end="", flush=True)


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
        print("  NOTE: PV is running. Two consequences, both argue for a night run:")
        print("        - the power subtraction gets noisier (watch the sd column);")
        print("        - the per-phase currents are NET magnitudes, so PV on the")
        print("          car's phase subtracts from them and the amp check can fold.")
    if abs(batt) > 100:
        print("  NOTE: the battery is moving. Pin both battery power limits to 0.")
    return phases


def main() -> None:
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    phases = preflight()

    print(f"\nSweeping {first}-{last} A on {phases} phase(s). Ctrl-C restores 0 A.")
    print("Idle reference (waiting for the car to actually stop)", end="", flush=True)
    ha_set_state(CURRENT_LIMIT_ENTITY, "0")
    settle(IDLE_SETTLE_S)
    idle, idle_ebl_updates = collect(SAMPLES_PER_STEP)
    idle_car = mean(idle, "car_w")
    idle_sd = statistics.pstdev([x["car_w"] for x in idle])
    idle_phase: list[float] | None = None
    has_phase = bool(idle[0]["phase_a_list"])
    if has_phase:
        n_ph = len(idle[0]["phase_a_list"])
        idle_phase = [statistics.fmean(x["phase_a_list"][i] for x in idle)
                      for i in range(n_ph)]
    print(f"\n  EBL-DTSU with the car idle: {idle_car:+.0f} W (sd {idle_sd:.0f}) — "
          f"the two meters' relative offset, subtracted from every step.")
    if has_phase:
        print(f"  idle phase currents: "
              f"{', '.join(f'{v * GRID_PHASE_CURRENT_SCALE:.2f} A' for v in idle_phase)}")
    else:
        print("  per-phase currents not exposed — the amp cross-check is off.")

    results = []
    hdr = f"\n{'A':>3} | {'car W':>7} {'sd':>4} {'n':>2} | {'W/A':>6} | {'meter W':>8}"
    print(hdr + (" | phase ΔA" if has_phase else ""))
    print("-" * (len(hdr) + (12 if has_phase else 0)))
    for amps in range(first, last + 1):
        ha_set_state(CURRENT_LIMIT_ENTITY, str(amps))
        print(f"  {amps:>2} A settling", end="", flush=True)
        settle(SETTLE_S)
        status = ha_get(WALLBOX_STATUS_ENTITY)
        for _ in range(STATUS_WAIT_TRIES):
            if status == "Charging":
                break
            time.sleep(STATUS_WAIT_S)
            status = ha_get(WALLBOX_STATUS_ENTITY)
        print(f" {status}")
        if status != "Charging":
            print(f"      skipped — wallbox is {status}, not Charging")
            continue

        samples, ebl_updates = collect(SAMPLES_PER_STEP)
        car = mean(samples, "car_w") - idle_car
        sd = statistics.pstdev([x["car_w"] for x in samples])
        if sd > MAX_STEP_SD_W:
            print(f"      skipped — spread too wide (sd {sd:.0f} W > {MAX_STEP_SD_W} W); "
                  f"something else was switching. Re-run this amp.")
            continue
        sem = sd / len(samples) ** 0.5
        row = {
            "amps": amps, "true_w": car, "sd": sd, "n": len(samples),
            "sem": sem, "wb_reports": samples[-1].get("wb_reports", 0),
            "ebl_updates": ebl_updates,
            "meter_w": mean(samples, "wallbox_w"),
            "balance_w": mean(samples, "balance_w"),
        }
        extra = ""
        if has_phase:
            now = [statistics.fmean(x["phase_a_list"][i] for x in samples)
                   for i in range(len(idle_phase))]
            deltas = [(n - b) * GRID_PHASE_CURRENT_SCALE
                      for n, b in zip(now, idle_phase, strict=False)]
            row["phase_delta_a"] = deltas
            # The car sits on one phase, so exactly one delta should track the
            # commanded amps. This needs no voltage and no watts-per-amp guess,
            # and it is by far the quietest signal available: measured idle, the
            # per-phase currents held to sd 0.004-0.009 A (about 2 W equivalent)
            # where the power subtraction managed sd 47 W.
            extra = " | " + " ".join(f"{d:+5.1f}" for d in deltas)
        results.append(row)
        print(f"{amps:>3} | {car:>7.0f} {sd:>4.0f} {len(samples):>2} "
              f"| {car / amps:>6.1f} | {row['meter_w']:>8.0f}{extra}"
              f"   (±{sem:.0f} W, {row['wb_reports']} wb reports, "
              f"{ebl_updates} EBL values)")

    # Re-measure the idle offset now the sweep is over. It is subtracted from every
    # step, so if it has moved the whole run is suspect — and comparing the two is
    # the only way to tell a genuine meter offset from a car that had not finished
    # winding down when the first reference was taken.
    print("\nIdle reference again", end="", flush=True)
    ha_set_state(CURRENT_LIMIT_ENTITY, "0")
    settle(IDLE_SETTLE_S)
    idle2, _ = collect(SAMPLES_PER_STEP)
    idle2_car = mean(idle2, "car_w")
    drift = idle2_car - idle_car
    print(f"\n  start {idle_car:+.0f} W, end {idle2_car:+.0f} W, drift {drift:+.0f} W")
    if abs(drift) > 50:
        print("  WARNING: the idle offset moved by more than 50 W. Every step is "
              "corrected by\n  it, so treat the absolute figures below as suspect "
              "— the ratios are still good.")
    else:
        print("  The offset held, so it is a real difference between the two meters "
              "and not\n  a car still winding down.")

    restore("0")

    if not results:
        print("\nNo usable steps. Nothing to fit.")
        return

    print(f"\n=== RESULT, {phases} phase(s) ===")
    if has_phase:
        # Name the car's phase from whichever delta actually tracked the command.
        tot = [sum(abs(r["phase_delta_a"][i]) for r in results)
               for i in range(len(idle_phase))]
        car_phase = tot.index(max(tot)) + 1
        print(f"The car draws on EBL phase {car_phase} "
              f"(largest current response across the sweep).")
        print("This amp table is the most precise result here — prefer it over the "
              "watt table below when they disagree.")
        print(f"\n{'A':>3} | {'commanded':>9} | {'measured ΔA':>11} | {'diff':>6}")
        print("-" * 40)
        for r in results:
            got = r["phase_delta_a"][car_phase - 1]
            print(f"{r['amps']:>3} | {r['amps']:>9} | {got:>11.2f} | {got - r['amps']:>+6.2f}")

    # This is the result that matters: sensor.wallbox_power feeds the Modbus-proxy
    # correction, so it has to match the real power. Everything else is support.
    print("\nDoes sensor.wallbox_power match the real power? (the control-loop signal)")
    print(f"\n{'A':>3} | {'real W':>7} {'±sem':>5} | {'reported W':>10} "
          f"| {'error W':>8} {'error %':>8} | {'wb reps':>8} {'ebl':>4} | {'balance W':>9}")
    print("-" * 86)
    for r in results:
        err = r["meter_w"] - r["true_w"]
        print(f"{r['amps']:>3} | {r['true_w']:>7.0f} {r['sem']:>5.0f} "
              f"| {r['meter_w']:>10.0f} | {err:>+8.0f} {err / r['true_w'] * 100:>+7.1f}% "
              f"| {r['wb_reports']:>8} {r['ebl_updates']:>4} | {r['balance_w']:>9.0f}")
    print("  real W = dtsu_raw - grid (the car, since only the EBL sees it)")
    print("  balance W = independent PV-grid-battery-house cross-check")

    ratios = [r["meter_w"] / r["true_w"] for r in results]
    print(f"\n  reported/real ranges {min(ratios):.3f}-{max(ratios):.3f}. A flat ratio means "
          f"the idle\n  offset is right; a wrong one diverges at low power.")

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
    print(f"meter residual gain  = {gain:.4f}  (real = this x sensor.wallbox_power)")
    print("\nsensor.wallbox_power ALREADY carries the deployed correction, so that gain "
          "is a\nRESIDUAL: multiply the deployed METER_SCALE / METER_SCALE_1P by it.")
    print(f"  e.g. deployed 1.050 x {gain:.4f} = {1.050 * gain:.4f}")
    print("A residual near 1.000 means the signal already matches the real power.")
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
