#!/usr/bin/env python3
"""
n8n_export_workflows.py — export every n8n workflow to git-friendly, secret-free JSON.

Why: n8n keeps flows only in its own database (n8n DB on pg-01), so changes made in
the UI or via the API (e.g. Echo's TRAVEL branch, 2026-10-03) were never versioned.
This writes one file per workflow to n8n/workflows/ — review + commit by hand.

Secret handling (IaC/CLAUDE.md guardrail 2 — never commit secrets):
  - values under secret-sounding keys (authorization, token, api_key, password, ...)
    and header/query parameter entries with such names are replaced by "<REDACTED>"
  - token-shaped strings anywhere (Bearer ..., JWTs, sk-..., wanderer_key_..., al_...)
    are replaced too
  - n8n expressions (strings starting with "=") are kept — they reference credentials
    or other nodes, not literal secrets
  - only the LOCATION of each redaction is printed, never the value
Credentials themselves are never in workflow JSON (nodes reference them by id/name).

Volatile fields are dropped (versionId, timestamps, pinData, staticData, shared,
activeVersion...) and keys are sorted, so diffs show real changes only.

Auth: N8N_API_KEY env, or Vault secret/n8n/api:api_key via /opt/vault/.env (parsed,
not shell-sourced).

Usage: python3 IaC/scripts/n8n_export_workflows.py [--out DIR] [--check]
  --check   exit 1 if any exported file would change (drift detection), write nothing
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

N8N_API = os.environ.get("N8N_API", "https://n8n.home.example.org/api/v1")
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "n8n" / "workflows" / "live"

SECRET_KEY_RE = re.compile(r"(authorization|api[-_]?key|apikey|x-api-key|token|secret|password|passwd|"
                           r"bearer|access[-_]?key|private[-_]?key|client[-_]?secret|cookie|session)", re.I)
TOKEN_RES = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),   # JWT
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bwanderer_key_[A-Za-z0-9]{16,}"),
    re.compile(r"\bal_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsyt_[A-Za-z0-9_]{10,}"),                                              # Matrix/Synapse
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\b(xox[abp]-[A-Za-z0-9-]{10,})"),
]
DROP_TOP = {"versionId", "updatedAt", "createdAt", "pinData", "staticData", "shared", "activeVersion",
            "activeVersionId", "versionCounter", "triggerCount", "meta", "isArchived", "homeProject",
            "parentFolder", "scopes", "usedCredentials"}
KEEP_TOP = ("id", "name", "active", "tags", "settings", "nodes", "connections")


def api_key():
    if os.environ.get("N8N_API_KEY"):
        return os.environ["N8N_API_KEY"]
    env = {}
    for line in open("/opt/vault/.env"):
        m = re.match(r"^(?:export\s+)?(VAULT_TOKEN|VAULT_ADDR)=(.*)$", line.strip())
        if m:
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            env[m.group(1)] = v
    req = urllib.request.Request(env["VAULT_ADDR"] + "/v1/secret/data/n8n/api",
                                 headers={"X-Vault-Token": env["VAULT_TOKEN"]})
    return json.load(urllib.request.urlopen(req, timeout=15))["data"]["data"]["api_key"]


def get(path, key):
    req = urllib.request.Request(N8N_API + path, headers={"X-N8N-API-KEY": key, "Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def scrub_str(s, where, hits):
    if s.startswith("="):  # n8n expression -> references, not literals
        out = s
        for rx in TOKEN_RES:  # ...but a literal token pasted INTO an expression still counts
            if rx.search(out):
                out = rx.sub("<REDACTED>", out)
                hits.append(where)
        return out
    for rx in TOKEN_RES:
        if rx.search(s):
            s = rx.sub("<REDACTED>", s)
            hits.append(where)
    return s


def scrub(obj, where, hits):
    if isinstance(obj, dict):
        # header/query/body parameter lists: [{name: Authorization, value: ...}]
        if "name" in obj and "value" in obj and isinstance(obj.get("name"), str) \
                and SECRET_KEY_RE.search(obj["name"]) and isinstance(obj["value"], str) \
                and obj["value"] and not obj["value"].startswith("="):
            hits.append(f"{where}[{obj['name']}]")
            obj = {**obj, "value": "<REDACTED>"}
        out = {}
        for k, v in obj.items():
            w = f"{where}.{k}"
            if isinstance(v, str) and v and SECRET_KEY_RE.search(k) and not v.startswith("=") \
                    and k not in ("authentication", "genericAuthType", "nodeCredentialType", "sendHeaders"):
                out[k] = "<REDACTED>"
                hits.append(w)
            else:
                out[k] = scrub(v, w, hits)
        return out
    if isinstance(obj, list):
        return [scrub(v, f"{where}[{i}]", hits) for i, v in enumerate(obj)]
    if isinstance(obj, str):
        return scrub_str(obj, where, hits)
    return obj


def normalise(w):
    w = {k: v for k, v in w.items() if k in KEEP_TOP}
    w["tags"] = sorted((t.get("name") for t in w.get("tags") or []), key=str)
    w["nodes"] = sorted(w.get("nodes") or [], key=lambda n: n.get("name", ""))
    return w


def slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:60] or "workflow"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    key = api_key()
    items, cursor = [], None
    while True:
        page = get("/workflows?limit=100" + (f"&cursor={cursor}" if cursor else ""), key)
        items += page["data"]
        cursor = page.get("nextCursor")
        if not cursor:
            break
    wanted, changed, total_hits = {}, [], 0
    archived = [m["name"] for m in items if m.get("isArchived")]
    for meta in items:
        if meta.get("isArchived"):      # archived in n8n = not live; keep it out of live/
            continue
        w = get(f"/workflows/{meta['id']}", key)
        hits = []
        clean = scrub(normalise(w), w["name"], hits)
        total_hits += len(hits)
        fn = f"{slug(w['name'])}__{w['id']}.json"
        text = json.dumps(clean, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        wanted[fn] = text
        p = out / fn
        if not p.exists() or p.read_text() != text:
            changed.append(fn)
        flag = f"  [{len(hits)} redaction(s): {', '.join(sorted(set(h.split('.', 1)[-1][:70] for h in hits))[:4])}]" if hits else ""
        print(f"{'*' if fn in changed else ' '} {'ACTIVE ' if w.get('active') else 'inactive'} {fn}{flag}")
    stale = sorted(p.name for p in out.glob("*.json") if p.name not in wanted) if out.exists() else []
    print(f"\n{len(items)} workflows ({len(archived)} archived, skipped), {len(changed)} new/changed, {len(stale)} no longer in n8n, "
          f"{total_hits} secret redaction(s)")
    if a.check:
        return 1 if changed or stale else 0
    out.mkdir(parents=True, exist_ok=True)
    for fn in changed:
        (out / fn).write_text(wanted[fn])
    for fn in stale:
        (out / fn).unlink()
    (out / "README.md").write_text(
        "# n8n workflows (live export)\n\nGenerated by `IaC/scripts/n8n_export_workflows.py` from the running "
        "n8n — **do not edit by hand**; change the workflow in n8n, re-export, review the diff, commit.\n"
        "Secrets are redacted (`<REDACTED>`); credentials are referenced by id/name only. Restore a flow with "
        "the n8n API (POST /api/v1/workflows) after re-entering any redacted literal.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
