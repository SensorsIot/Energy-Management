#!/usr/bin/env bash
# Idempotent restore, for the case where the sweep was killed before its own
# trap could run.
#
# It acts ONLY when both battery power limits are still 0 — the sweep's
# fingerprint. energy-manager holds DISCHARGE at 0 by itself (the cheap-slot
# hold) but never charging as well, so an unconditional restore here unpins a
# battery that energy-manager wants held. On 2026-10-08 that is exactly what
# happened: this script set the discharge limit back to 5000 W at 03:30 while the
# car was charging, and the house battery drained from 62 % to 1 % into it over
# the next 75 minutes. energy-manager kept deciding "block" every 15 minutes and
# never re-asserted it, because it only writes the limit when its own decision
# changes.
set -uo pipefail
. /home/dev/.secrets/env
exec >> /home/dev/wallbox-sweep/safety.log 2>&1
echo "=== $(date -Is) safety restore ==="

# Never act while the sweep is still working: mid-sweep both battery limits are
# legitimately 0, which is also this script's trigger. The runner holds a lock
# with its PID and clears it in its own trap.
LOCK=/home/dev/wallbox-sweep/sweep.running
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "  sweep still running (pid $(cat "$LOCK")) — standing down"
  exit 0
fi
[ -f "$LOCK" ] && echo "  stale lock for pid $(cat "$LOCK") — the sweep died, continuing"

read_state() {
  curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states/$1" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["state"])'
}
dis=$(read_state number.battery_maximum_discharging_power)
chg=$(read_state number.battery_maximum_charging_power)
echo "  discharge=$dis charge=$chg"
if [ "${dis%.*}" != 0 ] || [ "${chg%.*}" != 0 ]; then
  echo "  not both pinned — the sweep cleaned up after itself, nothing to do"
  exit 0
fi
echo "  both still pinned — the sweep did not clean up; restoring"

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
