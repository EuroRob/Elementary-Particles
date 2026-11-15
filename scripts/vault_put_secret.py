#!/usr/bin/env python3
"""
vault_put_secret.py — store third-party credentials in Vault without them ever
touching the terminal, shell history, a file, or chat.

Prompts for each key with hidden input (getpass) and writes them to KV v2 at
secret/<path>. Existing keys at that path are kept (merge-patch), unless the path
doesn't exist yet. Prints only key names and the HTTP status, never values.

Reads VAULT_ADDR / VAULT_TOKEN by parsing /opt/vault/.env line by line (no shell
sourcing — see feedback_env_file_shell_sourcing_mangles_values).

Usage:
  python3 IaC/scripts/vault_put_secret.py travel/aviationstack api_key
  python3 IaC/scripts/vault_put_secret.py travel/google_routes api_key
  python3 IaC/scripts/vault_put_secret.py travel/openweathermap api_key
"""
import getpass
import json
import re
import sys
import urllib.error
import urllib.request

ENV_FILE = "/opt/vault/.env"


def vault_env():
    env = {}
    for line in open(ENV_FILE):
        m = re.match(r"^(?:export\s+)?(VAULT_TOKEN|VAULT_ADDR)=(.*)$", line.strip())
        if m:
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1].replace("'\\''", "'")
            env[m.group(1)] = v
    if "VAULT_TOKEN" not in env or "VAULT_ADDR" not in env:
        sys.exit(f"VAULT_ADDR/VAULT_TOKEN not found in {ENV_FILE}")
    return env


def call(env, method, path, body=None, ctype="application/json"):
    req = urllib.request.Request(f"{env['VAULT_ADDR']}/v1/secret/data/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Vault-Token": env["VAULT_TOKEN"], "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, (json.load(r) if method == "GET" else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path, keys = sys.argv[1].removeprefix("secret/"), sys.argv[2:]
    env = vault_env()
    values = {}
    for k in keys:
        v = getpass.getpass(f"{path} -> {k} (input hidden): ").strip()
        if not v:
            sys.exit(f"empty value for {k}; nothing written")
        values[k] = v
    status, existing = call(env, "GET", path)
    if status == 200:
        status, _ = call(env, "PATCH", path, {"data": values}, "application/merge-patch+json")
        action = "merged into existing"
    else:
        status, _ = call(env, "POST", path, {"data": values})
        action = "created"
    print(f"secret/{path}: {action}, keys {sorted(values)} -> HTTP {status}")
    status, d = call(env, "GET", path)
    if status == 200:
        print(f"secret/{path} now has keys: {sorted(d['data']['data'])}")


if __name__ == "__main__":
    main()
