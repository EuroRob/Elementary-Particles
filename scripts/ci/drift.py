#!/usr/bin/env python3
"""Re-validate every open pull request against the *current* base branch.

The `checks` job runs when a PR is pushed; this runs on a schedule and after every
push to master, merges each open PR onto today's master in a scratch worktree and
runs scripts/ci/check.sh there. The verdict lands as commit status `checks-drift`
on the PR head; a PR comment is posted only when the verdict changes.

Runs on the Forgejo runner (code-01) with the job's ephemeral token: nothing here
needs or stores a credential. Environment:
    FORGEJO_API   e.g. http://10.0.30.31:3000/api/v1
    TOKEN         the job token (${{ github.token }})
    REPO          owner/repo
    BASE          base branch name (default master)
    RUN_URL       link for the status (optional)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

API = os.environ["FORGEJO_API"].rstrip("/")
TOKEN = os.environ["TOKEN"]
REPO = os.environ["REPO"]
BASE = os.environ.get("BASE", "master")
RUN_URL = os.environ.get("RUN_URL", "")
CONTEXT = "checks-drift"
CHECK = "scripts/ci/check.sh"


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 headers={"Authorization": f"token {TOKEN}", "Content-Type": "application/json",
                                          "Accept": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r) if r.length != 0 else {}
    except urllib.error.HTTPError as e:
        print(f"   API {method} {path} -> {e.code}: {e.read()[:200]!r}")
        raise


def git(*args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)


def last_drift_status(sha: str) -> dict | None:
    s = api("GET", f"/repos/{REPO}/commits/{sha}/status")
    mine = [x for x in s.get("statuses", []) if x.get("context") == CONTEXT]
    return max(mine, key=lambda x: x.get("created_at", "")) if mine else None


def main() -> int:
    base_sha = git("rev-parse", f"origin/{BASE}").stdout.strip()
    prs = api("GET", f"/repos/{REPO}/pulls?state=open&limit=50")
    print(f"{len(prs)} open PR(s); base {BASE} = {base_sha[:12]}")
    failures = 0
    for pr in prs:
        n, sha = pr["number"], pr["head"]["sha"]
        label = f"#{n} {pr['title'][:60]}"
        prev = last_drift_status(sha)
        if prev and base_sha[:12] in (prev.get("description") or ""):
            print(f"-- {label}: already judged against {base_sha[:12]} ({prev['status']})")
            continue
        print(f"== {label} (head {sha[:12]})")
        git("fetch", "-q", "origin", sha)
        wt = tempfile.mkdtemp(prefix=f"drift-{n}-")
        try:
            git("worktree", "add", "-q", "--detach", wt, f"origin/{BASE}")
            merge = git("-c", "user.name=drift", "-c", "user.email=drift@ci", "merge", "--no-edit", "-q", sha,
                        cwd=wt, check=False)
            if merge.returncode != 0:
                state, desc, detail = "failure", f"vs {BASE} {base_sha[:12]}: merge conflict", merge.stdout + merge.stderr
            else:
                env = dict(os.environ, BASE_REF=f"origin/{BASE}")
                run = subprocess.run([CHECK], cwd=wt, text=True, capture_output=True, env=env)
                out = run.stdout + run.stderr
                failed = [l.strip("= \n") for l, nxt in zip(out.splitlines(), out.splitlines()[1:] + [""])
                          if l.startswith("== ") and "FAILED" in out[out.find(l):].split("\n== ")[0]]
                if run.returncode == 0:
                    state, desc, detail = "success", f"vs {BASE} {base_sha[:12]}: all checks pass", ""
                else:
                    state = "failure"
                    desc = f"vs {BASE} {base_sha[:12]}: {len(failed)} check(s) fail"
                    detail = "\n".join(l for l in out.splitlines() if l.strip() and not l.startswith("   ok"))[-3000:]
        finally:
            git("worktree", "remove", "--force", wt, check=False)
            shutil.rmtree(wt, ignore_errors=True)
        print(f"   {state}: {desc}")
        api("POST", f"/repos/{REPO}/statuses/{sha}",
            {"state": state, "context": CONTEXT, "description": desc[:255], "target_url": RUN_URL})
        prev_state = prev["status"] if prev else None
        if state == "failure" and prev_state != "failure":
            api("POST", f"/repos/{REPO}/issues/{n}/comments",
                {"body": f"**checks-drift**: this branch no longer passes against `{BASE}` @ `{base_sha[:12]}` "
                         f"({desc.split(': ', 1)[1]}). Rebase or merge `{BASE}` and push.\n\n"
                         f"```\n{detail}\n```\n" + (f"[run]({RUN_URL})" if RUN_URL else "")})
        elif state == "success" and prev_state == "failure":
            api("POST", f"/repos/{REPO}/issues/{n}/comments",
                {"body": f"**checks-drift**: passes again against `{BASE}` @ `{base_sha[:12]}`."})
        failures += state == "failure"
    print(f"done: {failures} PR(s) failing against {BASE}")
    return 0   # the job itself is green when it ran; verdicts are on the PRs


if __name__ == "__main__":
    sys.exit(main())
