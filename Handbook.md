# Energy Management System — Operator Handbook (OPERATE)

How to run the Energy Management System. Human procedures, present-state. This indexes the
operational tasks; behaviour lives in the add-on FSDs (see [`STRUCTURE.md`](STRUCTURE.md)), build
method in [`Harness/`](Harness/).

## Access & prerequisites
Host access — SSH, InfluxDB, Grafana, Home Assistant, Docker — is driven by the `remote-connections`
skill, which holds the connection details and where credentials are loaded from. Secrets live in the
environment, never in the repo.

### WiFi access point (devolo)

The WiFi serving the cellar — including the gPlug smart-meter reader — is a **devolo dLAN 2400
WiFi ac**. There are two powerline nodes and **only one has a radio**:

| Host | IP | Role |
|---|---|---|
| `devolo-635` | `192.168.0.12` | **The access point.** OpenWrt (Chaos Calmer 15.05.1, IPQ40xx) with a ubus JSON-RPC API. Radios: `ath0` = 2.4 GHz `private-2G`, `ath1` = 5 GHz `Smart-5G` |
| `devolo-494` | `192.168.0.9` | Powerline **Domain Master** (*Magic 2 LAN 1-1*), LAN only — **no radio** |

Backhaul from the AP to the router runs over powerline on the cellar mains.

#### Read a client's signal as the AP sees it

This answers "is this an uplink or a downlink problem?" — the question a client's own RSSI
cannot settle on its own.

```bash
NULL=00000000000000000000000000000000
SID=$(curl -s -X POST http://192.168.0.12/ubus -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$NULL\",\"session\",\"login\",{\"username\":\"root\",\"password\":\"YOUR_DEVOLO_PASSWORD\"}]}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['result'][1]['ubus_rpc_session'])")

curl -s -X POST http://192.168.0.12/ubus -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$SID\",\"iwinfo\",\"assoclist\",{\"device\":\"ath0\"}]}"
```

Each station returns `signal` (dBm **as heard by the AP**), `noise`, `inactive` (ms since its last
frame) and `rx`/`tx` rates. Compare that against the RSSI the client reports for itself: the two
measure **opposite directions**, and treating one as if it described both sends you after the wrong
fix — e.g. raising a client's transmit power when it is the downlink that is weak.

Radio state: `iwinfo info ath0`. Full settings: `uci get wireless` over the same session.

#### Gotchas

- Radios are **`ath0`/`ath1`**, not `wlan0`. A wrong device name returns a bare `[4]` (NOT_FOUND),
  which reads like a permission failure but is not. Enumerate with `iwinfo devices` first.
- The vendor `api` object (including `WifiConnectedStationsGet`) is **ACL-denied even with a valid
  session**. Use `iwinfo`.
- Fetch the web UI with `curl --compressed`. Responses are gzip; read raw, they look like binary
  noise and invite the wrong conclusion about what the device exposes.
- `http://192.168.0.9/assets/data.cfl` is an **unauthenticated** key=value dump
  (`AUTHREQUIRED=N`) — powerline topology, node names and model IDs, no login needed.
- **Do not run `iwinfo scan` or `WifiNeighborAPsGet` casually.** On an AP-mode radio these scan
  off-channel and can briefly drop associated clients — including the device you are diagnosing.

Per the policy above, the AP password is **not stored in this repo** (which is public). Substitute
it for `YOUR_DEVOLO_PASSWORD` from the environment or your password manager.

## Installation

Prerequisites: Home Assistant OS or Supervised install; InfluxDB 2.x with buckets configured;
network access to the MeteoSwiss API.

1. **Add the repository** — **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add
   `https://github.com/SensorsIot/Energy-Management`.
2. **Install each add-on** — find it in the store, **Install**, configure options in the
   **Configuration** tab, then **Start**.
3. **InfluxDB buckets:**
   ```bash
   influx bucket create --name pv_forecast --retention 30d
   influx bucket create --name load_forecast --retention 30d
   ```
4. **Verify** — check the add-on log (**Settings → Add-ons → [Add-on] → Log**) and query InfluxDB:
   ```flux
   from(bucket: "pv_forecast")
     |> range(start: -1h)
     |> filter(fn: (r) => r._measurement == "pv_forecast")
     |> limit(n: 10)
   ```

The add-on config split (secrets vs YAML) is build-side — see
[`Harness/project/addon-architecture.md`](Harness/project/addon-architecture.md).

### Per-add-on setup & update workflow

**Initial setup:** install the add-on → **Configuration** tab → enter secrets (tokens) → **Save** →
**Start** (creates the default config file) → edit `/addon_configs/<slug>/<addon>.yaml` via File
Editor → restart.

**After updates:** the add-on updates automatically (if enabled); the user config is never modified.
Check `/addon_configs/<slug>/<addon>.yaml.example` for new options, add the ones you want to the
sibling user config, and restart.

## Routine operations
- **Deploy an add-on update** — bump the add-on version (see
  [`Harness/project/build-and-release.md`](Harness/project/build-and-release.md)), commit/push, then
  rebuild via HA Supervisor (`ha addons rebuild`).
- **Inspect time-series / dashboards** — InfluxDB and Grafana, reached via the `remote-connections`
  skill.
- **Lock / unlock the wallbox cable** — the **Kabel** button on the Amazon-Fire dashboard
  (`lovelace-amazonfire`, *Overwiew* view, between *Waschen* and *all Off*) toggles
  `switch.wallbox_cable_lock`: on = locked (green→orange icon), off = unlocked. Mechanism and states
  are specified in [`ocpp-server` FSD §3.6.7](ocpp-server/Documents/ocpp-server-fsd.md#367-cable-lock-control-user).
  The setting is a **policy applied at unplug time** — flipping it does not move the lock on a
  currently-plugged car; it decides whether the wallbox releases the cable the next time the car is
  unplugged. The switch reflects the wallbox's real setting (re-read on every reconnect); if the
  wallbox is offline the toggle snaps back.

### Measure the wallbox calibration

`tools/wallbox_calibration_sweep.py` walks the wallbox through each amp step and measures what it
actually draws. It produces the two constants in
[`ocpp-server` FSD §7.1 / §7.2](ocpp-server/Documents/ocpp-server-fsd.md#71-linear-regression):
`WATTS_PER_AMP` and the meter corrections.

**Run it at 02:00, not in the afternoon.** The measurement rests on the car sitting between the EBL
meter and the DTSU, so `grid_power − dtsu_raw` is the car and nothing else. Three things spoil that
by day, all of them verified on 2026-10-07:

| Spoiler | Effect |
|---|---|
| A moving house load | The meters do not sample together, so the common term stops cancelling |
| PV running | The EBL per-phase currents are **net** magnitudes, so PV on the car's phase subtracts from them |
| The home battery moving | Adds a term the subtraction cannot see |

The sweep is **paced by the EBL meter**, which reports only every ~16 s while the DTSU runs near
1 Hz. It counts a sample only when the meter's timestamp advances. Sampling on a wall clock instead
re-reads a stale value: a contaminated run reported `sd 15 W` at 16 A while being 476 W wrong,
because all six samples were the same number.

**Prerequisites**, all already in place:

- `sensor.grid_phase_1_current` / `_2_` / `_3_` — MQTT sensors for the gPlug `I1`/`I2`/`I3` fields.
  With these the sweep compares commanded amps against measured amps on the car's phase, which needs
  no voltage and no watts-per-amp assumption and is the most precise figure it produces
  (sd ~0.01 A against ~47 W for the power subtraction). It also names the car's phase.
- The car plugged in and **below** its own charging target, or it will refuse to draw.

**Dwell, do not hurry.** The binding cadence is the **wallbox**, not the EBL meter — and the wallbox
reading is the quantity being calibrated. Measured 2026-10-08, it reports MeterValues on a
rock-steady **60 s** (`02:03:19`, `02:04:19`, `02:05:19`, …) against the EBL meter's ~16 s.

| | EBL reports | ≈ time | wallbox reports |
|---|---:|---:|---:|
| Settle after a step change | 10 | 160 s | 2–3 at the new level |
| Sample | 25 | 400 s | **6–7** |

Sampling is sized so one bad wallbox report is a seventh of the evidence rather than a quarter. The
result table carries the standard error of each step's mean **and the number of wallbox reports
behind it**, counted from `last_reported` advancing (`last_changed` does not move when a steady
charge reports the same number again). A step resting on one meter sample is therefore visible
rather than disguised as an average.

A full 6–16 A sweep takes about **two hours**, finishing around 04:00 from a 02:00 start — well
inside the cheap window, and an hour clear of the 05:00 safety nets.

The idle reference is measured **twice**, before and after, with a longer settle (10 EBL reports) so a
car that charged right up to the start has actually wound down. It is subtracted from every step, so
the two are compared: if they differ by more than 50 W the absolute figures are suspect while the
ratios remain usable.

**Unattended run.** `tools/wallbox_sweep_runner.sh` does the whole sequence — record the battery
limits, stop energy-manager, pin both battery limits to 0, sweep, restore — with a `trap` that
restores on success, error or `SIGTERM`. Deploy it to the VM host and arm a timer:

```bash
scp tools/wallbox_calibration_sweep.py tools/wallbox_sweep_runner.sh \
    tools/wallbox_sweep_safety_restore.sh dev@192.168.0.160:/home/dev/wallbox-sweep/
ssh dev@192.168.0.160 "export XDG_RUNTIME_DIR=/run/user/\$(id -u); \
  systemd-run --user --unit=wallbox-sweep --on-calendar='*-*-* 02:00:00' \
    /home/dev/wallbox-sweep/sweep_runner.sh"
```

The host's user systemd instance has `Linger=no`, so it lives only as long as that host's tmux
session. Three layers cover a failure:

| Layer | Covers |
|---|---|
| `trap` in the runner | normal end, error, `SIGTERM` |
| `wallbox-sweep-safety` timer (05:00, same host) | the runner killed with `SIGKILL` |
| `automation.wallbox_sweep_safety_restore` (05:00, in HA) | the VM host or its tmux session dying |

Both nets fire only when **both** battery power limits are 0 — the sweep's fingerprint, since
energy-manager holds *discharge* at 0 on its own during a cheap-slot hold but never charging as
well. The HA automation is the only one that cannot die with the sweep.

**A net must never fire mid-sweep**, because during the sweep both limits are legitimately 0 and that
is also the trigger. The runner holds `sweep.running` containing its PID and clears it in its trap;
the host-side net stands down while that PID is alive and treats a stale lock as a death. Schedule
the nets well clear of the run regardless — a full 6–16 A sweep takes about two hours.

An unconditional net is actively harmful: on 2026-10-08 one set the discharge limit back to 5000 W at
03:30 while the car was charging, and the home battery drained from 62 % to 1 % into it.

Results land in `/home/dev/wallbox-sweep/sweep-<date>.log`. The sweep **refuses to fit** a constant
when fewer than four steps survive its spread and increment gates, so a contaminated run yields no
number rather than a wrong one.

## Monitoring

**Grid-correction watchdog** — a native HA automation (`automation.grid_correction_watchdog`) that
alerts via Telegram only when **buying** energy (M-Bus `sensor.grid_power` < 0 = import) while the
wallbox is charging (`sensor.wallbox_power` > 1.5 kW) **and** the corrected Huawei DTSU
(`sensor.power_meter_active_power`) **under-reads** that import by more than **1 kW for 90 s** — i.e.
the proxy correction has failed and the grid is silently supplying the car (ocpp-server-fsd §3.6.6).
A healthy correction tracks within ~100 W, so 1 kW is a clear failure with margin. The check is
**directional and import-only** (`grid < 0` and `power_meter − grid > 1 kW`): export and the by-design
ramp overshoot never alarm — only a costly silent import does.

It runs entirely in HA (production) — no external host, script, or cron — and sends via the
`telegram_bot.send_message` service (chat configured in HA). `mode: single` gives a natural
per-episode cooldown. Adjust the thresholds by editing the automation; test-fire with
`automation.trigger` on `automation.grid_correction_watchdog`.

**M-Bus staleness alert** — the energy-manager add-on sends a Telegram warning when the M-Bus grid
meter (`sensor.grid_power`, external gPlug reader) stops publishing fresh readings for longer than
`mbus_stale_alert_seconds` (default 300 s), and a recovery notice when it returns. This reading
feeds grid/energy **reporting** only (EV and battery control run on PV−load surplus), so a dead
reader would otherwise pass silently. Tune the
threshold via the add-on's `sensors.mbus_stale_alert_seconds` option; it uses the add-on's own
`telegram_bot_token` / `telegram_chat_id` (not the HA `telegram_bot` service). Behaviour is specified
in [`energy-manager` FSD §4.7.5](energy-manager/Documents/energy-manager-fsd.md#475-m-bus-staleness-alert).

## Dashboards & queries

Grafana queries operators use to visualize each add-on's output. Behaviour/schemas these read are
specified in the owning FSD (see [`STRUCTURE.md`](STRUCTURE.md)).

### LoadForecast

**Load forecast with uncertainty band:**
```flux
from(bucket: "load_forecast")
  |> range(start: now(), stop: 120h)
  |> filter(fn: (r) => r._measurement == "load_forecast")
  |> filter(fn: (r) => r._field == "power_w_p10" or r._field == "power_w_p50" or r._field == "power_w_p90")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

**Forecast vs actual:**
```flux
forecast = from(bucket: "load_forecast")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r._field == "power_w_p50")
actual = from(bucket: "HomeAssistant")
  |> range(start: -24h, stop: now())
  |> filter(fn: (r) => r.entity_id == "house_load_power")
  |> aggregateWindow(every: 15m, fn: mean)
union(tables: [forecast, actual])
```

### SwissSolarForecast

**PV power forecast with uncertainty band:**
```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 120h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r.inverter == "total")
  |> filter(fn: (r) => r._field == "power_w_p10" or r._field == "power_w_p50" or r._field == "power_w_p90")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
```

**Per-inverter comparison:**
```flux
from(bucket: "pv_forecast")
  |> range(start: now(), stop: 120h)
  |> filter(fn: (r) => r._measurement == "pv_forecast")
  |> filter(fn: (r) => r._field == "power_w_p50")
  |> pivot(rowKey: ["_time"], columnKey: ["inverter"], valueColumn: "_value")
```

### Pre-built Grafana dashboard

A pre-built dashboard JSON ships at
`/home/energymanagement/swiss-solar-forecast/grafana-forecast-dashboard.json`. Import it via Grafana →
**Dashboards → New → Import**, upload the JSON, and select the InfluxDB datasource. Panels: PV Power
Forecast (P10/P50/P90 bands), Load Forecast (P10/P50/P90 bands), Net Power (surplus/deficit),
Cumulative Energy, Weather (GHI, temperature), and a statistics table.

## Troubleshooting

### No forecast data
Check GRIB downloads and the add-on log:
```bash
ls -la /share/swiss-solar-forecast/icon-ch1/
ls -la /share/swiss-solar-forecast/icon-ch2/
```
Then **Settings → Add-ons → SwissSolarForecast → Log**.

### InfluxDB connection failed
```bash
curl -H "Authorization: Token YOUR_TOKEN" http://192.168.0.203:8087/api/v2/buckets
```
Verify the credentials in the add-on Configuration tab.

### Load forecast empty
Check historical data exists, and that `entity_id` matches your sensor:
```flux
from(bucket: "HomeAssistant")
  |> range(start: -7d)
  |> filter(fn: (r) => r.entity_id == "house_load_power")
  |> count()
```

### InfluxDB delete-API performance
Symptoms: add-ons hang at "Deleting future forecasts"; InfluxDB memory > 5 GB; high CPU; timeouts.
Diagnose the goroutine count (normal 100–200, problem > 1000):
```bash
curl http://192.168.0.203:8087/debug/pprof/goroutine?debug=1 | head -1
```
Recover by restarting the container (`docker restart influxdb2`; memory should drop to ~2 GB).
All add-ons use `run_time` as a field, not a tag, so points overwrite on the same
`measurement + tags + timestamp` without delete operations — avoiding the slow InfluxDB 2.x delete
API and its goroutine deadlocks.
