#!/usr/bin/env python3
"""
n8n_watchdog.py — DB-aware health check + self-healing for n8n on web-01.

Why (incident_n8n_db_pool_wedged_2026-10-03): n8n's connection pool wedged on dead
Postgres connections for ~2h. `/healthz` kept answering {"status":"ok"} (it never
touches the DB), so nothing noticed; the public API answered 401/timeouts and every
webhook (incl. Echo's chat) failed. `docker restart n8n` fixed it.

Checks every run (systemd timer, ~2 min):
  1. GET /healthz/readiness on the container (n8n's own connected+migrated flags)
  2. GET /api/v1/workflows?limit=1 with the API key — validating the key needs the DB,
     so this is a real end-to-end DB round trip (exactly what failed in the incident)
Both must pass within the timeout. After FAIL_THRESHOLD consecutive failures it
restarts the container, waits for recovery and posts the outcome to the System Alerts
Matrix room DIRECTLY (dedicated bot) — never through n8n, which is the thing that's down.

Loop guard: at most one restart per RESTART_COOLDOWN_S; while still failing after
that, it only posts "needs manual attention" (at most once per ALERT_REPEAT_S).
A container that is deliberately stopped, or restarting/starting, is left alone.

Config: environment (EnvironmentFile, root-only). Standard library only.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONTAINER = os.getenv("N8N_CONTAINER", "n8n")
API_KEY = os.environ["N8N_API_KEY"]
TIMEOUT_S = float(os.getenv("CHECK_TIMEOUT_S", "10"))
FAIL_THRESHOLD = int(os.getenv("FAIL_THRESHOLD", "3"))
RESTART_COOLDOWN_S = int(os.getenv("RESTART_COOLDOWN_S", "1800"))
ALERT_REPEAT_S = int(os.getenv("ALERT_REPEAT_S", "3600"))
RECOVERY_WAIT_S = int(os.getenv("RECOVERY_WAIT_S", "120"))
STARTUP_GRACE_S = int(os.getenv("STARTUP_GRACE_S", "180"))
STATE_FILE = os.getenv("STATE_FILE", "/var/lib/n8n-watchdog/state.json")
MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER", "")
MATRIX_ROOM_ID = os.getenv("MATRIX_ROOM_ID", "")
MATRIX_BOT_SECRET = os.getenv("MATRIX_BOT_SECRET", "")
DRY_RUN = os.getenv("DRY_RUN", "") == "1"


def log(msg):
    print(msg, flush=True)  # journald captures stdout


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return {"fails": 0, "last_restart": 0, "last_alert": 0, "alerted": False}


def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    json.dump(s, open(tmp, "w"))
    os.replace(tmp, STATE_FILE)


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)


def container_info():
    r = docker("inspect", CONTAINER, "--format",
               "{{.State.Status}}|{{.State.StartedAt}}|"
               "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}")
    if r.returncode != 0:
        return None
    status, started, ips = r.stdout.strip().split("|", 2)
    ip = ips.split()[0] if ips.split() else ""
    try:
        from datetime import datetime
        age = time.time() - datetime.fromisoformat(started[:26].rstrip("Z") + "+00:00").timestamp()
    except ValueError:
        age = 1e9
    return {"status": status, "ip": ip, "age_s": age}


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:120].decode(errors="replace")
    except Exception as e:  # noqa: BLE001 — timeouts, refused, DNS: all mean "not healthy"
        return None, f"{type(e).__name__}: {e}"


def check(ip):
    base = f"http://{ip}:5678"
    code, err = get(f"{base}/healthz/readiness")
    if code != 200:
        return False, f"readiness -> {code or 'no response'} {err}".strip()
    code, err = get(f"{base}/api/v1/workflows?limit=1", {"X-N8N-API-KEY": API_KEY})
    if code != 200:
        return False, f"API (DB round trip) -> {code or 'no response'} {err}".strip()
    return True, "readiness 200 + API 200"


def matrix(text):
    if not (MATRIX_HOMESERVER and MATRIX_ROOM_ID and MATRIX_BOT_SECRET):
        log("matrix not configured; message was: " + text)
        return False
    if DRY_RUN:
        log("[dry-run] would post: " + text)
        return True
    url = (f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{MATRIX_ROOM_ID}"
           f"/send/m.room.message/n8nwatchdog-{int(time.time() * 1000)}")
    req = urllib.request.Request(url, data=json.dumps({"msgtype": "m.text", "body": text}).encode(),
                                 method="PUT", headers={"Authorization": f"Bearer {MATRIX_BOT_SECRET}",
                                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        log(f"matrix post failed: {e}")
        return False


def main():
    s = load_state()
    now = time.time()
    info = container_info()
    if not info:
        log(f"container {CONTAINER} not found — leaving alone")
        return 0
    if info["status"] != "running":
        log(f"container status={info['status']} (deliberately stopped/restarting?) — leaving alone")
        return 0
    if info["age_s"] < STARTUP_GRACE_S:
        log(f"container started {info['age_s']:.0f}s ago — startup grace, not judging yet")
        return 0

    ok, detail = check(info["ip"])
    if ok:
        if s.get("alerted"):
            matrix(f"✅ n8n healthy again ({detail}).")
        if s["fails"] or s.get("alerted"):
            log(f"recovered: {detail}")
        s.update(fails=0, alerted=False)
        save_state(s)
        return 0

    s["fails"] += 1
    log(f"UNHEALTHY ({s['fails']}/{FAIL_THRESHOLD}): {detail}")
    if s["fails"] < FAIL_THRESHOLD:
        save_state(s)
        return 1

    if now - s.get("last_restart", 0) < RESTART_COOLDOWN_S:
        if now - s.get("last_alert", 0) >= ALERT_REPEAT_S:
            matrix(f"🚨 n8n still unhealthy after an automatic restart {int((now - s['last_restart']) / 60)} min ago "
                   f"— needs manual attention. Last check: {detail}")
            s["last_alert"] = now
            s["alerted"] = True
        save_state(s)
        return 1

    log("restarting n8n" + (" [dry-run]" if DRY_RUN else ""))
    s["last_restart"] = now
    save_state(s)
    if not DRY_RUN:
        r = docker("restart", CONTAINER)
        if r.returncode != 0:
            matrix(f"🚨 n8n unhealthy ({detail}) and `docker restart` FAILED: {r.stderr.strip()[:200]}")
            s.update(last_alert=now, alerted=True)
            save_state(s)
            return 1
    deadline = time.time() + RECOVERY_WAIT_S
    ok2, detail2 = False, "not re-checked"
    while time.time() < deadline and not DRY_RUN:
        time.sleep(10)
        info = container_info()
        if info and info["status"] == "running" and info["ip"]:
            ok2, detail2 = check(info["ip"])
            if ok2:
                break
    if ok2:
        matrix(f"🔧 n8n was unhealthy for {s['fails']} checks ({detail}); watchdog restarted it — "
               f"healthy again ({detail2}).")
        s.update(fails=0, alerted=False, last_alert=now)
    else:
        matrix(f"🚨 n8n unhealthy ({detail}); watchdog restarted it but it is NOT healthy after "
               f"{RECOVERY_WAIT_S}s ({detail2}). Needs manual attention.")
        s.update(alerted=True, last_alert=now)
    save_state(s)
    return 0 if ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
