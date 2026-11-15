#!/bin/bash
# monitoring_watchdog.sh -- deployed by Roles/monitoring/tasks/watchdog.yml.
# Starts any alerting container that isn't running and pushes to the phone
# via Home Assistant directly (NOT via Alertmanager). Env from
# /etc/monitoring-watchdog.env. Notifications are rate-limited to one per
# container per 30 min so a crash-looping container can't spam the phone.
set -u
STATE=/var/lib/monitoring-watchdog; mkdir -p "$STATE"
[ -e /etc/monitoring-watchdog.pause ] && { echo "paused (/etc/monitoring-watchdog.pause)"; exit 0; }

notify() {  # $1 = key for rate limiting, $2 = title, $3 = message
  local stamp="$STATE/$1.notified" now; now=$(date +%s)
  if [ -f "$stamp" ] && [ $(( now - $(cat "$stamp") )) -lt 1800 ]; then return; fi
  echo "$now" > "$stamp"
  python3 - "$2" "$3" <<'PY' || echo "HA push failed"
import json, os, sys, urllib.request
req = urllib.request.Request(f"{os.environ['HA_URL']}/api/services/notify/{os.environ['HA_NOTIFY_SERVICE']}",
    data=json.dumps({"title": sys.argv[1], "message": sys.argv[2]}).encode(), method="POST",
    headers={"Authorization": "Bearer " + os.environ["HA_TOKEN"], "Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=15)
PY
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

for c in $CONTAINERS; do
  if running "$c"; then
    # Running but wedged: Alertmanager must also answer /-/ready (checked
    # from inside the monitoring network; it publishes no host port).
    if [ "$c" = alertmanager ] && running prometheus && \
       ! docker exec prometheus wget -qO- -T 5 http://alertmanager:9093/-/ready >/dev/null 2>&1; then
      echo "$c running but not ready -> restart"
      docker restart "$c" >/dev/null
      notify "$c" "⚠️ web-01: $c restarted" "$c was running but not answering /-/ready; restarted by monitoring-watchdog."
    fi
    continue
  fi
  echo "$c not running ($(docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' "$c" 2>/dev/null || echo missing)) -> starting"
  ( cd "$COMPOSE_DIR" && docker compose up -d "$c" ) >/dev/null 2>&1
  sleep 5
  if running "$c"; then
    notify "$c" "⚠️ web-01: $c was down" "$c was not running; monitoring-watchdog started it again. Check why: docker logs $c"
  else
    notify "$c" "🚨 web-01: $c DOWN" "$c is not running and monitoring-watchdog could NOT start it. Alerting may be blind. ssh web-01: cd $COMPOSE_DIR && docker compose up -d $c"
  fi
done
