#!/usr/bin/env python3
"""Apply a desired Syncthing state (JSON on stdin) through the local REST API.

Idempotent: reads the live config, changes only what differs, prints CHANGED
lines for Ansible. Installed by Roles/syncthing; run as the syncthing user.
Desired state: {"options": {...}, "gui": {...}, "devices": [{deviceID, name}],
"folders": [{id, label, path, type, ignoreDelete, ignorePerms, versioning, devices: [ids]}]}.
Prints this device's ID as `MYID <id>`. Never prints the API key.
"""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def api_key() -> str:
    home = os.path.expanduser("~")
    for p in [f"{home}/.local/state/syncthing/config.xml", f"{home}/.config/syncthing/config.xml"] + \
            glob.glob(f"{home}/**/syncthing/config.xml", recursive=True):
        if os.path.exists(p):
            return ET.parse(p).getroot().findtext("gui/apikey")
    sys.exit("no syncthing config.xml found")


KEY = api_key()
BASE = "http://127.0.0.1:8384"


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def same(live, want) -> bool:
    """want is a subset of live (nested dicts compared key by key: the API adds fields)."""
    if isinstance(want, dict) and isinstance(live, dict):
        return all(same(live.get(k), v) for k, v in want.items())
    return live == want


def differs(live: dict, want: dict) -> dict:
    return {k: v for k, v in want.items() if not same(live.get(k), v)}


def main() -> None:
    want = json.load(sys.stdin)
    for _ in range(30):
        try:
            call("GET", "/rest/system/ping")
            break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(2)
    for section in ("options", "gui"):
        if want.get(section):
            change = differs(call("GET", f"/rest/config/{section}"), want[section])
            if change:
                call("PATCH", f"/rest/config/{section}", change)
                print(f"CHANGED {section}: {', '.join(sorted(change))}")
    live_devices = {d["deviceID"]: d for d in call("GET", "/rest/config/devices")}
    for d in want.get("devices", []):
        if d["deviceID"] not in live_devices:
            call("PUT", f"/rest/config/devices/{d['deviceID']}", {"addresses": ["dynamic"], **d})
            print(f"CHANGED device added: {d['name']}")
        elif differs(live_devices[d["deviceID"]], d):
            call("PATCH", f"/rest/config/devices/{d['deviceID']}", d)
            print(f"CHANGED device updated: {d['name']}")
    me = call("GET", "/rest/system/status")["myID"]
    live_folders = {f["id"]: f for f in call("GET", "/rest/config/folders")}
    for f in want.get("folders", []):
        devs = [{"deviceID": me}] + [{"deviceID": x} for x in f.pop("devices", [])]
        f["devices"] = devs
        cur = live_folders.get(f["id"])
        if cur is None:
            call("PUT", f"/rest/config/folders/{f['id']}", f)
            print(f"CHANGED folder added: {f['id']}")
            continue
        change = differs(cur, {k: v for k, v in f.items() if k != "devices"})
        have = {d["deviceID"] for d in cur.get("devices", [])}
        if {d["deviceID"] for d in devs} - have:
            change["devices"] = cur.get("devices", []) + [d for d in devs if d["deviceID"] not in have]
        if change:
            call("PATCH", f"/rest/config/folders/{f['id']}", change)
            print(f"CHANGED folder {f['id']}: {', '.join(sorted(change))}")
    print(f"MYID {me}")


if __name__ == "__main__":
    main()
