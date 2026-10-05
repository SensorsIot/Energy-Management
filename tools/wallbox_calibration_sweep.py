#!/usr/bin/env python3
"""Measure what each commanded amp actually draws.

Commands each amp level via HA, waits for settling, then reads the EBL grid
meter and the Huawei DTSU to see what that amp delivered.

This is how WATTS_PER_AMP (ocpp-server/src/ocpp_handler.py) is measured. The wallbox is
commanded in amps, so the sweep walks amps and reports W/A per step.

Drives the live wallbox and the house meters, so it is an operator tool rather
than part of the test suite.

Reads HA_URL and HA_TOKEN from the environment (never from the file — see
Harness/project/build-and-release.md). In the devcontainer they are already
exported; elsewhere source the secrets file first.

Usage: python3 tools/wallbox_calibration_sweep.py
"""

import json
import os
import time
import urllib.request

HA_URL = os.environ["HA_URL"]
HA_TOKEN = os.environ["HA_TOKEN"]

CURRENT_LIMIT_ENTITY = "number.wallbox_current_limit"
GRID_METER_ENTITY = "sensor.grid_power"          # EBL M-Bus via gPlug
DTSU_METER_ENTITY = "sensor.power_meter_active_power"  # Huawei DTSU
WALLBOX_POWER_ENTITY = "sensor.wallbox_power"     # Wallbox OCPP MeterValues
WALLBOX_STATUS_ENTITY = "sensor.wallbox_status"

# Wallbox MeterValues arrive every 60s — need long settle time
SETTLE_TIME_S = 75   # seconds to wait after setting power
SAMPLE_COUNT = 3     # number of readings to average
SAMPLE_INTERVAL_S = 10


def ha_get(entity_id: str) -> str:
    """Get entity state from HA."""
    url = f"{HA_URL}/api/states/{entity_id}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {HA_TOKEN}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data["state"]


def ha_set_state(entity_id: str, value: str) -> None:
    """Set entity state via direct POST (preserves attributes)."""
    url = f"{HA_URL}/api/states/{entity_id}"
    current = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"Authorization": f"Bearer {HA_TOKEN}"}),
        timeout=10,
    ).read())
    attrs = current.get("attributes", {})
    payload = json.dumps({"state": value, "attributes": attrs}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def read_meters() -> dict:
    """Read all three power measurements."""
    grid = float(ha_get(GRID_METER_ENTITY))
    dtsu = float(ha_get(DTSU_METER_ENTITY))
    wb = float(ha_get(WALLBOX_POWER_ENTITY))
    return {"grid_w": grid, "dtsu_w": dtsu, "wallbox_w": wb}


def average_readings(n: int, interval: float) -> dict:
    """Take n readings and average them."""
    readings = []
    for i in range(n):
        readings.append(read_meters())
        if i < n - 1:
            time.sleep(interval)
    avg = {}
    for key in readings[0]:
        avg[key] = sum(r[key] for r in readings) / len(readings)
    return avg


def main() -> None:
    # First ensure wallbox is paused for clean baseline
    print("Pausing wallbox for baseline measurement...")
    ha_set_state(CURRENT_LIMIT_ENTITY, "0")
    time.sleep(10)

    status = ha_get(WALLBOX_STATUS_ENTITY)
    print(f"  Wallbox status: {status}")
    if status not in ("SuspendedEVSE", "Charging", "Preparing"):
        print(f"  WARNING: unexpected status {status}")

    # Read baseline with no charging
    print("Reading baseline (no charging, waiting 20s)...")
    time.sleep(20)
    baseline = average_readings(SAMPLE_COUNT, SAMPLE_INTERVAL_S)
    print(
        f"  Baseline: grid={baseline['grid_w']:.0f}W, "
        f"dtsu={baseline['dtsu_w']:.0f}W, wb={baseline['wallbox_w']:.0f}W"
    )
    print()

    steps = list(range(6, 17))  # 6, 7, ..., 16 A
    results = []

    print(
        f"{'Req A':>7} | {'WB W':>7} | {'Grid W':>8} | {'DTSU W':>8} "
        f"| {'Grid-Base':>10} | {'DTSU-Base':>10}"
    )
    print("-" * 70)

    for target_a in steps:
        # Set current limit
        ha_set_state(CURRENT_LIMIT_ENTITY, str(int(target_a)))
        status = ha_get(WALLBOX_STATUS_ENTITY)
        print(
            f"  Set {target_a}A, status={status}, settling {SETTLE_TIME_S}s...",
            end="", flush=True,
        )
        time.sleep(SETTLE_TIME_S)

        # Wait for Charging status (up to 30s extra)
        for _ in range(6):
            status = ha_get(WALLBOX_STATUS_ENTITY)
            if status == "Charging":
                break
            print(".", end="", flush=True)
            time.sleep(5)
        print(f" status={status}")

        # Average readings
        avg = average_readings(SAMPLE_COUNT, SAMPLE_INTERVAL_S)
        grid_delta = avg["grid_w"] - baseline["grid_w"]
        dtsu_delta = avg["dtsu_w"] - baseline["dtsu_w"]

        results.append({
            "target_a": target_a,
            "wallbox_w": avg["wallbox_w"],
            "grid_w": avg["grid_w"],
            "dtsu_w": avg["dtsu_w"],
            "grid_delta_w": grid_delta,
            "dtsu_delta_w": dtsu_delta,
        })

        print(
            f"{target_a:>7} | {avg['wallbox_w']:>7.0f} | {avg['grid_w']:>8.0f} "
            f"| {avg['dtsu_w']:>8.0f} | {grid_delta:>10.0f} | {dtsu_delta:>10.0f}"
        )

    # Stop charging
    print("\nStopping: setting current limit to 0A...")
    ha_set_state(CURRENT_LIMIT_ENTITY, "0")
    time.sleep(5)
    status = ha_get(WALLBOX_STATUS_ENTITY)
    print(f"Final status: {status}")

    # Summary — W/A per step is the figure WATTS_PER_AMP is set from.
    print("\n=== WATTS PER AMP ===")
    print(
        f"{'Req A':>7} | {'WB W':>7} | {'Grid Delta':>10} | {'DTSU Delta':>10} "
        f"| {'W/A grid':>8} | {'W/A wb':>8}"
    )
    print("-" * 70)
    for r in results:
        a = r["target_a"]
        wpa_grid = r["grid_delta_w"] / a if a else 0.0
        wpa_wb = r["wallbox_w"] / a if a else 0.0
        print(
            f"{a:>7} | {r['wallbox_w']:>7.0f} | {r['grid_delta_w']:>10.0f} "
            f"| {r['dtsu_delta_w']:>10.0f} | {wpa_grid:>8.1f} | {wpa_wb:>8.1f}"
        )
    usable = [r for r in results if r["target_a"] >= 8]
    if usable:
        mean_wpa = sum(r["grid_delta_w"] / r["target_a"] for r in usable) / len(usable)
        print(f"\nMean W/A over 8-16 A (grid-measured): {mean_wpa:.1f}")


if __name__ == "__main__":
    main()
