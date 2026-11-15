# Claude session records

Added 2026-10-25. the owner's ask: keep the interactions, decisions and attempts with Claude Code so
that a question months later ("how is the Nextcloud hardening looking, is there an attack going
on?") leads straight back to the work and the reasoning. Code stays in this repo (git history);
the conversation layer goes to a separate private repo.

## Decisions (owner, 2026-10-25)

| Question | Decision |
|---|---|
| Retention | Keep forever: `cleanupPeriodDays: 36500` in `~/.claude/settings.json` (default 30 deleted transcripts) |
| Where records live | New private Forgejo repo `owner/claude-sessions`, local clone `~/GitLab/claude-sessions` |
| Search | Records + grep now; MemPalace (AGENT_MEMORY_ARCHITECTURE.md phase 2) next |
| Outline | Keepsakes only, as before -- records are not synced to Outline |

## Layers

| Layer | Where | Who can read |
|---|---|---|
| Raw transcripts (`*.jsonl`, complete) | `~/.claude/projects/` (IaC VM, 0700) + `nas-02:/DATAGRID/archive/claude-sessions/raw` | root on nas-02 only (0700/0600) |
| Session records (readable, scrubbed) | `owner/claude-sessions` `records/YYYY/MM/` | owner |
| Digests (gpu01-primary, local) | `owner/claude-sessions` `digests/` + top of each record | owner |
| Memory snapshot | `owner/claude-sessions` `memory/` | owner |

The raw transcripts **contain live secrets** that tool output printed in past sessions (FOUND
2026-10-25: the iac-api token, the pg-01 app password, n8n keys, the HA token and others are in
there). That is why the archive is root-only, and why records never include tool output.

## How it runs

`claude-session-records.timer` (user unit, 01:30 Europe/Berlin -- before vzdump 03:00 and the
05:00 patch window, after the 22:00 wind-down that uses gpu01-primary) ->
`IaC/scripts/claude-session-records.sh`:

1. `rsync` to nas-02 with `--chmod=D0700,F0600 --chown=root:root`, never `--delete`.
2. `claude_session_records.py` -- parses each transcript and writes the record (prompts, replies,
   AskUserQuestion answers as decisions, git commit lines, files edited, one line per tool call
   from its description), the digest (cached by input hash, retried next night if gpu-01 is
   down), `INDEX.md` and the memory snapshot; commits and pushes only when something changed.

Scrubbing, two nets: shape rules (keys, Vault tokens, bearer, bcrypt, `password=...`, long
mixed-case tokens, 64+ hex) and every Vault secret value matched verbatim (identifier fields like
`url`, `user`, `token_id` are left readable). Without Vault credentials the script refuses to
commit.

The service runs the script from `~/GitLab/GitOps-nightly`, a detached worktree that its
`ExecStartPre` moves to `origin/master` each night -- never from `~/GitLab/GitOps`. FIXED
2026-10-30: the unit pointed into `~/GitLab/GitOps`, the script lived only on an unmerged branch,
and every night 10-26..10-30 failed with 203/EXEC because another branch was checked out; nobody
noticed. Now a failure triggers `claude-session-records-alert.service`, one Matrix notice in
"🚨 System Alerts" (via `patch_notify.send`, as @podcast-bot; no log lines in the notice).

Install / reinstall:

```bash
git -C ~/GitLab/GitOps worktree add --detach ~/GitLab/GitOps-nightly origin/master   # once
cp IaC/scripts/claude-session-records{,-alert}.service IaC/scripts/claude-session-records.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now claude-session-records.timer
systemctl --user start claude-session-records.service   # run now; journalctl --user -u claude-session-records
```

Never check out a branch in `~/GitLab/GitOps-nightly`; the next run discards it.

## Finding something later

```bash
cd ~/GitLab/claude-sessions
grep -ril "crowdsec" records/ digests/ | sort      # which sessions
less INDEX.md                                       # by date + keywords
```

Example (2026-10-25): "crowdsec|ddos|brute force" ranks the OPNsense IDS session (10-22), the
bouncer stale-alert fix (10-25) and the PHP patch (10-22) at the top.
