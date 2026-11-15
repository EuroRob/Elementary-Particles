#!/usr/bin/env python3
"""Agent contribution metrics from git history (and, optionally, pull requests).

    agent_metrics.py                      # git-only metrics for the current repo
    agent_metrics.py --prs prs.json       # add PR cycle-time metrics (Forgejo/GitHub JSON, see below)
    agent_metrics.py --markdown > docs/AGENT_METRICS.md

Agent commits are identified by a `Co-Authored-By:` trailer containing "Claude" (the
convention Claude Code writes). Everything is derived from the repository itself, so
anyone with a clone can rerun it. Numbers are indicators, not proof of causation:
"followed by a fix" means a later commit whose subject says fix/revert/correct touched
one of the same files within N days, whatever the reason.

`--prs` expects a JSON list of objects with: number, merged (bool), created_at,
merged_at, changed_files, merge_commit_sha (the shape `/repos/{owner}/{repo}/pulls`
returns on Forgejo and GitHub, trimmed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import subprocess
from collections import defaultdict

FIX = re.compile(r"\b(fix|fixes|fixed|hotfix|revert|correct|corrected|repair|broke|bug)\b", re.I)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def load_commits(branch: str) -> list[dict]:
    raw = git("log", branch, "--format=%x1e%H|%ct|%s|%(trailers:key=Co-Authored-By,valueonly,separator=;)", "--name-only")
    commits = []
    for block in raw.split("\x1e")[1:]:
        head, *files = block.strip("\n").split("\n")
        sha, ts, subject, coauthors = head.split("|", 3)
        commits.append({
            "sha": sha, "t": int(ts), "subject": subject,
            "agent": "claude" in coauthors.lower(),
            "files": {f for f in files if f.strip()},
        })
    commits.sort(key=lambda c: c["t"])
    return commits


def git_metrics(commits: list[dict], window_days: int) -> dict:
    agent = [c for c in commits if c["agent"]]
    first_agent = min((c["t"] for c in agent), default=None)
    since = [c for c in commits if first_agent and c["t"] >= first_agent]
    # compare like with like: human-only commits from the same period as the agent commits
    human = [c for c in since if not c["agent"]]
    by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in commits:
        m = dt.datetime.fromtimestamp(c["t"], dt.UTC).strftime("%Y-%m")
        by_month[m][0] += 1
        by_month[m][1] += c["agent"]

    win = window_days * 86400

    def fixes_after(c: dict) -> list[dict]:
        return [d for d in commits if c["t"] < d["t"] <= c["t"] + win and FIX.search(d["subject"]) and d["files"] & c["files"]]

    followed = followed_by_human = 0
    for c in agent:
        fx = fixes_after(c)
        if fx:
            followed += 1
            if any(not d["agent"] for d in fx):
                followed_by_human += 1
    followed_h = sum(1 for c in human if fixes_after(c))

    reverts = [c for c in commits if c["subject"].lower().startswith("revert")]
    reverted_agent = sum(1 for r in reverts if any(a["subject"][:40] and a["subject"][:40] in r["subject"] for a in agent))
    return {
        "commits_total": len(commits),
        "commits_agent": len(agent),
        "commits_since_first_agent": len(since),
        "agent_share_since_first_agent": round(100 * len(agent) / len(since), 1) if since else None,
        "first_agent_commit": dt.datetime.fromtimestamp(first_agent, dt.UTC).date().isoformat() if first_agent else None,
        "followed_by_fix_within_days": window_days,
        "agent_commits_followed_by_fix_pct": round(100 * followed / len(agent), 1) if agent else None,
        "agent_commits_followed_by_human_fix_pct": round(100 * followed_by_human / len(agent), 1) if agent else None,
        "human_commits_in_period": len(human),
        "human_commits_followed_by_fix_pct": round(100 * followed_h / len(human), 1) if human else None,
        "reverts_total": len(reverts),
        "reverts_of_agent_commits": reverted_agent,
        "by_month": {m: {"commits": v[0], "agent": v[1]} for m, v in sorted(by_month.items())},
    }


def pr_metrics(prs: list[dict], commits: list[dict]) -> dict:
    by_sha = {c["sha"]: c for c in commits}
    parse = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    merged = [p for p in prs if p.get("merged")]
    hours = [(parse(p["merged_at"]) - parse(p["created_at"])).total_seconds() / 3600 for p in merged]
    inside = agent_inside = prs_with_agent = 0
    for p in merged:
        sha = p.get("merge_commit_sha")
        if not sha or sha not in by_sha:
            continue
        parents = git("rev-list", "--parents", "-n1", sha).split()
        if len(parents) < 3:
            continue
        shas = git("rev-list", f"{parents[1]}..{parents[2]}").split()
        n = len(shas)
        a = sum(1 for s in shas if by_sha.get(s, {}).get("agent"))
        inside += n
        agent_inside += a
        prs_with_agent += bool(a)
    return {
        "prs_total": len(prs),
        "prs_merged": len(merged),
        "prs_closed_unmerged": sum(1 for p in prs if not p.get("merged") and p.get("state") == "closed"),
        "merge_hours_median": round(statistics.median(hours), 1) if hours else None,
        "merge_hours_p75": round(sorted(hours)[int(0.75 * len(hours))], 1) if hours else None,
        "merged_same_day_pct": round(100 * sum(h < 24 for h in hours) / len(hours), 1) if hours else None,
        "changed_files_median": statistics.median([p.get("changed_files") or 0 for p in merged]) if merged else None,
        "commits_in_merged_prs": inside,
        "agent_commits_in_merged_prs_pct": round(100 * agent_inside / inside, 1) if inside else None,
        "merged_prs_with_agent_commit": prs_with_agent,
    }


def markdown(g: dict, p: dict | None) -> str:
    out = ["# Agent contribution metrics", "",
           f"Generated by `scripts/agent_metrics.py` on {dt.date.today().isoformat()}. "
           "Agent commits carry a `Co-Authored-By: Claude …` trailer; a person reviewed and merged every one of them.", "",
           "| Metric | Value |", "| --- | --- |",
           f"| Commits on the main branch | {g['commits_total']} |",
           f"| Agent co-authored commits | {g['commits_agent']} |",
           f"| First agent commit | {g['first_agent_commit']} |",
           f"| Agent share of commits since then | {g['agent_share_since_first_agent']} % |",
           f"| Agent commits followed within {g['followed_by_fix_within_days']} days by a fix/revert touching the same files | {g['agent_commits_followed_by_fix_pct']} % |",
           f"| ... of which the fix came from a person, not the agent | {g['agent_commits_followed_by_human_fix_pct']} % |",
           f"| Human-only commits in the same period ({g['human_commits_in_period']}) followed by a fix | {g['human_commits_followed_by_fix_pct']} % |",
           f"| Reverts (total / of agent commits) | {g['reverts_total']} / {g['reverts_of_agent_commits']} |"]
    if p:
        out += [f"| Pull requests (merged / closed without merge) | {p['prs_merged']} / {p['prs_closed_unmerged']} |",
                f"| Merged PRs containing at least one agent commit | {p['merged_prs_with_agent_commit']} of {p['prs_merged']} |",
                f"| Agent share of commits inside merged PRs | {p['agent_commits_in_merged_prs_pct']} % |",
                f"| PR open → merge, median / p75 (hours) | {p['merge_hours_median']} / {p['merge_hours_p75']} |",
                f"| PRs merged within 24 h | {p['merged_same_day_pct']} % |",
                f"| Changed files per PR, median | {p['changed_files_median']} |"]
    out += ["", "## Commits per month", "", "| Month | Commits | Agent co-authored |", "| --- | --- | --- |"]
    out += [f"| {m} | {v['commits']} | {v['agent']} |" for m, v in g["by_month"].items()]
    out += ["", "## What changed because of this", "",
            "The first run of these numbers (2026-11-14) showed about a third of agent commits followed within a "
            "week by a fix on the same files, a six-minute median from pull request to merge, and review comments "
            "on 2 of 68 pull requests: review happened in the agent session, and nothing ran the tests or any "
            "validation automatically. In response, the repository got a Forgejo Actions pipeline on its own "
            "isolated runner (`scripts/ci/check.sh`: YAML, Python, shell, Terraform formatting, Ansible syntax, "
            "300 offline tests, secret scan on every pull request) and a pull-request template that asks for what "
            "changed, what ran, known risks and what needs a human look. The next recomputation is the test of "
            "whether the follow-up-fix rate drops; first-pass validation (did the checks pass on the first push) "
            "becomes measurable from the pipeline's history.", ""]
    out += ["## How to read this", "",
            "- The follow-up-fix rate is an upper bound on rework: it counts any later fix on the same files, "
            "whether or not the agent's change caused it. Almost all of those fixes are agent commits too, i.e. the "
            "agent iterating on its own work inside the same task; the row below it counts only fixes a person "
            "made afterwards, which is the closer measure of defects that got past the session.",
            "- The human-only rate is not a like-for-like baseline: those commits are few, and many are large "
            "catch-up commits whose files were touched again later for unrelated reasons. It is shown for "
            "completeness, not as a ratio to quote.",
            "- Fast merges reflect a one-reviewer repository: the reviewer is the person who asked for the change "
            "and read it as it was produced. The number to watch over time is reverts of agent commits.",
            "- Pull-request figures need `--prs` with an export from the git host's API; the git-only figures work on any clone."]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--branch", default="HEAD")
    ap.add_argument("--prs", help="JSON file with pull requests (see module docstring)")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    commits = load_commits(args.branch)
    g = git_metrics(commits, args.window_days)
    p = None
    if args.prs:
        with open(args.prs) as fh:
            prs = json.load(fh)
        for pr in prs:  # accept the trimmed keys this repo's export used
            pr.setdefault("created_at", pr.get("created"))
            pr.setdefault("merge_commit_sha", pr.get("merge_sha"))
            pr.setdefault("number", pr.get("n"))
        p = pr_metrics(prs, commits)
    if args.markdown:
        print(markdown(g, p), end="")
    else:
        print(json.dumps({"git": g, "prs": p}, indent=1))


if __name__ == "__main__":
    main()
