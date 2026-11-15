#!/usr/bin/env python3
"""
ep_probe.py — dependency-aware health probes + daily canary questions for the
Elementary Particles / One Concierge stack (ONE_CONCIERGE_ENHANCEMENT_PLAN.md, E0).

Lesson behind it (incident_n8n_db_pool_wedged_2026-10-03): "process is up" is not
"it works". Every probe here exercises the real dependency — an API call that needs
the database, an authenticated request, a real answer through the front door.

ALERT-ONLY by design. Only n8n auto-restarts (Roles/n8n_watchdog, proven failure
mode). Alerts go straight to the System Alerts Matrix room via the dedicated bot —
never through n8n.

Usage:  ep_probe.py --config probes.json --group health|canary [--dry-run] [--only NAME]
Config: JSON list of probes, e.g.
  {"name": "wanderer-api", "group": "health", "interval_s": 300, "threshold": 2,
   "type": "http", "method": "GET", "url": "http://10.0.30.11:3010/api/v1/trail?perPage=1",
   "headers": {"Authorization": "Bearer ${WANDERER_TOKEN}"}, "expect_status": [200],
   "expect_body": "\"items\"", "timeout_s": 15, "what": "Wanderer + its PocketBase DB"}
  url may be "container://NAME:PORT/path" (resolved to the container's IP — for
  services with no published port). ${VAR} is substituted from the environment
  (root-only EnvironmentFile), never logged. type "dns": {"server", "qname",
  "expect_ip"}. type "tcp": {"host", "port", optional "send" (sent as-is) and
  "expect_body" regex on the first reply} -- e.g. a Wyoming describe/info. Canary probes: type http with "json" body and "expect_body" regex
  (case-insensitive) on the answer.
State: /var/lib/ep-probes/<group>.json (consecutive failures, last alert, alerted).
"""
import argparse
import json
import os
import random
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

STATE_DIR = os.getenv("STATE_DIR", "/var/lib/ep-probes")
MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER", "")
MATRIX_ROOM_ID = os.getenv("MATRIX_ROOM_ID", "")
MATRIX_BOT_SECRET = os.getenv("MATRIX_BOT_SECRET", "")
REPEAT_S = int(os.getenv("ALERT_REPEAT_S", "21600"))  # re-alert every 6 h while still down


def log(msg):
    print(msg, flush=True)


def subst(s):
    return re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), s)


def resolve_url(url):
    m = re.match(r"container://([\w.-]+):(\d+)(/.*)?$", url)
    if not m:
        return url
    r = subprocess.run(["docker", "inspect", m.group(1), "--format",
                        "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
                       capture_output=True, text=True, timeout=20)
    ip = (r.stdout.split() or [""])[0]
    if r.returncode != 0 or not ip:
        raise RuntimeError(f"container {m.group(1)} not found/running")
    return f"http://{ip}:{m.group(2)}{m.group(3) or '/'}"


def http_probe(p):
    url = subst(resolve_url(p["url"]))
    data, headers = None, {k: subst(v) for k, v in (p.get("headers") or {}).items()}
    if "json" in p:
        data = json.dumps(p["json"]).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=p.get("method", "GET"), headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=p.get("timeout_s", 15)) as r:
            status, body = r.status, r.read(200_000).decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read(2000).decode(errors="replace")
    except Exception as e:  # noqa: BLE001 — refused/timeout/DNS all mean "down"
        return False, f"{type(e).__name__}: {str(e)[:160]}", None
    dt = time.monotonic() - t0
    if status not in p.get("expect_status", [200]):
        return False, f"HTTP {status} ({dt:.1f}s) {body[:120]!r}", body
    if p.get("expect_body") and not re.search(p["expect_body"], body, re.I | re.S):
        return False, f"HTTP {status} but body did not match /{p['expect_body']}/ ({dt:.1f}s): {body[:160]!r}", body
    if p.get("max_s") and dt > p["max_s"]:
        return False, f"slow: {dt:.1f}s > {p['max_s']}s", body
    return True, f"HTTP {status} {dt:.1f}s", body


def _skip_name(buf, i):
    while True:
        n = buf[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:          # compression pointer
            return i + 2
        i += 1 + n


def _parse_a_records(resp):
    qd, an = struct.unpack(">HH", resp[4:8])
    i = 12
    for _ in range(qd):
        i = _skip_name(resp, i) + 4   # qtype + qclass
    ips = set()
    for _ in range(an):
        i = _skip_name(resp, i)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", resp[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            ips.add(socket.inet_ntoa(resp[i:i + 4]))
        i += rdlen
    return ips


def dns_probe(p):
    """Minimal A-record query straight to one server (no resolver config involved)."""
    qid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(x)]) + x.encode() for x in p["qname"].split(".")) + b"\0" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(p.get("timeout_s", 5))
    try:
        s.sendto(q, (p["server"], 53))
        resp, _ = s.recvfrom(4096)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", None
    finally:
        s.close()
    ips = _parse_a_records(resp)
    if p.get("expect_ip") and p["expect_ip"] not in ips:
        return False, f"{p['qname']} -> {sorted(ips) or 'no A record'} (expected {p['expect_ip']})", None
    return True, f"{p['qname']} -> {sorted(ips)}", None



def tcp_probe(p):
    """Connect, optionally send a request line, optionally match the first reply."""
    try:
        with socket.create_connection((p["host"], int(p["port"])), timeout=p.get("timeout_s", 5)) as s:
            if not p.get("send"):
                return True, f"{p['host']}:{p['port']} accepts connections", None
            s.sendall(p["send"].encode())
            reply = s.recv(4096).decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", None
    if p.get("expect_body") and not re.search(p["expect_body"], reply):
        return False, f"unexpected reply: {reply[:120]!r}", None
    return True, f"{p['host']}:{p['port']} answered", None

def matrix(text, dry):
    if dry:
        log("[dry-run] would post: " + text)
        return True
    if not (MATRIX_HOMESERVER and MATRIX_ROOM_ID and MATRIX_BOT_SECRET):
        log("matrix not configured: " + text)
        return False
    url = (f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{MATRIX_ROOM_ID}"
           f"/send/m.room.message/epprobe-{int(time.time() * 1000)}-{random.randint(0, 9999)}")
    req = urllib.request.Request(url, data=json.dumps({"msgtype": "m.text", "body": text}).encode(), method="PUT",
                                 headers={"Authorization": f"Bearer {MATRIX_BOT_SECRET}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        log(f"matrix post failed: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--group", default="health")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true", help="ignore intervals (run every probe now)")
    a = ap.parse_args()
    probes = [p for p in json.load(open(a.config)) if p.get("group", "health") == a.group
              and (not a.only or p["name"] == a.only)]
    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{a.group}.json")
    try:
        state = json.load(open(state_path))
    except (OSError, ValueError):
        state = {}
    now, failing = time.time(), []
    for p in probes:
        st = state.setdefault(p["name"], {"fails": 0, "last_run": 0, "last_alert": 0, "alerted": False})
        if not a.force and now - st["last_run"] < p.get("interval_s", 60) - 5:
            continue
        st["last_run"] = now
        ok, detail, _ = {"dns": dns_probe, "tcp": tcp_probe}.get(p.get("type"), http_probe)(p)
        what = p.get("what", p["name"])
        if ok:
            if st["alerted"]:
                matrix(f"✅ {p['name']} recovered ({what}): {detail}", a.dry_run)
            if st["fails"]:
                log(f"OK again {p['name']}: {detail}")
            st.update(fails=0, alerted=False)
            continue
        st["fails"] += 1
        log(f"FAIL {st['fails']}/{p.get('threshold', 3)} {p['name']}: {detail}")
        failing.append(p["name"])
        if st["fails"] >= p.get("threshold", 3) and (not st["alerted"] or now - st["last_alert"] >= REPEAT_S):
            icon = "🐤" if a.group == "canary" else "🚨"
            prefix = "Canary question failed" if a.group == "canary" else "Probe failing"
            if matrix(f"{icon} {prefix}: {p['name']} ({what}) — {st['fails']}× in a row. {detail}"
                      + (f"\nCheck: {p['hint']}" if p.get("hint") else ""), a.dry_run):
                st.update(alerted=True, last_alert=now)
    if not a.dry_run:
        tmp = state_path + ".tmp"
        json.dump(state, open(tmp, "w"), indent=1)
        os.replace(tmp, state_path)
    if a.dry_run or a.force:
        log(f"{len(probes)} probes in group {a.group}; failing now: {failing or 'none'}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
