---
type: Event
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[DEPLOYMENT_CHECKLIST]]"
related_to:
  - "[[DEPLOYMENT_TEST_RESULTS]]"
  - "[[ANSIBLE_INTEGRATION]]"
incident_date: "2026-05-28"
severity: low
status: resolved
---

# Incident: Research-01 First-Deploy Neo4j Plugin Timeout

**Date:** 2026-05-28  
**Service:** research-01 → neo4j container  
**Severity:** Low  
**Status:** Resolved  

## What Happened

On first deployment of `research-01`, the `neo4j` container's healthcheck was
failing for ~90 seconds after start. The `research-api` container (which
depends_on neo4j with `condition: service_healthy`) was therefore delayed.

## Root Cause

Neo4j 5 downloads the APOC and Graph Data Science plugins on first boot when
`NEO4J_PLUGINS=["apoc","graph-data-science"]` is set. On a fresh VM with no
cached images, the plugin download added 60–90s to startup time, exceeding the
default healthcheck `start_period`.

## Impact

- `research-api` container failed to start on first `docker compose up -d`
- No data loss; no ingestion had run yet

## Resolution

Increased `start_period` in the neo4j healthcheck from 30s to 120s:

```yaml
healthcheck:
  test:     ["CMD", "wget", "-qO-", "http://localhost:7474"]
  interval: 30s
  timeout:  15s
  retries:  10
  start_period: 120s    # was 60s — extended for plugin download on cold start
```

Also documented in `IaC/services/research/research-01/docker-compose.yml`.

## Prevention

On subsequent deploys, plugins are already present on the Docker volume —
startup time returns to ~20s. This only affects first-deploy on a fresh volume.

## Lessons Learned

- Always set a generous `start_period` for containers that fetch dependencies on
  first boot (Neo4j, Elasticsearch with plugins, etc.)
- Add a cold-start note to the deployment quickstart
