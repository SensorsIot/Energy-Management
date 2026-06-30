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
Check `/config/<addon>.yaml.example` for new options, add the ones you want to your user config, and
restart.

## Routine operations
- **Deploy an add-on update** — bump the add-on version (see
  [`Harness/project/build-and-release.md`](Harness/project/build-and-release.md)), commit/push, then
  rebuild via HA Supervisor (`ha addons rebuild`).
- **Inspect time-series / dashboards** — InfluxDB and Grafana, reached via the `remote-connections`
  skill.

## Monitoring

**Grid-correction watchdog** — alerts via Telegram when the Huawei DTSU grid meter (corrected by the
ESP32 Modbus proxy with the wallbox power) diverges from the independent M-Bus meter while the
wallbox is charging — i.e. the proxy correction has failed and the inverter is not seeing the
car load (ocpp-server-fsd §3.6.6).

- Script: `tools/grid_correction_watchdog.py` (read-only InfluxDB; alerts via HA `telegram_bot.send_message`).
- Runs on the VM host as the systemd service `grid-correction-watchdog.service` (`--loop`, 30 s cadence).
- Fires on a sustained divergence (default ≥ 3 bins of 30 s over 2.5 kW while wallbox > 1.5 kW), then
  stays quiet for 30 min. Tunables via `WD_*` env vars (`WD_THRESHOLD_W`, `WD_CONSEC`, `WD_COOLDOWN_S`, …).
- Manage: `sudo systemctl {status,restart} grid-correction-watchdog` on the VM host;
  `python3 tools/grid_correction_watchdog.py --test` sends a test alert, `-v` prints a one-shot check.

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
`/home/energymanagement/swisssolarforecast/grafana-forecast-dashboard.json`. Import it via Grafana →
**Dashboards → New → Import**, upload the JSON, and select the InfluxDB datasource. Panels: PV Power
Forecast (P10/P50/P90 bands), Load Forecast (P10/P50/P90 bands), Net Power (surplus/deficit),
Cumulative Energy, Weather (GHI, temperature), and a statistics table.

## Troubleshooting

### No forecast data
Check GRIB downloads and the add-on log:
```bash
ls -la /share/swisssolarforecast/icon-ch1/
ls -la /share/swisssolarforecast/icon-ch2/
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
