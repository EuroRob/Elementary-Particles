# Code partner: DOMO at the workbench

**Status (2026-11-05): LIVE** on code-01 (VM 252, pve-01, 10.10.30.32) at code.home.example.org and by voice
through DOMO. Tested end to end through domo-router: a sentence became a diff, a commit and a draft PR
opened by `domo-coder`. That was test PR #60, closed and its branch deleted. 88 unit tests.

A coding partner that runs entirely in the house:
- the model is DOMO's (`gpu01-primary` on gpu-01);
- the repo is a clone on its own VM;
- the voice is DOMO's (phone → gateway → domo-router).

You talk; it reads the repo, proposes a change, and tells you what it would do. The diff is on
**code.home.example.org**. You say "apply change 1", it commits on its own branch and pushes that branch to Forgejo; "open the pull request", and a draft
pull request is waiting in Forgejo. You merge it, or you don't.

No VS Code is needed on the Mac:
- voice works from the DOMO app;
- the page works in any browser;
- a full editor in the browser is phase 2.

It is the on-premise counterpart to Claude Code. Claude Code (cloud) builds the infrastructure. The
code partner works only on the repo and holds no credential to anything else. See
[AGENT_ACCESS_LANES.md](AGENT_ACCESS_LANES.md) for why that split matters.

## The rule, in code

**AI suggests, checks code, people decide.**

| Who | Does what |
|---|---|
| **The model** may | list, read and search files; propose an edit (exact text → new text); show the diff |
| **Code** checks every proposal | the path is allowed; the search text occurs exactly once; YAML/JSON still parse, Python compiles, shell passes `bash -n`, Terraform is `fmt`-clean; no added line looks like a credential; at most 4 files and 160 changed lines per change; CRLF files stay CRLF |
| **owner** decides | apply (= commit + push of the session's `domo/` branch, since 2026-11-06), reject, open the PR, merge. Merging isn't in the code partner at all, and Forgejo protects master as well. |

The model gets exactly six tools: `find_files`, `list_files`, `read_file`, `search`, `propose_edit`, `show_diff`.
There is no tool to apply, commit, push or run anything. Those happen only when owner says a fixed
phrase or presses a button, and code matches those, not the model.

**Before a change is committed, code checks again:**
- the change isn't older than 60 minutes;
- every file is still exactly as it was when the change was proposed;
- the checks and the secret scan pass again;
- the worktree is still on the session's `domo/` branch.

The commit says who approved it and how: `Approved-by: owner (voice)` or `owner (ui)`.

**Off limits:**
- **Never read:** `.env`, `terraform.tfvars`, state files, `secrets/`, keys, `.git/`.
- **Read but never written:** its own code and policy, `.gitignore` (un-ignoring tfvars would leak
  them), CI workflows, `CLAUDE.md` and `.claude/`.

All of this is in [`integration/code_partner_policy.yml`](../integration/code_partner_policy.yml),
and loosening any of it is always the owner's own edit.

## How it fits together

```
iPhone DOMO app ──WebRTC──▶ domo-gateway (gpu-02: Whisper, Piper)
                               │ text
                               ▼
                         domo-router (web-01) ── code_route: "start a coding session …",
                               │                   then every turn while it's open
                               │ X-Code-Partner-Key
Browser ──▶ code.home.example.org ──▶ Traefik + Authelia two_factor (web-01)
                               │ Remote-User
                               ▼
                   code-partner (code-01, 10.10.30.32:8210, systemd)
                     ├─ policy (read-only)       ├─ audit.jsonl (paths, sizes, outcomes; never content)
                     ├─ repo clone + one git worktree per session, branch domo/<date>-<topic>
                     ├─▶ gpu-01 Ollama :11434  gpu01-primary, num_ctx 16384 (the resident copy)
                     └─▶ git-01 Forgejo :3000  fetch, push domo/*, draft PR (bot user domo-coder)
```

**The VM is a guardrail too.** The systemd unit:
- runs without privileges, with a read-only system and its own code read-only;
- can exchange packets **only** with gpu-01, git-01 and web-01 (`IPAddressDeny=any`), which also
  means no DNS and no internet.

code-01 has no Vault credential and no SSH key to any other host. Ansible copies its two secrets
there at deploy time.

## What you say

Start and end:

| Say | What happens |
|---|---|
| "start a coding session (on the studio swap)" | New branch from origin/master, `domo/20261105-1917-studio-swap`. While it's open, everything you say goes to it. |
| anything else | The model works: reads, searches, proposes. It answers in 1–3 spoken sentences; code adds what's waiting ("Change 1 is ready: 2 changed lines in deploy_studio01.yml. Say 'apply change 1' or 'reject it'.") |
| "end coding session" / "that's it for coding" | Closes it (also after 45 minutes of silence); the branch stays |

Decisions (fixed phrases, matched by code):

| Say | What happens |
|---|---|
| "apply change 1" / "yes, apply it" / "commit that" | Commit on the branch and push the branch (it then shows in Forgejo and, after a fetch, in VS Code). **A bare "yes" never applies:** Whisper invents short words from noise (FOUND 2026-10-10). |
| Natural forms, at most 16 words: "proceed with the proposed edit", "let's go ahead and commit this and push it as well as create a PR" | The same; a trailing "and push / create a PR" also opens the PR. Anything after the object other than that ("apply this to the inventory too") goes to the model. |
| "reject it" / "drop change 2" | Discard it |
| "push it" / "open a pull request" / "create a PR" | Push the branch if needed (never anything else, never `--force`); open or update a draft PR "WIP: DOMO: …", review requested from owner |

Conversation and model:

| Say | What happens |
|---|---|
| "status" / "what's waiting" | Branch, applied changes, what's waiting, push state (from code) |
| "new conversation" | Clears the model's context; same branch, same changes |
| "switch model to …" / "which model" | Picks from the allowed list only, see [Models](#models) |

The page at code.home.example.org shows:
- the conversation;
- every waiting change as a colored diff, with **Apply** and **Reject** buttons;
- **Push + PR**, **New conversation** and **End session** buttons.

It refreshes every 4 s, so a voice turn shows up while you talk. You can also type there instead of
talking.

## Models

Only models that are **already resident**, each at the `num_ctx` it is loaded with. Any other
value makes Ollama reload, and DOMO stalls while it does
(CONTINUE_LOCAL_AI.md).
- Today that's one: `gpu01-primary` (Ministral 14B, 16K).
- gpu-02's model runs at 2048 tokens of context, too small for code.
- "Switch model" exists so a coder model can be added to the policy file the day a GPU has room
  for one.

What to expect:
- Ministral 14B handles small, scoped edits well: one setting, one task, one function.
- Multi-file refactors stay with Claude Code.
- The limits (4 files, 160 lines per change) point the conversation that way too.

## Tested

**Unit tests** (`integration/test_code_partner.py`, 88): they use a real git repo and remote and a
fake model. They cover:
- every path rule, symlinks out of the repo, and `./.gitignore`-style tricks;
- exact-once edits, CRLF, broken YAML and Python, 5 kinds of credential, plus 6 legitimate references
  that must pass;
- the size limits, and stale and expired changes;
- a bare "yes";
- push touching only the session branch (master on the remote unchanged);
- leaked tool calls ("[TOOL_CALLS]…" as text, which Ministral does);
- auth on every HTTP route, including the CSRF header;
- sessions surviving a restart.

**Router tests:** domo-router got 3 new tests; the existing ones still pass (359 in the two suites).

**Live run, 2026-11-05:** with the real `gpu01-primary`, on a scratch repo holding the real
`deploy_studio01.yml`. It found four things, all fixed:

| What happened | Fix |
|---|---|
| The model guessed a path that doesn't exist (`ansible/playbooks/deploy-studio-01.yml`); search quietly found nothing | A wrong path returns the closest real ones; the prompt carries a map of the repo's folders |
| It said "I'll change retries from 10 to 20" **without** calling `propose_edit` | Code catches the claim and sends one internal note asking for the call; if it still doesn't, code adds "Nothing is proposed yet" to the answer |
| Relative state paths broke the git worktree | Paths made absolute |
| Markdown backticks in spoken answers | Stripped |

After the fixes, the full loop worked:
1. "In the studio-01 deploy playbook, let the Chromatone health wait retry 20 times instead of 10." →
   a correct 2-line diff in 11 s.
2. "apply change 1" → a commit with `Approved-by: owner (voice)`.
3. "push it" → only `domo/…` on the remote; master untouched.

**Through the real voice path, in the full repo (2026-11-05, after deploy):**

| What happened | Fix |
|---|---|
| It ran out of steps (6): it looked for the playbook by searching file contents, and the folder map was dominated by the vendored `Docker/` trees | A `find_files` tool (by name), `repo_hints` in the policy instead of the folder map, 10 steps |
| "I'll propose changing it to 20" with no proposal ("propose" wasn't a claim word) | More claim words |
| After a bare "yes" the model said "Diff applied" (nothing was applied: code held it) | A bare yes/ok/go ahead is answered by code ("say 'apply change 1'"); a model sentence claiming applied/committed/pushed/merged is corrected while nothing is applied |

**Smoke test 2026-11-06 (session ecde7288, "one-page summary of the repo"): nothing reached git.**

| What happened | Fix |
|---|---|
| Ministral printed `propose_edit[ARGS]{…}` as text **without** `[TOOL_CALLS]`; the parser needed the prefix, so all three edits were lost (`changes: []`) | Prefix optional, several calls per reply, raw newlines in JSON allowed; an unreadable call is never spoken ("came out garbled") |
| The model said the diff was "visible as a pending diff on the owner's screen" and pointed to an "Apply Diff button in GitHub" | Prompt: talk to owner as "you", a change exists only with a change number, where things are (code.home.example.org, git.home.example.org, VS Code). Code appends a correction when nothing is waiting but the answer talks about a pending/visible/proposed diff ("already proposed" now counts as a claim) |
| "Proceed with the proposed edit…" and "commit this and push it as well as create a PR" went to the model | Natural apply/push phrases (above) |
| The branch never appeared in Forgejo | Apply pushes the branch; the PR stays a separate step |
| Spoken cleanup removed every `_`: GITOPS_REPO_SUMMARY.md → GITOPSREPOSUMMARY.md | Only underscores at word edges are removed |

A first attempt asked for a swap change that only exists on PR #57's unmerged branch. The model said
the setting wasn't in that file, which was right.

## Forgejo

[`scripts/forgejo_domo_coder.py`](../scripts/forgejo_domo_coder.py) sets up Forgejo. A dry run is the
default, and its plan was checked 2026-11-05:
1. bot user **`domo-coder`**: restricted, not admin;
2. collaborator on `owner/GitOps` with **write**, so it can push branches and open PRs;
3. **master protected:** only `owner` may push or merge. Nothing automated pushes to master today:
   the nightly session-records job pushes to `owner/claude-sessions`, a different repo;
4. its API key (write:repository, read:user) → Vault `secret/code-partner/forgejo` → `forgejo_key`;
5. the router key → Vault `secret/code-partner/router` → `router_key`.

Generated values go straight into Vault; nothing is printed.

## Deploy (done 2026-11-05)

| # | Step | Result |
|---|---|---|
| 1 | `scripts/forgejo_domo_coder.py --apply` | `domo-coder` restricted user, write on owner/GitOps (404 on private repos), master: push and merge only by owner, keys in Vault |
| 2 | `terraform apply` in `Terraform/envs/code-01/` | VM 252 on pve-01, 2 cores, 3 GB, 24 GB |
| 3 | Pi-hole `10.10.30.20 code.home.example.org` (REST, no restart) | resolves; Authelia two_factor in front |
| 4 | `deploy_code01.yml` | service active, ~48 MB; from the IaC VM (not allowed) it doesn't answer, from web-01 it does |
| 5 | domo-router: narrow live update (backups `*.bak-20261105202026` on web-01): `domo_router.py` from this branch (= live ha-home-scenes + `code_route`), 3 compose lines, `CODE_PARTNER_KEY` appended to `.env`, `up -d --no-deps domo-router` | healthy |

To redeploy the app after a change: `deploy_code01.yml --tags code_partner`.

## Phase 2 (not built)

Next up, in order (live progress, undo, checks that run, LiteLLM): [CODE_PARTNER_NEXT.md](CODE_PARTNER_NEXT.md).

| Item | What |
|---|---|
| **Editor in the browser** | openvscode-server on code-01 behind Authelia, with Continue pointed at gpu-01. A full editor without VS Code on the Mac, next to the voice partner. ~400 MB RAM, within the 3 GB. |
| **Checks that run code** | `pytest`, `ansible-playbook --syntax-check`, `terraform validate`, in a sandbox: own user, `PrivateNetwork=yes`, no key file. Off in the policy until then. |
| **Resume a branch** | "continue the coding session on studio swap" reopens an existing `domo/` branch |
| **Application side** | Read-only, per-person tools for app data (like Scout's narrow database link), added one at a time with the owner's OK, so questions about the data are answered on premise ([AGENT_ACCESS_LANES.md](AGENT_ACCESS_LANES.md) step 4) |
| **Audit to Postgres** | `mcp_audit`-style rows instead of the local JSON lines |

## Files

| File | What |
|---|---|
| `integration/code_partner.py` | the service |
| `integration/code_partner_policy.yml` | the guardrails |
| `integration/code_partner_ui.html` | code.home.example.org |
| `integration/test_code_partner.py` | tests |
| `integration/DOMO_IOS_App/domo_router.py` | `code_route` |
| `IaC/Terraform/envs/code-01/` | the VM |
| `IaC/Ansible/playbooks/deploy_code01.yml`, `Roles/code_partner/` | deploy |
| `IaC/services/assistant/code-partner/service.yaml` | the particle |
| `IaC/scripts/forgejo_domo_coder.py` | Forgejo setup |
