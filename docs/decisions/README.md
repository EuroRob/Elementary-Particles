---
type: Responsibility
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[UNIFIED_RELATIONSHIP_MODEL]]"
  - "[[ELEMENTARY_PARTICLES_ERM]]"
---

# Architecture Decision Records — README

This directory contains Architecture Decision Records (ADRs) for the
Elementary Particles platform. Each file captures a specific architectural
choice: the context that drove it, the options considered, and the reasoning
behind the decision made.

ADRs answer the question *why does the system look the way it does?* — the
question that no service.yaml, relationships.yaml, or deployment guide answers.

## File naming convention

```
YYYY-MM-DD-<short-slug>.md
```

Examples:
```
2026-05-28-qdrant-over-weaviate.md
2026-04-10-neo4j-community-edition.md
2026-03-15-opnsense-over-pfsense.md
2026-02-20-postgres-single-primary.md
```

## Frontmatter template

```yaml
---
type: Note
state: organized          # → archived if the decision is superseded
ep_tier: <tier>
ep_particle: <primary-vm-or-service>
belongs_to: "[[<primary-architecture-doc>]]"
related_to:
  - "[[<affected-doc>]]"
decision_date: "YYYY-MM-DD"
status: active | superseded | deprecated
superseded_by: ""         # fill in if status: superseded
---
```

## Why this matters

ADRs are indexed by MemPalace and the Research Brain. Future queries like
"why do we use Qdrant instead of Weaviate?" or "why is Neo4j Community edition
instead of Enterprise?" return the actual reasoning rather than a blank.

ADRs also help onboard new contributors and prevent re-litigating settled
decisions. When context changes, update `status: superseded` and point
`superseded_by` to the newer ADR.
