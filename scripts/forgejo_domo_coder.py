#!/usr/bin/env python3
"""
Forgejo side of DOMO's code partner (IaC/docs/CODE_PARTNER.md, "Forgejo").

  1. bot user `domo-coder`: restricted (sees only repos it is added to), not admin
  2. collaborator on owner/GitOps with write (it pushes domo/* branches, opens PRs)
  3. branch protection on master: only `owner` may push or merge
  4. an API key for domo-coder (scopes write:repository, read:user) -> Vault
     secret/code-partner/forgejo (forgejo_key); its password -> same path
     (password, for recovery only)
  5. the router key domo-router and code-partner share -> Vault
     secret/code-partner/router (router_key), if not there yet

Default is a dry run that prints what it would do. Nothing secret is ever
printed: generated values go straight into Vault.

    set -a; source /opt/vault/.env; set +a      # in a subshell
    python3 scripts/forgejo_domo_coder.py           # plan
    python3 scripts/forgejo_domo_coder.py --apply   # do it
"""

import argparse
import os
import secrets
import sys

import requests

FORGEJO = os.environ.get("FORGEJO_URL", "http://git-01.internal:3000").rstrip("/")
REPO = "owner/GitOps"
BOT = "domo-coder"
OWNER_LOGIN = "owner"
VAULT = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "")


def vault_get(path: str) -> dict:
    r = requests.get(f"{VAULT}/v1/secret/data/{path}", headers={"X-Vault-Token": VAULT_TOKEN}, timeout=10)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()["data"]["data"]


def vault_put(path: str, data: dict) -> None:
    """Merges into the existing secret (KV v2 patch semantics done by hand)."""
    merged = {**vault_get(path), **data}
    r = requests.post(f"{VAULT}/v1/secret/data/{path}", headers={"X-Vault-Token": VAULT_TOKEN},
                      json={"data": merged}, timeout=10)
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not VAULT_TOKEN:
        sys.exit("VAULT_TOKEN not set (source /opt/vault/.env in a subshell first)")
    admin = vault_get("forgejo/admin")
    s = requests.Session()
    s.auth = (admin["username"], admin["password"])
    api = f"{FORGEJO}/api/v1"
    plan: list[str] = []

    def step(msg: str, fn) -> None:
        plan.append(msg)
        print(("DO   " if args.apply else "PLAN ") + msg)
        if args.apply:
            fn()

    # 1. user
    exists = s.get(f"{api}/users/{BOT}", timeout=10).status_code == 200
    password = secrets.token_urlsafe(32)
    if not exists:
        def create_user():
            r = s.post(f"{api}/admin/users", json={
                "username": BOT, "email": f"{BOT}@home.example.org", "password": password,
                "must_change_password": False, "send_notify": False, "visibility": "private",
                "full_name": "DOMO code partner"}, timeout=10)
            r.raise_for_status()
            s.patch(f"{api}/admin/users/{BOT}", json={"login_name": BOT, "source_id": 0, "restricted": True,
                                                      "admin": False}, timeout=10).raise_for_status()
            vault_put("code-partner/forgejo", {"password": password, "username": BOT})
        step(f"create user {BOT} (restricted, not admin); password -> Vault code-partner/forgejo", create_user)
    else:
        print(f"OK   user {BOT} exists")

    # 2. collaborator
    is_collab = s.get(f"{api}/repos/{REPO}/collaborators/{BOT}", timeout=10).status_code == 204
    if not is_collab:
        step(f"add {BOT} to {REPO} with write",
             lambda: s.put(f"{api}/repos/{REPO}/collaborators/{BOT}", json={"permission": "write"},
                           timeout=10).raise_for_status())
    else:
        print(f"OK   {BOT} is a collaborator")

    # 3. master protection
    rules = s.get(f"{api}/repos/{REPO}/branch_protections", timeout=10).json()
    rule = {"rule_name": "master", "enable_push": True, "enable_push_whitelist": True,
            "push_whitelist_usernames": [OWNER_LOGIN], "enable_merge_whitelist": True,
            "merge_whitelist_usernames": [OWNER_LOGIN], "block_on_outdated_branch": False}
    if not any((r.get("rule_name") or r.get("branch_name")) == "master" for r in rules):
        step(f"protect master: push + merge only by {OWNER_LOGIN}",
             lambda: s.post(f"{api}/repos/{REPO}/branch_protections", json=rule, timeout=10).raise_for_status())
    else:
        print("OK   master already has a protection rule (not changed; check it lists only owner)")

    # 4. bot API key (needs the bot's own login; only on first creation or with the stored password)
    have_key = bool(vault_get("code-partner/forgejo").get("forgejo_key"))
    if not have_key:
        def make_key():
            pw = vault_get("code-partner/forgejo").get("password")
            if not pw:
                sys.exit(f"no stored password for {BOT}; reset it in the Forgejo admin UI, store it at "
                         "code-partner/forgejo:password, and run again")
            r = requests.post(f"{api}/users/{BOT}/tokens", auth=(BOT, pw), timeout=10, json={
                "name": "code-partner", "scopes": ["write:repository", "read:user"]})
            r.raise_for_status()
            vault_put("code-partner/forgejo", {"forgejo_key": r.json()["sha1"]})
        step(f"create API key for {BOT} (write:repository, read:user) -> Vault code-partner/forgejo:forgejo_key",
             make_key)
    else:
        print("OK   Vault code-partner/forgejo has forgejo_key")

    # 5. router key
    if not vault_get("code-partner/router").get("router_key"):
        step("generate router key -> Vault code-partner/router:router_key",
             lambda: vault_put("code-partner/router", {"router_key": secrets.token_urlsafe(32)}))
    else:
        print("OK   Vault code-partner/router has router_key")

    if not args.apply and plan:
        print("\nDry run. Re-run with --apply to make these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
