# Claude Code Notes

## Host Access

### SSH to Hub (192.168.0.203)
```bash
ssh -i ~/.ssh/id_ed25519 pi@192.168.0.203
```

### Grafana
- URL: http://192.168.0.203:3000
- User: admin
- Password: admin (reset on 2026-01-15)
- API: Use basic auth `-u admin:admin`

```bash
# Get dashboard
curl -s "http://192.168.0.203:3000/api/dashboards/uid/DASHBOARD_UID" -u admin:admin

# Update dashboard
curl -s -X POST "http://localhost:3000/api/dashboards/db" \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d @dashboard.json
```

### InfluxDB
- URL: http://192.168.0.203:8087 (external) / localhost:8086 (from pi host)
- Container: influxdb2
- Org: spiessa
- Token: Mounted at `/home/dev/.secrets/influxdb` in add-on containers
- Config: `/home/pi/IOTstack/volumes/influxdb2/config/influx-configs` on hub

```bash
# Query via Grafana API proxy (recommended - uses Grafana's stored credentials)
ssh pi@192.168.0.203 "curl -s -X POST 'http://localhost:3000/api/ds/query' -u admin:admin \
  -H 'Content-Type: application/json' -d '{
  \"queries\": [{
    \"refId\": \"A\",
    \"datasource\": {\"uid\": \"byzPPbd4z\"},
    \"query\": \"from(bucket: \\\"load_forecast\\\") |> range(start: -1h) |> limit(n: 5)\"
  }],
  \"from\": \"now-1h\",
  \"to\": \"now\"
}'"

# Query from inside container (if you have the token)
ssh pi@192.168.0.203 'docker exec influxdb2 influx query "YOUR_QUERY" -o spiessa -t "TOKEN"'
```

### Docker on Hub
IOTstack setup at `/home/pi/IOTstack/`

Key containers:
- grafana (port 3000)
- influxdb2 (port 8087 external, 8086 internal)
- mosquitto (port 1883)

```bash
# Check containers
ssh pi@192.168.0.203 "docker ps"

# Grafana logs
ssh pi@192.168.0.203 "docker logs grafana --tail 50"

# InfluxDB logs
ssh pi@192.168.0.203 "docker logs influxdb2 --tail 50"
```

## Dashboards

### LoadForecast Dashboard
- UID: df9feonzp10xsc
- URL: http://192.168.0.203:3000/d/df9feonzp10xsc/loadforecast

### Forecast Dashboard
- UID: cf9druxb33yf4e
- URL: http://192.168.0.203:3000/d/cf9druxb33yf4e/forecast
