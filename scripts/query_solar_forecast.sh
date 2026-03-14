#!/bin/bash
# Query InfluxDB for solar forecast (hybrid model only)
# Args: today|tomorrow
#
# Used as HA command_line sensor. Deployed to /config/scripts/ on HA.
# Requires /config/scripts/.env with INFLUX_URL and INFLUX_TOKEN.

source /config/scripts/.env

if [ "$1" = "today" ]; then
    QUERY="import \"date\"
import \"timezone\"
option location = timezone.location(name: \"Europe/Zurich\")
today_start = date.truncate(t: now(), unit: 1d)
tomorrow_start = date.add(d: 1d, to: today_start)
from(bucket: \"pv_forecast\")
  |> range(start: now(), stop: tomorrow_start)
  |> filter(fn: (r) => r._measurement == \"pv_forecast\")
  |> filter(fn: (r) => r.inverter == \"total\")
  |> filter(fn: (r) => r.model == \"hybrid\")
  |> filter(fn: (r) => r._field == \"energy_wh_p50\")
  |> group()
  |> sum()
  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))"
elif [ "$1" = "tomorrow" ]; then
    QUERY="import \"date\"
import \"timezone\"
option location = timezone.location(name: \"Europe/Zurich\")
today_start = date.truncate(t: now(), unit: 1d)
tomorrow_start = date.add(d: 1d, to: today_start)
day_after = date.add(d: 2d, to: today_start)
from(bucket: \"pv_forecast\")
  |> range(start: tomorrow_start, stop: day_after)
  |> filter(fn: (r) => r._measurement == \"pv_forecast\")
  |> filter(fn: (r) => r.inverter == \"total\")
  |> filter(fn: (r) => r.model == \"hybrid\")
  |> filter(fn: (r) => r._field == \"energy_wh_p50\")
  |> group()
  |> sum()
  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))"
else
    echo "0"
    exit 0
fi

RESULT=$(curl -s --request POST "$INFLUX_URL" \
  --header "Authorization: Token $INFLUX_TOKEN" \
  --header "Content-Type: application/vnd.flux" \
  --data "$QUERY" 2>/dev/null)

# Extract the numeric value from CSV response
VALUE=$(echo "$RESULT" | grep -v "^#" | grep -v "^$" | grep "," | tail -1 | awk -F, '{print $NF}')

if [ -n "$VALUE" ] && [ "$VALUE" != "" ]; then
    # Use awk for rounding instead of printf (more portable)
    echo "$VALUE" | awk '{printf "%.2f\n", $1}'
else
    echo "0"
fi
