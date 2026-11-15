---
type: Note
state: organized
ep_tier: platform
ep_particle: iac-api
belongs_to: "[[AUTONOMOUS_AGENT_HANDOFF]]"
related_to:
  - "[[NETBOX_STATUS_AUTOMATION]]"
  - "[[NETBOX_CUSTOM_FIELDS_SETUP]]"
  - "[[CREDENTIAL_ROTATION_PLAN]]"
decision_date: "2026-09-07"
status: active
---

# ADR: Two-Tier Agent Architecture (Operator + Concierge) over a Single Autonomous Agent

**Date:** 2026-09-07
**Status:** Active
**Tier:** platform | **Particle:** iac-api

## Context

The homelab has reached the point where LLM agents should take over two
different classes of ongoing work that a human/Claude-Code-interactive
session has been doing manually up to now:

1. **Operational maintenance** — the NetBox patch-lifecycle pipeline
   (`patch_status` custom field → Event Rule → webhook → n8n →
   `rolling_update.yml`) already exists and is partially live (see
   `NETBOX_STATUS_AUTOMATION.md` and the pg-01 auto-remediation pattern in
   `incidents/`). It needs an agent to plan patch cycles, evaluate
   blast radius, and either execute or request approval — not just fire a
   fixed Ansible play on a cron.
2. **Personal assistant / research assistance** — open-ended,
   conversational, multi-channel (chat-reachable) work that doesn't touch
   production infrastructure at all.

The user asked specifically whether **OpenClaw** — a self-hosted,
multi-channel personal-agent platform (skills system, cron scheduler, raw
SSH-exec tool, HTTP gateway, Telegram/Discord/WhatsApp/Matrix channels,
~380k GitHub stars by mid-2026) — should be adopted as *the* agent for both
jobs, orchestrated from n8n.

## Options Considered

| Option | Fit for ops/patching | Fit for personal assistant | Notes |
|---|---|---|---|
| **OpenClaw as single agent for both** | Poor | Excellent | Its core primitive is an SSH-exec tool with no PR gate, no repo-aware guardrail system, no equivalent of this repo's CLAUDE.md/memory discipline. Pointing that at production hosts directly recreates exactly the "written once, never executed, breaks on first real run" pattern this repo has hit repeatedly (`incidents/2026-08-04-netbox-patch-pipeline-first-activation.md`, the 2026-08-17 patch-pipeline bug wave, the NordLynx full-tunnel leak). Excellent as a chat-reachable, cron-driven assistant layer. |
| **Claude Agent SDK headless daemon for both** | Excellent | Workable but not its strength | Inherits this repo's guardrail/memory conventions natively (it's the same runtime as this session). No first-class multi-channel chat gateway (Telegram/Matrix/etc.) — would have to be built. |
| **Two-tier split: Operator (Claude Agent SDK) + Concierge (OpenClaw)** | Excellent | Excellent | Each tier does the job it's actually good at. The dangerous one (infra mutation) gets the tier with an inherited guardrail system, PR gating, and no ambient prod credentials. The safe one (chat/research) gets the tier with rich multi-channel reach. **Chosen.** |
| **Custom build (AGiXT / LangGraph / CrewAI)** | Good, but net-new | Good, but net-new | Rejected for now: highest engineering cost, and this homelab's actual gap isn't "no agent framework exists" — it's "no mutation boundary, audit trail, or approval gate exists yet." Revisit only if the two-tier split proves the framework itself, not the boundary, is the bottleneck. |

## Decision

Adopt a **two-tier agent architecture**:

- **Tier 1 — Operator**: a headless Claude Agent SDK daemon that inherits
  this repo's CLAUDE.md guardrails as its literal system prompt, acts only
  through the `iac-api` mutation surface (never raw SSH/Vault-root/Proxmox
  creds), and lands every change as a reviewed PR — never a direct commit.
  Owns patching, maintenance, and any task that can change infrastructure
  state.
- **Tier 2 — Concierge**: OpenClaw, wired to n8n and to chat channels
  (Matrix, to match the existing Matrix-alerting pattern), for personal
  assistant and research tasks. Given **read-only** access to `iac-api` and
  NetBox/Grafana dashboards. Anything it wants to change on the
  infrastructure side is filed as a request into Tier 1's approval queue —
  it never executes a mutation itself.

Full operating contract for both tiers: `../AUTONOMOUS_AGENT_HANDOFF.md`.

## Consequences

- **iac-api** stops being "the thing n8n calls for deploy webhooks" and
  becomes the single enforced mutation boundary for every non-human actor
  in the homelab. It needs new endpoints (approval queue, dry-run mode,
  action audit log) before Tier 1 goes live for real — tracked as Phase 1
  in the handoff doc.
- Two systems to run instead of one (a new Operator daemon + OpenClaw),
  but the blast radius of a Concierge mistake is bounded to "sent a bad
  chat reply," not "patched the wrong VM." That trade is worth it given
  this repo's track record of automation breaking on first real execution.
- Git branch hygiene (`[[feedback_repo_branch_hygiene]]`) gets a second
  consumer: agent-authored branches (`agent/<slug>`) must follow the same
  discipline already flagged as inconsistent for human-authored branches.
- If OpenClaw's SSH-exec tool is ever pointed at anything beyond its own
  container/VM, that is a silent violation of this ADR — flag it.
