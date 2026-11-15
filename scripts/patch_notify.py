#!/usr/bin/env python3
"""
patch_notify.py -- one Matrix notice per patch window, in "🚨 System Alerts" (2026-10-16).

Called at the end of patch_window.sh with the window's log stamp. Reads that window's
logs (~/patch-logs/<stamp>-{summary,queue,vms,containers}.log), and posts:

  - always on a queue day (Saturdays) and whenever anything failed, rolled back or
    could not run;
  - otherwise only when something was actually patched or updated. A quiet morning
    (nothing pending, all containers on their newest image) posts nothing.

Posts as @podcast-bot (Vault secret/family-messaging/bot), already a member of the
room. The token is read from Vault and never printed. Dry runs post nothing unless
PATCH_NOTIFY_DRY_RUN=1 (then the notice is marked as a dry run).

Usage: patch_notify.py <stamp> <queue_rc> <vm_rc> <container_rc> [--print]
  --print   show the notice instead of sending it (for testing)
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

LOGDIR = os.path.expanduser("~/patch-logs")
ROOM = os.environ.get("PATCH_NOTIFY_ROOM", "!EXAMPLEROOMID0000:home.example.org")   # 🚨 System Alerts
VAULT_PATH = "secret/data/family-messaging/bot"
MSG = re.compile(r'"msg": "(.*)"\s*$')


def _messages(path: str) -> list[str]:
    try:
        text = open(path, errors="replace").read()
    except OSError:
        return []
    return [m.group(1).replace('\\"', '"') for m in (MSG.search(l) for l in text.splitlines()) if m]


def summarize(vm_msgs: list[str], ct_msgs: list[str]) -> dict:
    """The window's outcome from the playbooks' own messages (pure; tested)."""
    out = {"patched": [], "nothing": [], "skipped": [], "rolled_back": [], "gate": [],
           "ct_updated": [], "ct_rolled_back": []}
    for m in vm_msgs:
        if (g := re.match(r"✓ (\S+) \(VM \d+\) patched successfully\. apt=(\d+)", m)):
            out["patched"].append(f"{g[1]} ({g[2]} pkgs)")
        elif (g := re.match(r"(\S+): apt=0 upgradable pkgs, docker=0 image updates, will_patch=False", m)):
            out["nothing"].append(g[1])
        elif (g := re.match(r"SKIP (\S+): (unreachable|health check fails)", m)):
            out["skipped"].append(f"{g[1]} ({'off/unreachable' if g[2] == 'unreachable' else 'its health check fails before patching'})")
        elif (g := re.match(r"Health checks FAILED for (\S+) after patching", m)):
            out["rolled_back"].append(g[1])
        elif (g := re.match(r"(\S+): newest backup .* -> SKIP", m)):
            out["gate"].append(g[1])
    for m in ct_msgs:
        if (g := re.match(r"(\S+) updated: (.+?)\. Rollback images kept", m)):
            out["ct_updated"].append(f"{g[1]} ({g[2]})")
        elif (g := re.match(r"(\S+) failed validation and was rolled back", m)):
            out["ct_rolled_back"].append(g[1])
    return out


def notice(s: dict, queue_ran: bool, rcs: tuple[int, int, int], dry: bool = False) -> Optional[str]:
    """The notice text, or None when there is nothing worth telling (pure; tested)."""
    q_rc, vm_rc, ct_rc = rcs
    bad = s["rolled_back"] or s["ct_rolled_back"] or any(rcs)
    if not (queue_ran or bad or s["patched"] or s["ct_updated"]):
        return None
    icon = "⚠️" if bad else "✅"
    head = f"{icon} Patch window{' (DRY RUN)' if dry else ''}"
    head += f": {len(s['patched'])} VM{'s' if len(s['patched']) != 1 else ''} patched"
    if s["rolled_back"]:
        head += f", {len(s['rolled_back'])} rolled back"
    if s["skipped"] or s["gate"]:
        head += f", {len(s['skipped']) + len(s['gate'])} waiting"
    lines = [head]
    if queue_ran:
        lines.append("Weekly queue: every patchable VM set pending" + ("" if q_rc == 0 else f" -- FAILED (rc={q_rc})"))
    if s["patched"]:
        lines.append("Patched: " + ", ".join(s["patched"]))
    if s["rolled_back"]:
        lines.append("ROLLED BACK to the pre-patch snapshot (writes since then are gone): " + ", ".join(s["rolled_back"]))
    if s["skipped"]:
        lines.append("Waiting, retried tomorrow: " + ", ".join(s["skipped"]))
    if s["gate"]:
        lines.append("Waiting for a fresh backup: " + ", ".join(s["gate"]))
    if s["nothing"]:
        lines.append(f"Already current: {len(s['nothing'])}")
    if s["ct_updated"]:
        lines.append("Containers updated: " + ", ".join(s["ct_updated"]))
    if s["ct_rolled_back"]:
        lines.append("Containers ROLLED BACK: " + ", ".join(s["ct_rolled_back"]))
    if vm_rc or ct_rc:
        lines.append(f"Exit codes: VMs {vm_rc}, containers {ct_rc}. Logs: ~/patch-logs/ on the IaC VM.")
    return "\n".join(lines)


def _vault(path: str) -> dict:
    req = urllib.request.Request(f"{os.environ['VAULT_ADDR'].rstrip('/')}/v1/{path}",
                                 headers={"X-Vault-Token": os.environ["VAULT_TOKEN"]})
    return json.load(urllib.request.urlopen(req, timeout=10))["data"]["data"]


def send(text: str) -> None:
    bot = _vault(VAULT_PATH)
    txn = f"patch-{int(time.time() * 1000)}"
    url = (f"{bot['homeserver'].rstrip('/')}/_matrix/client/v3/rooms/{urllib.parse.quote(ROOM, safe='')}"
           f"/send/m.room.message/{txn}")
    body = json.dumps({"msgtype": "m.notice", "body": text}).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
                                 headers={"Authorization": f"Bearer {bot['access_token']}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(__doc__)
        return 2
    stamp, q_rc, vm_rc, ct_rc = argv[1], int(argv[2]), int(argv[3]), int(argv[4])
    dry = stamp.endswith("-DRYRUN")
    if dry and os.environ.get("PATCH_NOTIFY_DRY_RUN") != "1" and "--print" not in argv:
        return 0
    queue_ran = os.path.exists(os.path.join(LOGDIR, f"{stamp}-queue.log")) or \
        "queue day:" in open(os.path.join(LOGDIR, f"{stamp}-summary.log"), errors="replace").read()
    s = summarize(_messages(os.path.join(LOGDIR, f"{stamp}-vms.log")), _messages(os.path.join(LOGDIR, f"{stamp}-containers.log")))
    text = notice(s, queue_ran, (q_rc, vm_rc, ct_rc), dry)
    if text is None:
        return 0
    if "--print" in argv:
        print(text)
        return 0
    send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
