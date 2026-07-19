# Energy Management System — Operator Handbook (OPERATE)

How to run the Energy Management System. Human procedures, present-state. This indexes the
operational tasks; behaviour lives in the add-on FSDs (see [`STRUCTURE.md`](STRUCTURE.md)), build
method in [`Harness/`](Harness/).

## Access & prerequisites
Host access — SSH, InfluxDB, Grafana, Home Assistant, Docker — is driven by the `remote-connections`
skill, which holds the connection details and where credentials are loaded from. Secrets live in the
environment, never in the repo.

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
