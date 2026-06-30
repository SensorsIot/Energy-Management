#!/usr/bin/env python3
"""Grid-correction watchdog.

Alarms (Telegram) when the Huawei DTSU grid meter (corrected by the ESP32 Modbus
proxy with the wallbox power) diverges from the independent M-Bus grid meter
while the wallbox is charging — i.e. the proxy correction has failed and the
inverter is no longer seeing the car load.

Detection is read-only from InfluxDB:
  - `M_Grid`  (HomeData/MBUS)   — the true grid (M-Bus), ~5 s.
  - `power_meter_active_power` (HomeData/Power) — the corrected DTSU the inverter
    reads, ~30 s.  When the correction works these track within a few hundred W;
    when it fails the corrected value misses the wallbox and they diverge by ~the
    wallbox power.
  - `wallbox_power` (HomeAssistant) — gates the check to active charging.

A sustained divergence (>= CONSEC bins over THRESHOLD_W) while charging raises one
alert, then stays quiet for COOLDOWN_S. Brief transients (e.g. the battery
discharge ramp-down export spike on pause) are filtered by the consecutive-bin
requirement.

Alert routing: HA `telegram_bot.send_message` (default; chat configured in HA), or
direct Telegram Bot API if WATCHDOG_TELEGRAM_CHAT_ID + TELEGRAM_BOT_TOKEN are set.

Env: INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, HA_URL, HA_TOKEN
     [TELEGRAM_BOT_TOKEN, WATCHDOG_TELEGRAM_CHAT_ID]
Run: every few minutes via cron, or `--loop`. `--test` sends a test alert.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

# --- tunables (env-overridable) ---------------------------------------------
LOOKBACK_MIN = int(os.environ.get("WD_LOOKBACK_MIN", "6"))
BIN_S = int(os.environ.get("WD_BIN_S", "30"))
MIN_CHARGE_W = float(os.environ.get("WD_MIN_CHARGE_W", "1500"))
THRESHOLD_W = float(os.environ.get("WD_THRESHOLD_W", "2500"))
CONSEC = int(os.environ.get("WD_CONSEC", "3"))  # bins; 3×30 s = 90 s sustained
COOLDOWN_S = int(os.environ.get("WD_COOLDOWN_S", "1800"))
STATE_FILE = Path(os.environ.get("WD_STATE_FILE", str(Path.home() / ".grid_correction_watchdog")))


def _flux(query: str) -> list[list[str]]:
    url = os.environ["INFLUXDB_URL"].rstrip("/") + "/api/v2/query?org=" + os.environ["INFLUXDB_ORG"]
    req = urllib.request.Request(
        url,
        data=query.encode(),
        headers={
            "Authorization": "Token " + os.environ["INFLUXDB_TOKEN"],
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    rows = []
    for line in text.splitlines():
        line = line.strip("\r")
        if line and line[0] == ",":
            rows.append(line.split(","))
    return rows


def _series(measurement_filter: str, bucket: str) -> dict[int, float]:
    """Return {epoch_bin: value} at BIN_S resolution for the lookback window."""
    q = f'''
from(bucket:"{bucket}")
  |> range(start:-{LOOKBACK_MIN}m)
  |> filter(fn:(r)=>{measurement_filter})
  |> aggregateWindow(every:{BIN_S}s, fn:mean, createEmpty:false)
  |> keep(columns:["_time","_value"])
'''
    out = {}
    for row in _flux(q):
        # influx csv: ,result,table,_time,_value
        try:
            t = row[3]
            v = float(row[4])
        except (IndexError, ValueError):
            continue
        # _time like 2026-06-30T19:33:00Z → epoch
        try:
            ep = int(time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            continue
        out[ep - (ep % BIN_S)] = v
    return out


def collect():
    mgrid = _series('r._measurement=="MBUS" and r._field=="M_Grid"', "HomeData")
    meter = _series('r._measurement=="Power" and r._field=="power_meter_active_power"', "HomeData")
    wb = _series('r.entity_id=="wallbox_power" and r._field=="value"', "HomeAssistant")
    return mgrid, meter, wb


def evaluate(mgrid, meter, wb):
    """Return (alarm: bool, detail: dict). Forward-fills the sparse wallbox series."""
    bins = sorted(set(mgrid) | set(meter))
    last_wb = 0.0
    run = 0
    worst = None
    for b in bins:
        # forward-fill wallbox within ~2 bins, else assume 0 (idle)
        near = [w for w in wb if 0 <= b - w <= 2 * BIN_S]
        if b in wb:
            last_wb = wb[b]
        elif near:
            last_wb = wb[max(near)]
        else:
            last_wb = 0.0
        if b not in mgrid or b not in meter:
            run = 0
            continue
        charging = last_wb > MIN_CHARGE_W
        div = abs(mgrid[b] - meter[b])  # both import-negative; |diff| = divergence
        if charging and div > THRESHOLD_W:
            run += 1
            if worst is None or div > worst["div"]:
                worst = {"bin": b, "div": div, "wb": last_wb,
                         "mgrid": mgrid[b], "meter": meter[b]}
            if run >= CONSEC:
                return True, worst
        else:
            run = 0
    return False, worst


def _ha_notify(message: str) -> bool:
    url = os.environ["HA_URL"].rstrip("/") + "/api/services/telegram_bot/send_message"
    req = urllib.request.Request(
        url, data=json.dumps({"message": message}).encode(),
        headers={"Authorization": "Bearer " + os.environ["HA_TOKEN"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status == 200


def _direct_telegram(message: str) -> bool:
    tok = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["WATCHDOG_TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    req = urllib.request.Request(
        url, data=json.dumps({"chat_id": chat, "text": message}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status == 200


def alert(message: str) -> None:
    if os.environ.get("WATCHDOG_TELEGRAM_CHAT_ID") and os.environ.get("TELEGRAM_BOT_TOKEN"):
        _direct_telegram(message)
    else:
        _ha_notify(message)


def _cooldown_active() -> bool:
    try:
        return (time.time() - float(STATE_FILE.read_text().strip())) < COOLDOWN_S
    except (FileNotFoundError, ValueError):
        return False


def _mark_alerted() -> None:
    STATE_FILE.write_text(str(int(time.time())))


def check_once(verbose: bool = False) -> int:
    mgrid, meter, wb = collect()
    alarm, worst = evaluate(mgrid, meter, wb)
    if verbose:
        print(f"bins: mgrid={len(mgrid)} meter={len(meter)} wb={len(wb)} | "
              f"alarm={alarm} worst={worst}")
    if alarm:
        if _cooldown_active():
            if verbose:
                print("alarm suppressed (cooldown active)")
            return 0
        w = worst
        msg = (
            "⚠️ Grid-correction watchdog\n"
            f"DTSU↔M-Bus diverged {w['div']:.0f} W while charging "
            f"(wallbox {w['wb']:.0f} W).\n"
            f"M-Bus {w['mgrid']:.0f} W vs corrected DTSU {w['meter']:.0f} W — the inverter "
            "may not be seeing the car load (proxy correction failing)."
        )
        alert(msg)
        _mark_alerted()
        print("ALERT sent:", msg.replace("\n", " "))
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loop", action="store_true", help="run forever, checking every WD_BIN_S")
    ap.add_argument("--test", action="store_true", help="send a test alert and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.test:
        alert("✅ Grid-correction watchdog: test alert — channel OK.")
        print("test alert sent")
        return 0
    if args.loop:
        while True:
            try:
                check_once(args.verbose)
            except Exception as e:  # never let the watchdog die on a transient
                print("watchdog error:", e)
            time.sleep(max(30, BIN_S))
    return check_once(args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
