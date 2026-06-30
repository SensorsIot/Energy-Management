# Project — Home Assistant add-on architecture

The canonical Home Assistant add-on configuration architecture used by **all** add-ons in this
project. Operator setup/update steps are in the Handbook (Dashboards & queries / operations); this
doc is the build pattern.

## Configuration philosophy

Configuration splits into two homes:

```
SECRETS                              NON-SECRETS
(tokens, passwords)                  (settings, options)
  HA Configuration Tab                 /config/<addon>.yaml
  • masked fields                      • editable via File Editor / VS Code
  • secure storage (/data/options.json)• version controlled
  • never in files                     • YAML load
        │ bashio::config → env vars          │ YAML load
        └──────────────┬─────────────────────┘
                       ▼
            Python runtime merges both into the final config
```

## Secrets (HA Configuration UI)

Secrets — API tokens, passwords, bot tokens, private keys — are **never** stored in YAML files.

1. User opens **Settings → Add-ons → [Add-on] → Configuration** and enters secrets in masked fields.
2. HA Supervisor stores them in `/data/options.json`.
3. The startup script reads them via `bashio::config` and exports them as environment variables.
4. Python reads from `os.environ`.

```yaml
# config.yaml schema
options:
  influxdb_token: ""
  telegram_bot_token: ""
  telegram_chat_id: ""
schema:
  influxdb_token: password
  telegram_bot_token: password?
  telegram_chat_id: str?
```

```bash
# startup script
#!/command/with-contenv bashio
if bashio::config.has_value 'influxdb_token'; then
  export INFLUXDB_TOKEN="$(bashio::config 'influxdb_token')"
fi
exec python3 /app/run.py --config "/config/addon.yaml"
```

## Non-secrets (public add-on config)

Non-sensitive configuration lives in user-editable YAML.

| Context | Path |
|---------|------|
| Inside container | `/config/<addon>.yaml` |
| HA File Editor / VS Code | `/addon_configs/<addon_slug>/<addon>.yaml` |
| Host filesystem | `/usr/share/hassio/addon_configs/<addon_slug>/` |

Enable with `map: [addon_config:rw]` in `config.yaml`. Connection settings, device settings,
schedules, feature flags, and log level go here — tokens never do.

## Templates and defaults

Each add-on ships a template at `/usr/share/<addon>/<addon>.yaml.example`.

| Event | Action |
|-------|--------|
| First run (no user config) | Copy template → `/config/<addon>.yaml` |
| Every start | Copy template → `/config/<addon>.yaml.example` |
| Update/upgrade | **Never** overwrite user config |
| New options added | Handle via code defaults; update `.example` |

## Configuration merge order

```
1. Load defaults from template   → /usr/share/addon/addon.yaml.example
2. Load user config (overrides)  → /config/addon.yaml
3. Overlay secrets from env      → INFLUXDB_TOKEN, TELEGRAM_BOT_TOKEN, …
4. Apply code defaults for missing keys → config.get("key", default)
```

```python
def load_config(config_path: str) -> dict:
    defaults = yaml.safe_load(open("/usr/share/addon/addon.yaml.example"))
    user_config = yaml.safe_load(open(config_path))
    merged = deep_merge(defaults, user_config)          # user wins
    if os.environ.get("INFLUXDB_TOKEN"):
        merged["influxdb"]["token"] = os.environ["INFLUXDB_TOKEN"]
    return merged
```

## Add-on configuration files summary

| Add-on | Secrets (Config UI) | Non-Secrets (YAML) |
|--------|--------------------|--------------------|
| EnergyManager | `influxdb_token`, `telegram_bot_token`, `telegram_chat_id` | `/config/energymanager.yaml` |
| SwissSolarForecast | `influxdb_token`, `telegram_bot_token`, `telegram_chat_id` | `/config/swisssolarforecast.yaml` |
| LoadForecast | `influxdb_token` | `/config/loadforecast.yaml` |

## Best practices

| Practice | Do | Don't |
|----------|----|-------|
| Secrets | Store in HA Configuration UI | Put in YAML files |
| User config | Let the user edit via File Editor | Auto-modify user files |
| Defaults | Apply in code for missing keys | Require all keys in user config |
| Updates | Refresh the `.example` file | Overwrite user config |
| Logging | Log "token loaded" (not the value) | Log secret values |
