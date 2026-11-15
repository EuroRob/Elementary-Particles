---
type: Note
state: organized
ep_tier: platform
ep_particle: iac-api
belongs_to: "[[ARCHITECTURE_INDEX]]"
related_to:
  - "[[NETBOX_STATUS_AUTOMATION]]"
  - "[[NETBOX_CUSTOM_FIELDS_SETUP]]"
  - "[[CREDENTIAL_ROTATION_PLAN]]"
  - "decisions/2026-09-07-two-tier-autonomous-agent-architecture.md"
status: draft — Phase 1 code written 2026-09-07, not yet deployed/exercised against live infra
---

# Autonomous Agent Handoff — Operator + Concierge

**If you are an LLM agent reading this to take over operational or
personal-assistant duties in this homelab, this file is written for you.**
It is the CLAUDE.md-equivalent for autonomous, non-interactive operation.
Read it in full before taking any action. It assumes you have also read
`IaC/CLAUDE.md` (the human-paired guardrails) — everything in that file
still applies to you; this file adds what changes when nobody is watching
the terminal in real time.

Status: **Phase 1 code exists, nothing has been run against live
infrastructure.** The `/api/agent/*` endpoints, the `agent.agent_actions`
table (created on first successful startup via `ensure_agent_schema()`),
and three n8n workflows (`agent_operator_patch_plan.json`,
`agent_approval_decision.json`, `concierge_orchestration.json` — see §10)
are written and syntax-checked, but **none have been deployed to
`/opt/iac` or exercised against pg-01/NetBox for real.** Treat every
endpoint and table here the same way this repo treats every other
"written, not yet run" playbook — this repo has a documented history of
automation written in one session and breaking three separate ways on
first real execution (`incidents/2026-08-04-netbox-patch-pipeline-first-activation.md`),
and the single most important guardrail this doc can give you is: **don't
repeat that pattern.** Before trusting any of this, at minimum: deploy
`api_server.py` + `requirements-iac-api.txt`, set the two new tokens in
`/opt/iac/iac-api.env` (see that file's template), confirm `PG_APP_USER`
has `CREATE` on `elementary_particles`, hit `GET /api/health` and confirm
`agent_db_reachable: true`, then run the Operator plan workflow manually
once before ever letting its schedule trigger fire unattended.

---

## 1. The two tiers, and which one you are

| | **Tier 1 — Operator** | **Tier 2 — Concierge** |
|---|---|---|
| Runtime | Headless Claude Agent SDK daemon | OpenClaw |
| Job | Patching, maintenance, performance/stability response, any infra-mutating task | Personal assistant, research, news/briefing tasks, chat-reachable Q&A |
| Credentials | Scoped Vault AppRole for `iac-api` only — **no** raw SSH keys, **no** Proxmox/Vault root tokens, **no** NetBox admin token | Read-only Vault AppRole (dashboards, NetBox read, `iac-api` GET endpoints only) |
| Can it change infrastructure? | Yes — but only via `iac-api`, and only by opening a PR (see §5) or, for pre-approved low-risk actions, via a logged direct call | **No.** Ever. It files a request into the Operator's approval queue (§6) instead. |
| Where it runs | Dedicated VM/container, own service account, restricted network (§4) | Wherever OpenClaw's chat-gateway needs live (can be more permissive network-wise since it holds no mutation power) |
| Triggered by | n8n (cron + NetBox Event Rule webhooks + Alertmanager) | n8n (cron) + direct chat (Telegram/Matrix/etc.) |

If you're not sure which tier you are: **if your task can change the state
of a real host, VM, container, DNS record, firewall rule, or credential,
you are Tier 1 rules, full stop** — even if you were invoked from a chat
message. Tier 2 never gets to "just this once."

Rationale for this split, and why a single agent (including OpenClaw doing
both jobs) was rejected: `decisions/2026-09-07-two-tier-autonomous-agent-architecture.md`.

---

## 2. What you inherit from this repo (and what you don't)

You do **not** have a Claude Code `~/.claude/projects/.../memory/` directory
— that memory system is specific to interactive Claude Code sessions on the
IaC VM. You need a durable equivalent, because you will lose all in-context
knowledge between invocations (each n8n trigger or chat message is a fresh
process). Use these, in order of preference:

1. **NetBox** is still the source of truth for machine *state*
   (`patch_status`, `patch_last_run`, health-check fields — see
   `NETBOX_CUSTOM_FIELDS_SETUP.md`). Read it fresh every run. Don't cache
   it across invocations.
2. **NetBox journal entries** (native feature, one per object) — write a
   journal entry on the relevant VM/device object for every action you
   take against it. This is your per-host audit trail and is queryable by
   a human or by you on the next run.
3. **`agent_actions` table on pg-01`** (new — Phase 1, §9) — your
   cross-cutting memory: approval-queue requests, decisions, and anything
   that doesn't belong to one NetBox object. Schema in §7.
4. **Git history** on `agent/*` branches (§5) — your record of what code
   changes you proposed and why, in the commit message.
5. **This repo's human memory files**
   (`~/.claude/projects/.../memory/*.md` on the IaC VM) are **read-only**
   to you. Don't write to them — that's the human-paired Claude Code
   session's memory, not yours. If something you learn belongs there
   (a new recurring gotcha, a completed project), say so in your PR
   description and let the human-paired session fold it in.

You inherit `IaC/CLAUDE.md`'s 10 security guardrails **verbatim, no
exceptions for autonomous mode** — guardrail 1 literally says "no
exceptions, even in agent/autonomous mode." In particular:
- Guardrail 9 (never generate/export a WireGuard peer config) applies to
  you exactly as written. This is an absolute, not a judgment call.
- Guardrail 10 (new/rotated creds → Vault only, never a file) applies to
  you exactly as written.

---

## 3. The mutation boundary: `iac-api` only

You never hold credentials that reach a production host, Vault, or
Proxmox directly. Every mutating action goes through `iac-api`
(`IaC/api_server.py` on the IaC VM), which:
- holds the real credentials (Vault AppRole with broader scope than yours),
- validates and logs the request before acting,
- is the only thing in this homelab allowed to have that broader scope.

This mirrors how n8n already calls `iac-api` for deploy webhooks (Layers
5-8) and how the pg-01 auto-remediation path already works
(`incident/pg01_restart_orphans_app_connections` → iac-api restart +
Matrix alert, bypassing n8n). You are extending an existing, proven
pattern — not inventing a new one.

**Phase 1 status: implemented in `api_server.py` (2026-09-07), not yet
deployed.** Endpoints, all in the `# ==================== Autonomous Agent
Endpoints (Phase 1) ====================` section:

| Endpoint | Purpose | Auth | Gate |
|---|---|---|---|
| `GET /api/agent/patch-queue` | List NetBox VMs with `patch_status=pending` | Operator token | read-only |
| `POST /api/agent/patch/{vm}/plan` | Dry-run only — reuses `rolling_update.yml`'s own `dry_run=true` path | Operator token | always dry-run, never mutates |
| `POST /api/agent/patch/{vm}/execute?approval_id=N` | Runs `rolling_update.yml` for real | Operator token | **hard-gated**: refuses unless `agent_actions` row `N` has `status='approved'` and `target==vm` |
| `POST /api/agent/action-log` | Free-form audit entry | Operator or Concierge token | tier must match request body |
| `POST /api/agent/approval-request` | File a request into the approval queue | Operator or Concierge token | the *only* infra-adjacent action the Concierge token may take |
| `GET /api/agent/approval-status/{id}` | Poll a filed request | Operator or Concierge token | read-only |
| `POST /api/agent/approval/{id}/decide` | Approve or deny | **human/n8n token only** | rejects both agent tokens outright — see `verify_human_token()` |
| `GET /api/agent/actions` | List/filter the audit log | **human/n8n token only** | feeds the Matrix digest |

Every write path calls `log_agent_action()`/`update_agent_action()` before
or as part of acting — log-then-act, not act-then-log, so a crash
mid-action still leaves a record. Two inbound tokens exist, checked by
`verify_agent_token()` (Operator) and `verify_concierge_or_agent_token()`
(either tier, returns which one authenticated) — see `iac-api.env.example`
for `IAC_API_AGENT_TOKEN` / `IAC_API_CONCIERGE_TOKEN` setup.

**Found and fixed while wiring this**: `AnsibleRequest.inventory` defaulted
to `"production"` and `run_ansible_command()` built `-i inventories/{inventory}`
(plural) — neither has ever existed in this repo; the real path is
`inventory/services.yml` (singular). Fixed in both places. **Not yet
verified**: whether `patch_scheduler.json` and `patch_trigger.json` (the
existing *live* n8n workflows) are affected — both hardcode
`"inventory": "production"` directly in their request bodies, which would
still hit the same broken path even after this fix, unless something on
the IaC VM outside this repo (a symlink, an env override) already papers
over it. Worth a live check before assuming those two workflows are
unaffected.

**What you are never allowed to reach directly, agent or not:**
- Proxmox API with anything beyond a read-only token
- Vault paths outside your own AppRole's policy
- `ssh root@<host>` / `ssh ansible@<host>` to any production host
- wg-easy's peer-config/QR endpoints (guardrail 9)
- NetBox admin/write token beyond the specific custom fields you're scoped for

---

## 4. Where you run

- **Operator**: **`comms-01`** (10.0.30.45, VLAN30 — the existing
  Matrix/Synapse/Element/matrix-bot host), decided 2026-09-08. Not a new
  VM: comms-01 is already always-on critical infra, already on VLAN30, and
  already has an open firewall path to pg-01 (`Services → pg-01:5432`,
  seq 1800) and to iac-api — no new firewall rules needed for the Operator
  itself, unlike the narrow flat-network→pg-01:5432 rule iac-api's own
  `agent_actions` table needed (added 2026-09-08, see incidents below).
  **Accepted trade-off, decided knowingly**: comms-01 is also the Matrix
  alerting channel every Operator/Concierge notification in this design
  posts to. If comms-01 has a bad day, the Operator and the alerts about
  it going down fail together — the one scenario where you'd most want
  the alert to still work. Weighed against provisioning a whole separate
  VM and accepted anyway. If this ever becomes a real problem in practice
  (comms-01 instability, resource contention with Synapse), revisit —
  the original plan (a dedicated `agent-op-01` VM, fully isolated blast
  radius) is still the fallback.
- Should still be firewalled to reach only what it needs: `iac-api`,
  NetBox, Vault (its own scoped AppRole), Forgejo (git-01, for PRs), and
  Matrix (local, since it's now co-located). No route to VLAN40/50
  production hosts directly beyond what comms-01 already has.
- **Concierge (OpenClaw)**: can run more permissively since it holds no
  mutation power, but still: no SSH-exec tool pointed at anything beyond
  its own container, and its Vault AppRole must be read-only. If OpenClaw's
  built-in SSH-exec skill is enabled for anything beyond its own sandbox,
  that's a violation of the ADR — disable it or scope it to nothing.

---

## 5. Git / PR workflow for your changes

You never commit to `master`, and never push directly to any
long-lived branch. For every code change (Ansible, Terraform, iac-api,
your own prompt/guardrail files):

1. Branch: `agent/<short-task-slug>` off current `master`.
2. Commit with a clear message; sign as yourself, e.g.:
   `Co-Authored-By: Operator Agent <admin@home.example.org>`.
3. Push to Forgejo (`git.home.example.org` / `forgejo.home.example.org`), open a PR
   against `master`.
4. Request review — either flag it for the human, or (once the workflow
   exists) trigger the repo's `/code-review` skill against the PR before a
   human merges. **You do not merge your own PRs.**
5. Note in the PR description: what NetBox state or alert triggered this,
   what you verified (dry-run output, health checks), and what's still
   unverified.

This directly extends `[[feedback_repo_branch_hygiene]]` — master has
already drifted stale relative to feature branches once in this repo's
history; don't let agent branches make that worse. Check for an existing
open `agent/*` branch touching the same host/service before starting a new
one.

---

## 6. Approval gate — what requires a human before you act

Default posture: **dry-run first, execute only with explicit approval**,
until a specific action class has a proven track record (§9, Phase 2+).

**Always requires human approval, no exceptions, regardless of phase:**
- Anything touching WireGuard peer configs, firewall WAN rules, or
  full-tunnel routing (see `[[incident_nordlynx_full_tunnel_leak_2026-09-05]]`)
- Credential rotation of any kind
- Any action touching more than one host in a single run
- Any `terraform apply` that destroys or recreates a resource (not just
  updates in place)
- Deleting anything — VM, snapshot, Vault path, NetBox object
- The first-ever execution of any playbook/script against a given host
  (matches the lesson from the 2026-08-04 and 2026-08-17 incidents: first
  real runs surface bugs that a dry-run/`--check` alone won't catch — a
  human should be watching that specific run)

**Eligible for auto-execute once a track record exists (Phase 2+):**
- A single VM's routine patch cycle, low blast-radius tier, snapshot taken
  first, health-check-verified after
- Restarting a container that's already covered by an existing
  auto-remediation pattern (e.g. pg-01-dependent containers)

File an approval request via `POST /api/agent/approval-request`
(action type, target, plan/diff, blast-radius estimate, dry-run output).
Post the same summary to Matrix (§8) so the human sees it without having to
poll an API. Do not proceed until `approval-status` returns approved, or a
documented timeout/escalation policy (not yet defined — flag as an open
question, §10) fires.

---

## 7. Audit trail — `agent.agent_actions` table (Phase 1, implemented)

Created automatically by `ensure_agent_schema()` on `iac-api` startup — a
dedicated `agent` schema inside the existing shared `elementary_particles`
database on pg-01 (same DB other services already share), not a new
database. Best-effort like `init_vault_client()`: a cold pg-01 at boot
logs a warning and leaves `/api/agent/*` 500ing rather than crashing the
whole API. Requires `PG_APP_USER` to have `CREATE` on `elementary_particles`
— **not yet verified live**, check before first deploy.

```sql
CREATE TABLE agent.agent_actions (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    tier            TEXT NOT NULL CHECK (tier IN ('operator','concierge')),
    action_type     TEXT NOT NULL,          -- e.g. 'patch_execute', 'approval_request'
    target          TEXT,                   -- hostname/VM/service, if applicable
    dry_run         BOOLEAN NOT NULL DEFAULT true,
    status          TEXT NOT NULL,          -- 'proposed'|'approved'|'denied'|'executed'|'failed'
    approved_by     TEXT,                   -- human identity, or NULL
    plan_summary    TEXT,                   -- human-readable, no secrets
    result_summary  TEXT,
    matrix_msg_ref  TEXT,                   -- correlate to the alert posted
    netbox_journal_ref TEXT                 -- correlate to the per-object journal entry
);
```

Never write a secret value into `plan_summary` or `result_summary` —
guardrail 1 applies to this table too. Redact the same way `IaC/CLAUDE.md`
already requires elsewhere (key names/lengths, not values).

---

## 8. Alerting — reuse the Matrix pattern, don't invent a new one

The pg-01 auto-remediation path already posts restart events to Matrix,
bypassing n8n for speed. Reuse that channel and that pattern for every
Tier 1 action:
- Post before acting (for anything requiring approval) and after acting
  (result, success/fail, link to the PR if one was opened).
- Concierge posts to a **separate** Matrix room/channel — never mix
  personal-assistant chatter with operational alerts, or a human will
  start ignoring the room that actually matters.

---

## 9. Phased rollout

- **Phase 0 (now)**: this document + the ADR exist. Nothing executes.
- **Phase 1**: build the `iac-api` agent endpoints (§3), the
  `agent_actions` table (§7), and stand up the Operator daemon on its own
  VM with a read-only-to-start Vault AppRole. Operator runs in
  **plan-only** mode: it reads the NetBox patch queue, produces a plan,
  opens a PR, posts to Matrix — and stops. No execution, ever, in this
  phase.
- **Phase 2**: after several plan-only cycles have been reviewed and
  merged manually with no surprises, allow the Operator to auto-execute
  the single lowest-risk action class from §6, still with pre-action
  snapshot + post-action health check + Matrix alert on every run.
- **Phase 3**: stand up OpenClaw as the Concierge tier — read-only
  access, its own Matrix room, wired into n8n for scheduled
  research/briefing tasks. Wire its "I want to change something" path into
  the Operator's approval queue rather than giving it any execute power.
- **Phase 4**: expand the Operator's auto-execute scope one action class
  at a time, each expansion reviewed against §6's "always requires
  approval" list before being proposed for auto-execute.

Do not skip phases to save time. This repo's own incident history is the
argument for that: the patch pipeline was written complete-looking in one
session and broke three separate ways (wrong hostname, untagged Vault
tasks, wrong Vault KV path shape) the first time anyone actually ran it.
An autonomous agent skipping the plan-only phase is that same failure mode
with nobody watching.

---

## 10. n8n workflows (written 2026-09-07, not yet imported)

Three workflow JSON files under `IaC/n8n/workflows/`, matching this repo's
existing real-n8n-export format (see `patch_trigger.json` for the pattern
these follow):

- **`agent_operator_patch_plan.json`** — the Operator's Phase-1 plan-only
  cycle. Daily cron → `GET /api/agent/patch-queue` → per VM,
  `POST /api/agent/patch/{vm}/plan` (always dry-run) → poll → file an
  approval-request with the dry-run output attached → post an Approve/Deny
  link pair to Matrix. **Never calls `/execute`.** Does not touch or
  replace the existing live `EP: Monthly Patch Scheduler -> reset_pending`
  / `EP: NetBox Patch Trigger` workflows — those remain the direct-execute
  path until this one earns Phase 2 promotion.
- **`agent_approval_decision.json`** — the shared webhook
  (`GET /webhook/agent-approve?id=&decision=&approved_by=`) that both the
  Operator workflow above and the Concierge's infra-request branch link
  to. Calls `/api/agent/approval/{id}/decide` with the **human/n8n
  token** (never an agent token — this is the enforcement point for §6).
  Posts a confirmation back to Matrix and returns an HTML confirmation
  page to the clicker.
- **`concierge_orchestration.json`** — two branches: a scheduled
  "morning_briefing" call into OpenClaw's gateway (placeholder API shape —
  **not verified against OpenClaw's real endpoint**, fix before enabling)
  that speaks the result via the *existing* Piper TTS pipeline
  (`tts.piper` → the "Jarvis speaker", same call already proven live in
  `Home Assistant → Ollama (qwen2.5_7b) with Two-Tier Memory.json` — no
  second TTS engine stood up) and posts to a dedicated Concierge Matrix
  room; and a webhook (`concierge-infra-request`) OpenClaw calls when it
  wants an infra change, which files an approval-request via the
  **Concierge token** and nothing else — it cannot execute anything
  directly, matching §1's tier boundary.

**Before importing any of these**: create the `IaC API (Operator Agent
Bearer)` and `IaC API (Concierge Bearer)` httpHeaderAuth credentials
(values = `IAC_API_AGENT_TOKEN` / `IAC_API_CONCIERGE_TOKEN` from
`/opt/iac/iac-api.env`), swap the `TBD-operator-cred` / `TBD-concierge-cred`
/ `TBD-openclaw-cred` placeholder credential ids for the real ones n8n
assigns, and create a dedicated Matrix room for Concierge chatter and
(recommended, not yet done) a separate "Agent Approvals" room instead of
reusing System Alerts for approval requests.

---

## 11. Open questions (for the human to decide, not for you to assume)

- Approval-request timeout/escalation policy — if nobody responds, does it
  expire, retry-notify, or stay pending indefinitely? Not yet defined.
- Which Vault AppRole policy exactly backs the Operator's scoped token —
  needs to be authored and reviewed before Phase 1, not inferred by you.
- Whether OpenClaw's own model backend should be local (Ollama on
  gpu-01/gpu-02 — remember to set `options.num_ctx` explicitly, see
  `[[feedback_ollama_num_ctx_default]]`) or a hosted API — cost/privacy
  trade-off, homelab owner's call.
- Concierge voice: recommendation is to swap the existing Piper TTS voice
  model on the "Jarvis speaker" from `en_US-amy-low` to `en_GB-alan-medium`
  (calm, measured British-male register — the closest stock Piper voice to
  a Jarvis/Vision-style assistant tone) rather than standing up a second
  TTS engine or, especially, attempting to voice-clone the actual character
  performance (a real actor's protected voice — don't). This is a
  `PIPER_VOICE` env var change in `services/assistant_stack/docker-compose.yml`
  plus a one-time voice-model download, reversible in minutes. Not yet
  done — confirm the choice, then apply.
- Kill switch: the fastest documented stop-everything is disabling the
  NetBox Event Rule (`EP Patch Trigger`) that fires the webhook chain, or
  revoking the Operator's Vault AppRole. Neither has been tested as a
  kill switch yet — do that before Phase 2, not after you need it.
