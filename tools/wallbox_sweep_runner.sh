#!/usr/bin/env bash
# Unattended 1-phase wallbox calibration sweep.
#
# Frees the system for the measurement and ALWAYS puts it back, including on
# error or signal: the battery limits are restored, energy-manager is started
# and the wallbox current limit is set to 0. Safe to run twice.
set -uo pipefail

LOG=/home/dev/wallbox-sweep/sweep-$(date +%Y%m%d-%H%M).log
mkdir -p /home/dev/wallbox-sweep
exec > >(tee -a "$LOG") 2>&1

. /home/dev/.secrets/env
HA_SSH=(ssh -i /home/dev/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=10 root@192.168.0.202)

api_get()  { curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states/$1"; }
state()    { api_get "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])'; }
set_num()  { curl -s -o /dev/null -X POST -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
             "$HA_URL/api/services/number/set_value" -d "{\"entity_id\":\"$1\",\"value\":$2}"; }

echo "=== $(date -Is) 1-phase calibration sweep ==="

DIS=$(state number.battery_maximum_discharging_power)
CHG=$(state number.battery_maximum_charging_power)
echo "recorded battery limits: discharge=$DIS charge=$CHG"

restored=0
restore() {
  [ "$restored" = 1 ] && return
  restored=1
  echo "--- restoring ($(date -Is)) ---"
  curl -s -o /dev/null -X POST -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
    "$HA_URL/api/states/number.wallbox_current_limit" -d '{"state":"0","attributes":{"unit_of_measurement":"A","friendly_name":"Wallbox Current Limit","min":0,"max":16,"step":1}}'
  set_num number.battery_maximum_discharging_power "${DIS:-5000}"
  set_num number.battery_maximum_charging_power    "${CHG:-5000}"
  "${HA_SSH[@]}" 'ha addons start 8d023bea_energy-manager' >/dev/null 2>&1
  sleep 10
  echo "battery now: discharge=$(state number.battery_maximum_discharging_power) charge=$(state number.battery_maximum_charging_power)"
  echo "current limit now: $(state number.wallbox_current_limit) A"
  "${HA_SSH[@]}" 'ha addons info 8d023bea_energy-manager --raw-json' 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("energy-manager:", d["version"], d["state"])'
  echo "=== done $(date -Is); log: '"$LOG"' ==="
}
trap restore EXIT INT TERM

echo "pre-flight: car_ready=$(state binary_sensor.car_ready) phases=$(state sensor.wallbox_phases) PV=$(state sensor.solar_pv_total_ac_power)W"

echo "stopping energy-manager..."
"${HA_SSH[@]}" 'ha addons stop 8d023bea_energy-manager' >/dev/null 2>&1
echo "pinning the battery to 0 (both directions)..."
for i in 1 2 3; do
  set_num number.battery_maximum_discharging_power 0
  set_num number.battery_maximum_charging_power 0
  sleep 8
  d=$(state number.battery_maximum_discharging_power); c=$(state number.battery_maximum_charging_power)
  echo "  attempt $i: discharge=$d charge=$c"
  [ "${d%.*}" = 0 ] && [ "${c%.*}" = 0 ] && break
done

echo "--- sweep 6-16 A ---"
cd /home/dev/wallbox-sweep && python3 -u wallbox_calibration_sweep.py 6 16
