---
type: Responsibility
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[DEPLOYMENT_CHECKLIST]]"
  - "[[PROXMOX_HOST_IaC]]"
---

# Incident Log — README

This directory contains dated incident records for the Elementary Particles platform.
Each file captures a real operational event: what broke, what the impact was, how it
was resolved, and what was changed to prevent recurrence.

## File naming convention

```
YYYY-MM-DD-<vm-or-service>-<short-slug>.md
```

Examples:
```
2026-05-15-research-01-neo4j-oom.md
2026-04-22-gpu-01-vram-exhausted.md
2026-03-10-pg-01-wal-archive-lag.md
```

## Frontmatter template

```yaml
---
type: Event
state: organized          # → archived once postmortem complete
ep_tier: <tier>
ep_particle: <vm-or-service>
belongs_to: "[[<VM_DEPLOYMENT_GUIDE>]]"
related_to:
  - "[[<related-doc>]]"
incident_date: "YYYY-MM-DD"
severity: low | medium | high | critical
status: open | resolved | monitoring
---
```

## Why this matters

Every incident log becomes part of the institutional memory that MemPalace
mines and the Research Brain indexes. Future queries like "what broke on
research-01 before?" or "has Neo4j OOM happened previously?" become answerable
without trawling chat logs.
