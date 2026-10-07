#!/usr/bin/env bash
# Unconditional, idempotent restore. Runs after the sweep window whatever
# happened, so the house is never left with the battery pinned or
# energy-manager stopped.
set -uo pipefail
. /home/dev/.secrets/env
exec >> /home/dev/wallbox-sweep/safety.log 2>&1
echo "=== $(date -Is) safety restore ==="
for e in number.battery_maximum_discharging_power number.battery_maximum_charging_power; do
  curl -s -o /dev/null -X POST -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
    "$HA_URL/api/services/number/set_value" -d "{\"entity_id\":\"$e\",\"value\":5000}"
done
ssh -i /home/dev/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes root@192.168.0.202 \
  'ha addons start 8d023bea_energy-manager' >/dev/null 2>&1
sleep 8
for e in number.battery_maximum_discharging_power number.battery_maximum_charging_power number.wallbox_current_limit; do
  v=$(curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states/$e" | python3 -c 'import json,sys;print(json.load(sys.stdin)["state"])')
  echo "  $e = $v"
done
