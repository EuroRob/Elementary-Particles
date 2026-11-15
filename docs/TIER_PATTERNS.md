---
type: Topic
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[HOW_TO_ADD_SERVICE]]"
  - "[[DOCKER_IMAGE_GUIDE]]"
  - "[[TAG_REFERENCE]]"
  - "[[MULTI_COMPONENT_SERVICES]]"
---

# Service Tier Patterns

Architecture patterns and best practices for each deployment tier.

---

## 🏗️ Tier Overview

```
TIER-0: Platform        (Core IaC infrastructure)
   ↓
TIER-1: Core            (Foundation services - always on)
   ↓
TIER-2: Storage         (Data backends)
   ↓
TIER-3: Application     (User-facing services)
   ↓
TIER-4: Analytics       (Observability & Automation)
```

---

## TIER-0: Platform Services

**Purpose:** Core infrastructure-as-code tooling (rarely deployed after initial setup)

**Services:**
- Vault (secrets management)
- Terraform (provisioning)
- Ansible (configuration)
- NetBox (IPAM + source of truth)

### Pattern: Bootstrap Services
```yaml
# vault/service.yaml
name: vault
version: "1.15.0"
tier: "platform"
description: "HashiCorp Vault - secrets management"
owner: "infra-team"

tags:
  expose: "false"        # Internal only
  network: "vlan10"      # Management VLAN
  tier: "platform"
  sla: "critical"        # Everything depends on this
  backup: "daily"        # Configuration backup
  environment: "production"

deployment:
  platform: "docker"
  image: "vault:latest"
  
interfaces:
  - name: "api"
    protocol: "https"
    port: 8200
    internal_port: 8200

networking:
  vlan: "vlan10"
  dns_enabled: true
  dns_names:
    - "vault.internal"

monitoring:
  health_check: "http://localhost:8200/v1/sys/health"

backup:
  enabled: true
  schedule: "daily"
  targets:
    - type: "volume"
      source: "vault_config"
```

### Characteristics
- ✅ Don't get exposed (expose: false)
- ✅ Run on management VLAN (vlan10)
- ✅ Critical SLA (immediate alerts)
- ✅ Backed up (configuration disaster recovery)
- ✅ Available 24/7 (never stop)

### Deployment Strategy
```bash
# Deploy once during bootstrap
./orchestration/deploy.sh vault

# Then rarely touched (use `required: false` for other services to be resilient)
# If you need to update: blue-green deployment recommended
```

---

## TIER-1: Core Infrastructure Services

**Purpose:** Foundational services that everything else depends on

**Services:**
- Traefik (HTTP ingress)
- Authelia (authentication)
- Pi-hole (DNS)
- Homepage (user dashboard)

### Pattern 1: Traefik (Ingress Layer)

```yaml
# core/traefik/service.yaml
name: traefik
tier: "core"
description: "Reverse proxy & ingress controller"

tags:
  expose: "false"        # Unprotected (it IS the proxy)
  network: "vlan30"
  tier: "core"
  sla: "critical"        # Everything goes through it
  backup: "daily"        # Configuration

deployment:
  image: "traefik:latest"
  
interfaces:
  - name: "http"
    protocol: "http"
    port: 80
    internal_port: 80
    
  - name: "https"
    protocol: "https"
    port: 443
    internal_port: 443
    
  - name: "dashboard"
    protocol: "https"
    port: 8443
    internal_port: 8080

provides:
  - interface: "https:443"
    for_services:
      - "*"  # All exposed services

monitoring:
  health_check: "http://localhost:8080/ping"

networking:
  vlan: "vlan30"
  firewall_rules:
    - from: "any"
      to: "traefik"
      port: "80"
      action: "allow"
    - from: "any"
      to: "traefik"
      port: "443"
      action: "allow"
```

### Pattern 2: Authelia (Auth Gateway)

```yaml
# core/authelia/service.yaml
name: authelia
tier: "core"
description: "OIDC & MFA authentication gateway"

tags:
  expose: "true"
  auth: "none"           # It IS the auth system
  network: "vlan30"
  tier: "core"
  sla: "critical"

dependencies:
  - name: redis
    role: "session_store"
    required: true
    interface: "tcp/6379"

provides:
  - interface: "https:9091/api"
    for_services:
      - traefik
    contract: "OIDC API"
```

### Pattern 3: Pi-hole (DNS)

```yaml
# core/pihole/service.yaml
name: pihole
tier: "core"
description: "DNS & Ad blocking"

tags:
  expose: "true"
  auth: "basic"          # Admin panel protected
  network: "vlan30"
  tier: "core"
  sla: "critical"
  dns: "true"

interfaces:
  - name: "dns"
    protocol: "udp"
    port: 53
    internal_port: 53
    
  - name: "admin"
    protocol: "https"
    port: 443
    internal_port: 80
    path: "/admin"
    hostname: "pihole.internal"

provides:
  - interface: "udp:53"
    for_services:
      - "*"  # All services use this
```

### Core Tier Characteristics
- ✅ Form the foundation (everything built on these)
- ✅ Always running
- ✅ Single point of failure? Plan for redundancy
- ✅ Critical SLA
- ✅ Backed up

---

## TIER-2: Storage Services

**Purpose:** Data backends that applications depend on

**Services:**
- Postgres (primary database)
- Redis (cache/sessions)
- MinIO (S3-compatible storage)
- NFS/Storage mounts

### Pattern 1: Postgres (Primary Database)

```yaml
# storage/postgres/service.yaml
name: postgres
tier: "storage"
version: "15.4"
description: "PostgreSQL relational database"

tags:
  expose: "false"        # Network only
  network: "vlan40"      # Storage VLAN
  tier: "storage"
  sla: "critical"        # Data integrity critical
  backup: "daily"        # Nightly backups (WAL, full)
  data_classification: "confidential"

deployment:
  platform: "docker"
  image: "postgres:15-alpine"
  replicas: 1            # Single instance + backups

interfaces:
  - name: "database"
    protocol: "tcp"
    port: 5432
    internal_port: 5432

provides:
  - interface: "tcp:5432"
    for_services:
      - nextcloud
      - jellyfin
      - immich
      - paperless
      - gitlab
    contract: "PostgreSQL"
    authentication: "password"

secrets:
  - name: POSTGRES_PASSWORD
    vault_path: "secret/postgres/admin"
    required: true

monitoring:
  health_check: "health_check_command"
  metrics:
    - path: "/metrics"
      format: "prometheus"
      
backup:
  enabled: true
  schedule: "daily"
  targets:
    - type: "database"
      source: "postgres"
      exclude: ["pg_log"]
    - type: "volume"
      source: "postgres_data"

networking:
  vlan: "vlan40"
  firewall_rules:
    - from: "vlan30"       # Apps VLAN
      to: "postgres"
      port: "5432"
      action: "allow"
    - from: "vlan40"       # Storage VLAN
      to: "postgres"
      port: "5432"
      action: "allow"
```

### Pattern 2: Redis (Cache Layer)

```yaml
# storage/redis/service.yaml
name: redis
tier: "storage"
description: "In-memory cache & session store"

tags:
  expose: "false"
  network: "vlan40"
  tier: "storage"
  sla: "high"            # Important but resilient
  backup: "never"        # Ephemeral data (can rebuild)

deployment:
  image: "redis:7-alpine"
  replicas: 1

interfaces:
  - name: "cache"
    protocol: "tcp"
    port: 6379
    internal_port: 6379

provides:
  - interface: "tcp:6379"
    for_services:
      - authelia         # Session store
      - nextcloud        # Cache
      - jellyfin         # Session cache

secrets:
  - name: REDIS_PASSWORD
    vault_path: "secret/redis/password"
    required: false

monitoring:
  health_check: "ping"   # PING command
```

### Pattern 3: External Storage Mount (NAS)

```yaml
# storage/nas-media/service.yaml
name: nas-media
tier: "storage"
description: "External NAS mount for media files"

tags:
  expose: "false"
  network: "vlan40"
  tier: "storage"
  sla: "high"
  backup: "weekly"       # NAS has own backups

deployment:
  platform: "docker"
  # No image - mount management
  service_template: |
    nas-media:
      image: busybox:latest
      command: sleep infinity
      volumes:
        - type: nfs
          source: "nas.internal:/mnt/media"
          target: /media

provides:
  - interface: "nfs:2049"
    for_services:
      - jellyfin
      - immich
      - nextcloud

networking:
  vlan: "vlan40"
  firewall_rules:
    - from: "vlan30"
      to: "nas"
      port: "2049"       # NFS
      action: "allow"
```

### Storage Tier Characteristics
- ✅ Run in isolated network (vlan40)
- ✅ Restricted access (only from app VLAN)
- ✅ Critical SLA
- ✅ Daily or weekly backups (depending on importance)
- ✅ Handle sensitive data → encrypt volumes
- ✅ Monitor disk usage closely

---

## TIER-3: Application Services

**Purpose:** User-facing applications

**Services:**
- NextCloud (file sync)
- Jellyfin (media streaming)
- GitLab (version control)
- Immich (photo management)

### Pattern 1: Exposed Web App (NextCloud)

```yaml
# applications/nextcloud/service.yaml
name: nextcloud
tier: "application"
version: "28.0.0"
description: "File sync and collaboration platform"

tags:
  expose: "true"         # Users access it
  ingress: "https"       # HTTPS only
  auth: "oidc"           # OIDC via Authelia
  auth_provider: "authelia"
  dns: "true"            # DNS registered
  network: "vlan30"
  tier: "application"
  category: "Files"
  icon: "folder"
  color: "#0087BE"
  sla: "critical"        # Core service
  backup: "daily"
  data_classification: "confidential"
  environment: "production"

deployment:
  platform: "docker"
  replicas: 1            # Single instance default
  resources:
    cpu: "2"
    memory: "2Gi"
    storage: "50Gi"
  image: "nextcloud:28-apache"

interfaces:
  - name: "web"
    protocol: "https"
    port: 443
    internal_port: 80
    hostname: "nextcloud.internal"
    path: "/nextcloud"
    
  - name: "webdav"
    protocol: "https"
    port: 443
    path: "/nextcloud/remote.php/dav"

dependencies:
  - name: postgres
    role: "primary_datastore"
    required: true
    interface: "tcp/5432"
    authentication: "password"

provides:
  - interface: "https:443/nextcloud"
    for_services:
      - immich      # Can pull photos from NC
      - n8n         # Can orchestrate NC
    contract: "WebDAV + CalDAV + CardDAV"

secrets:
  - name: POSTGRES_PASSWORD
    vault_path: "secret/nextcloud/postgres"
    required: true
  - name: ADMIN_PASSWORD
    vault_path: "secret/nextcloud/admin"
    required: true

monitoring:
  health_check: "https://nextcloud.internal/nextcloud/status.php"
  health_check_interval: "60s"
  metrics:
    - path: "/metrics"
      format: "prometheus"
  alerts:
    - name: "nextcloud_down"
      condition: "up == 0"
      severity: "critical"
      notify: ["#ops-critical"]

backup:
  enabled: true
  schedule: "daily"
  retention: "30d"
  targets:
    - type: "database"
      source: "postgres_nextcloud"
    - type: "volume"
      source: "nextcloud_config"
      exclude: ["/var/www/html/data"]

networking:
  vlan: "vlan30"
  dns_enabled: true
  dns_names:
    - "nextcloud.internal"
    - "files.internal"
  firewall_rules:
    - from: "any"
      to: "nextcloud"
      port: "443"
      action: "allow"

metadata:
  data_classification: "confidential"
  compliance:
    - "GDPR"
  documentation_url: "https://docs.internal/nextcloud"
  sla:
    availability: "99.5%"
    response_time: "2s"
    resolution_time: "4h"
```

### Pattern 2: Internal API (Ollama)

```yaml
# applications/ollama/service.yaml
name: ollama
tier: "application"
description: "Local LLM inference API"

tags:
  expose: "false"        # Internal API only
  auth: "none"           # Trust network
  network: "vlan30"
  tier: "application"
  category: "Development"
  sla: "low"             # Optional, best-effort
  backup: "never"        # Stateless
  environment: "production"

deployment:
  platform: "docker"
  image: "ollama:latest"
  resources:
    cpu: "4"             # Needs CPU
    memory: "8Gi"

interfaces:
  - name: "api"
    protocol: "http"
    port: 11434
    internal_port: 11434

provides:
  - interface: "http:11434"
    for_services:
      - n8n
    contract: "REST API"

networking:
  vlan: "vlan30"
```

### Application Tier Characteristics
- ✅ User-facing (exposed: true or internal)
- ✅ Depends on storage tier
- ✅ Medium-to-high SLA
- ✅ Daily backups (if stateful)
- ✅ Health monitoring
- ✅ Authentication required

---

## TIER-4: Analytics & Observability

**Purpose:** Monitoring, logging, automation

**Services:**
- Prometheus (metrics)
- Grafana (dashboards)
- n8n (workflow automation)
- AlertManager (alerts)

### Pattern 1: Prometheus (Metrics Collection)

```yaml
# analytics/prometheus/service.yaml
name: prometheus
tier: "analytics"
description: "Metrics collection and time-series DB"

tags:
  expose: "false"
  network: "vlan30"
  tier: "analytics"
  sla: "high"            # Important but not critical
  backup: "weekly"       # Historical data
  environment: "production"

deployment:
  platform: "docker"
  image: "prom/prometheus:latest"

interfaces:
  - name: "api"
    protocol: "http"
    port: 9090
    internal_port: 9090

provides:
  - interface: "http:9090"
    for_services:
      - grafana
    contract: "Prometheus API"

monitoring:
  health_check: "http://localhost:9090/-/healthy"
```

### Pattern 2: Grafana (Dashboards)

```yaml
# analytics/grafana/service.yaml
name: grafana
tier: "analytics"
description: "Visualization dashboards"

tags:
  expose: "true"
  ingress: "https"
  auth: "oidc"
  dns: "true"
  network: "vlan30"
  tier: "analytics"
  category: "Monitoring"
  sla: "high"
  backup: "weekly"

dependencies:
  - name: prometheus
    role: "metrics_source"
    required: true

provides:
  - interface: "https:3000"
    for_services: []
    contract: "HTTP Dashboards"
```

### Pattern 3: n8n (Automation)

```yaml
# analytics/n8n/service.yaml
name: n8n
tier: "analytics"
description: "Workflow automation platform"

tags:
  expose: "true"
  auth: "oidc"
  dns: "true"
  network: "vlan30"
  tier: "analytics"
  category: "Automation"
  sla: "medium"
  backup: "daily"       # Workflows matter

dependencies:
  - name: postgres
    role: "workflow_storage"
    required: true

provides:
  - interface: "https:5678"
    for_services:
      - "*"  # Everything can trigger workflows
    contract: "Webhook API"
```

### Analytics Tier Characteristics
- ✅ Non-critical (service continues if analytics fail)
- ✅ Depends on core & storage tiers
- ✅ Often scaled separately (monitoring isolated)
- ✅ Can be best-effort SLA
- ✅ Preferentially co-located (don't spread monitoring)

---

## 📊 Cross-Tier Deployment Matrix

```
TIER-0          TIER-1          TIER-2          TIER-3          TIER-4
Platform        Core            Storage         Application     Analytics

↓               ↓               ↓               ↓               ↓
Vault       Traefik         Postgres        NextCloud       Prometheus
Terraform   Authelia        Redis           Jellyfin        Grafana
Ansible     Pi-hole         MinIO           Immich          n8n
NetBox      Homepage        NAS-mount       PaperLess       AlertManager

Dependencies:
→ Required by EVERYTHING
                            → Required by TIER-3
                                            → Optional to TIER-4
                                                            → Optional to TIER-3
```

---

## ⚡ Quick Reference: When to Use Each Tier

| Tier | Use For | Characteristics | Deploy How Often |
|------|---------|------------------|------------------|
| TIER-0 | Core IaC tools | Bootstrap once, rarely change | Once per year |
| TIER-1 | Ingress, Auth, DNS | Always on, foundational | Rarely (plan maintenance) |
| TIER-2 | Databases, caches | Persistent data, critical SLA | Rarely (data safety first) |
| TIER-3 | User applications | Ephemeral, can fail+restart | Daily (new features) |
| TIER-4 | Observability | Best-effort, non-critical | As needed |

---

## 🎯 Example: Full Stack Topology

```
TIER-4: Analytics
├─ prometheus (no deps)
├─ grafana (depends: prometheus)
└─ n8n (depends: postgres)

TIER-3: Applications
├─ nextcloud (depends: postgres)
├─ jellyfin (depends: postgres, optional: redis)
├─ immich (depends: postgres)
└─ paperless (depends: postgres)

TIER-2: Storage
├─ postgres (no deps)
├─ redis (no deps)
└─ nas-media (no deps)

TIER-1: Core
├─ traefik (no deps)
├─ authelia (depends: redis)
├─ pihole (depends: traefik)
└─ homepage (depends: traefik)

TIER-0: Platform
├─ vault (no deps)
├─ terraform (depends: vault)
├─ ansible (depends: terraform)
└─ netbox (depends: postgres from T2)
```

**Deployment order:**
```
1. vault (bootstrap)
2. postgres, redis (no deps)
3. traefik (TIER-1, no deps)
4. authelia (depends: redis)
5. pihole, homepage (no deps)
6. nextcloud, jellyfin, immich, paperless (all depend: postgres)
7. prometheus, n8n (depends: postgres)
8. grafana (depends: prometheus)
```

