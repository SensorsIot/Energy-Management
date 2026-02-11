#!/usr/bin/env python3
"""
Calibration verification sweep: set wallbox power 5000–11000W via HA,
wait for settling, read EBL grid meter and Huawei DTSU to verify.

Usage: source ~/.secrets/env && python3 calibration_sweep.py
"""

import json
import os
import sys
import time
import urllib.request

HA_URL = os.environ["HA_URL"]
HA_TOKEN = os.environ["HA_TOKEN"]

POWER_LIMIT_ENTITY = "number.wallbox_power_limit"
GRID_METER_ENTITY = "sensor.grid_power"          # EBL M-Bus via gPlug
DTSU_METER_ENTITY = "sensor.power_meter_active_power"  # Huawei DTSU
WALLBOX_POWER_ENTITY = "sensor.wallbox_power"     # Wallbox OCPP MeterValues
WALLBOX_STATUS_ENTITY = "sensor.wallbox_status"

SETTLE_TIME_S = 15   # seconds to wait after setting power
SAMPLE_COUNT = 3     # number of readings to average
SAMPLE_INTERVAL_S = 5


def ha_get(entity_id: str) -> str:
    """Get entity state from HA."""
    url = f"{HA_URL}/api/states/{entity_id}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {HA_TOKEN}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data["state"]


def ha_set_number(entity_id: str, value: float):
    """Set a number entity in HA."""
    url = f"{HA_URL}/api/services/number/set_value"
    payload = json.dumps({"entity_id": entity_id, "value": value}).encode()
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


def main():
    # Read baseline (before charging)
    print("Reading baseline (no charging)...")
    baseline = average_readings(SAMPLE_COUNT, SAMPLE_INTERVAL_S)
    print(f"  Baseline: grid={baseline['grid_w']:.0f}W, dtsu={baseline['dtsu_w']:.0f}W, wb={baseline['wallbox_w']:.0f}W")
    print()

    steps = list(range(5000, 12000, 1000))  # 5000, 6000, ..., 11000
    results = []

    print(f"{'Req W':>7} | {'WB W':>7} | {'Grid W':>8} | {'DTSU W':>8} | {'Grid-Base':>10} | {'DTSU-Base':>10} | {'Diff(Grid)':>10}")
    print("-" * 80)

    for target_w in steps:
        # Set power limit
        ha_set_number(POWER_LIMIT_ENTITY, target_w)
        status = ha_get(WALLBOX_STATUS_ENTITY)
        print(f"  Set {target_w}W, status={status}, settling {SETTLE_TIME_S}s...", end="", flush=True)
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
        diff = grid_delta - avg["wallbox_w"]

        results.append({
            "target_w": target_w,
            "wallbox_w": avg["wallbox_w"],
            "grid_w": avg["grid_w"],
            "dtsu_w": avg["dtsu_w"],
            "grid_delta_w": grid_delta,
            "dtsu_delta_w": dtsu_delta,
        })

        print(f"{target_w:>7} | {avg['wallbox_w']:>7.0f} | {avg['grid_w']:>8.0f} | {avg['dtsu_w']:>8.0f} | {grid_delta:>10.0f} | {dtsu_delta:>10.0f} | {diff:>+10.0f}")

    # Stop charging
    print("\nStopping: setting power limit to 0W...")
    ha_set_number(POWER_LIMIT_ENTITY, 0)
    time.sleep(5)
    status = ha_get(WALLBOX_STATUS_ENTITY)
    print(f"Final status: {status}")

    # Summary
    print("\n=== CALIBRATION VERIFICATION SUMMARY ===")
    print(f"{'Req W':>7} | {'WB W':>7} | {'Grid Delta':>10} | {'DTSU Delta':>10} | {'Req-Grid':>8} | {'Req-DTSU':>8}")
    print("-" * 70)
    for r in results:
        req_vs_grid = r["target_w"] - r["grid_delta_w"]
        req_vs_dtsu = r["target_w"] - r["dtsu_delta_w"]
        print(f"{r['target_w']:>7} | {r['wallbox_w']:>7.0f} | {r['grid_delta_w']:>10.0f} | {r['dtsu_delta_w']:>10.0f} | {req_vs_grid:>+8.0f} | {req_vs_dtsu:>+8.0f}")


if __name__ == "__main__":
    main()
