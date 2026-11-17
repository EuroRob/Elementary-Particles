# Elementary Particles — Solution Overview

## What it is

A fully self-hosted, infrastructure-as-code-driven personal/family cloud
platform — photos, video, documents, fitness, home automation, finance,
media, voice/AI assistance, and internal tooling, all running on owned
hardware with no dependency on third-party cloud services for daily
operation. Not a hobby homelab experiment: every service is deployed,
configured, monitored, and alerted-on through the same reproducible
pipeline, with real production usage (daily-driven since mid-2026),
real incidents, and real fixes — the repo's own commit history and
incident postmortems are the evidence.

## Architecture pattern ("Elementary Particles")

- **Provisioning**: Terraform (VM creation, disks, networking) against
  three standalone Proxmox VE hypervisors — no cluster, deliberately;
  each host is independently manageable.
- **Configuration**: Ansible, organized as composable roles per service
  ("particle"), driven from one inventory file as the single source of
  truth (`services.yml`).
- **Secrets**: HashiCorp Vault (KV v2), AppRole auth for automation,
  token auth for interactive use — nothing sensitive committed to git.
- **Orchestration**: n8n for scheduled/event-driven automation (backups,
  health checks, content pipelines) alongside direct Ansible/Terraform
  runs.
- **Everything is layered and idempotent** — a documented, numbered
  deployment order (network → identity/secrets → storage/database →
  reverse proxy → applications), each layer re-runnable without
  disturbing what's already up.

## Scale

Three Proxmox hypervisors, 30+ production VMs, several dozen
self-hosted applications, real multi-VLAN network segmentation
(management/IoT/services/storage/media/DMZ), full SSO across
web-facing services, and a monitoring/alerting stack independent enough
to page a phone even if the primary automation host is down.

## Infrastructure layer

| Concern | Tech |
|---|---|
| Virtualization | Proxmox VE (3 hosts, standalone) |
| Provisioning | Terraform (`bpg/proxmox` provider) |
| Configuration management | Ansible |
| Secrets | HashiCorp Vault |
| Reverse proxy / TLS | Traefik (file-provider routing) |
| SSO / auth | Authelia (OIDC + forward-auth) |
| Firewall / routing | OPNsense, VLAN-segmented |
| VPN | WireGuard (wg-easy) |
| Database | PostgreSQL (shared instance, per-app schemas/users) |
| Monitoring | Prometheus, Grafana, Alertmanager, node/pve exporters |
| Alerting | Multi-channel — Matrix/n8n webhook path + an independent Home Assistant push-notification path that bypasses the primary automation stack entirely |
| Git hosting | Forgejo (self-hosted, cut over from GitLab CE) |
| Automation/workflows | n8n |
| DNS / ad-blocking | Pi-hole |
| NAS / storage | OpenMediaVault on ZFS, host-level pools with either real disk passthrough or dataset exposure depending on the box |
| Backups | Proxmox VM backups → dedicated NAS targets, cross-host redundancy |

## Application layer (family-facing)

Nextcloud (files/calendar/contacts/tasks/notes), Immich (photos/video,
ML-backed search), Jellyfin (media, GPU-accelerated transcoding),
Paperless-ngx (document management), Tandoor (recipes), Firefly III
(personal finance), Outline (wiki, migrated off Confluence), Home
Assistant (smart home + Frigate NVR for camera/detection), a
custom-built Music Studio catalog, and a WGER-based fitness tracker
with an Apple Health sync bridge.

## The differentiated layer: an AI concierge, not a chatbot bolted on

This is the part most relevant to a "why would anyone else want this"
pitch:

- **Per-person AI agent instances** (OpenClaw-based) — each household
  member gets their own agent, own credentials, own scoped tool access,
  not one shared bot juggling identities. Runs on local GPU inference
  (Ollama), with automatic fallback between hosts — no dependency on a
  cloud LLM API for normal operation.
- **MCP (Model Context Protocol) tool servers** as the integration
  pattern — narrow, purpose-built, auditable tool sets (Nextcloud
  calendar/contacts/notes/tasks with real read-write access, wiki
  search, fitness data, etc.), not an all-powerful agent with unscoped
  system access.
- **Voice interface** — a custom web app (Echo) with real-time speech
  in/out (Whisper STT, Piper TTS), per-person voice profiles, and
  speaker-based routing to the correct agent instance.
- **Self-healing infrastructure patterns** — auto-remediation for known
  failure modes (database connection storms, container health checks),
  a monitoring stack designed to survive the failure of its own primary
  path.

