---
type: Responsibility
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[TAG_REFERENCE]]"
  - "[[TIER_PATTERNS]]"
  - "[[DOCKER_IMAGE_GUIDE]]"
  - "[[RELATIONSHIPS_QUICK_START]]"
---

# How to Add a Service

This is your **"hello world"** guide for the modular IaC platform.

## 🚀 Quick Start (5 minutes)

### Step 1: Create Service Directory

```bash
mkdir -p IaC/services/applications/myservice
cd IaC/services/applications/myservice
```

### Step 2: Create `service.yaml` (Service Definition)

**Minimal viable service:**

```yaml
name: myservice
version: "1.0.0"
tier: "application"

description: "My awesome service"
owner: "ops-team"

tags:
  expose: "false"              # Don't expose yet
  tier: "application"
  
deployment:
  platform: "docker"
  
interfaces:
  - name: "web"
    protocol: "http"
    port: 8080
    
dependencies: []              # No dependencies initially

provides: []                  # What it provides (optional)

deployment:
  image: "myimage:latest"
  service_template: |
    myservice:
      image: myimage:latest
      container_name: myservice
      networks:
        - services

secrets: []
monitoring:
  health_check: "http://localhost:8080/health"
backup:
  enabled: false
networking:
  vlan: "vlan30"
  dns_enabled: false
metadata: {}
```

### Step 3: Create `docker-compose.yml` (How to Run It)

```yaml
version: '3.8'

services:
  myservice:
    image: myimage:latest
    container_name: myservice
    environment:
      - LOG_LEVEL=info
    networks:
      - services
    restart: unless-stopped

networks:
  services:
    external: true
```

### Step 4: Validate It

```bash
# Check schema (when implemented)
./orchestration/validate_services.py services/applications/myservice
```

### Step 5: Deploy It

```bash
# Deploy single service
./orchestration/deploy.sh myservice

# OR deploy all services
./orchestration/deploy.sh --all
```

---

## 📋 Service Definition Reference (Full)

### Required Fields

```yaml
name: "service-name"                    # lowercase, no spaces
version: "1.0.0"                        # SemVer
tier: "platform|core|storage|application|analytics"
description: "What does this do?"
owner: "team-name"
```

### Tags (All Possible)

```yaml
tags:
  # ===== EXPOSURE =====
  expose: "true|false"                  # Show in Traefik?
  ingress: "http|https|both"            # Protocol
  
  # ===== AUTH =====
  auth: "none|basic|oidc|custom"        # Auth method
  auth_provider: "authelia|keycloak"    # Which provider
  
  # ===== NETWORKING =====
  network: "vlan10|vlan30|vlan40|vlan50"  # VLAN placement
  dns: "true|false"                     # Register DNS?
  
  # ===== TIERING & OPS =====
  tier: "platform|core|storage|application|analytics"
  backup: "never|daily|weekly|on-demand"
  sla: "critical|high|medium|low"
  owner: "ops|infra|apps|data"
  runbook: "https://..."                # Operational guide
  
  # ===== UI/DISPLAY =====
  category: "Admin|Files|Media|Automation|Development|Monitoring"
  icon: "cloud|folder|play|cog|code"    # Icon name
  color: "#FF5733"                      # Brand color
  
  # ===== CUSTOM =====
  environment: "production|staging|dev"
  cost_center: "ops|infra|apps"
  data_classification: "public|internal|confidential|restricted"
```

### Deployment

```yaml
deployment:
  platform: "docker|vm|kubernetes|bare-metal"
  namespace: "vlan30|default"           # Where to place it
  replicas: 1                           # For scaling
  resources:
    cpu: "2"
    memory: "2Gi"
    storage: "10Gi"
  
  image: "myimage:tag"                  # For containers
  dockerfile: "./Dockerfile"            # To build image
  
  service_template: |                   # Docker Compose service definition
    myservice:
      image: {{ image }}
      environment:
        - VAR1={{ env_var }}
      volumes:
        - myservice_data:/data
```

### Interfaces (How Services Expose Themselves)

```yaml
interfaces:
  - name: "web"
    protocol: "https"
    port: 443
    internal_port: 8080                # Container port
    path: "/myservice"                  # URL path
    hostname: "myservice.internal"      # FQDN
    
  - name: "api"
    protocol: "https"
    port: 443
    path: "/myservice/api"
```

### Dependencies

```yaml
dependencies:
  - name: postgres
    role: "primary_datastore"           # Why needed?
    required: true                      # Can't start without it
    interface: "tcp/5432"               # How to reach it
    authentication: "password"
    tags:
      tier: "storage"
      sla: "critical"

  - name: redis
    role: "cache"
    required: false                     # Optional (graceful degradation)
    interface: "tcp/6379"
```

### Provides (What Other Services Can Consume)

```yaml
provides:
  - interface: "https:443/myservice"
    for_services:
      - nextcloud                       # Other services that use this
      - n8n
    contract: "REST API"
    authentication: "api_key"
    documentation: "https://..."
```

### Secrets (In Vault, Not Here!)

```yaml
secrets:
  - name: DATABASE_PASSWORD
    vault_path: "secret/myservice/db"
    required: true
    
  - name: API_KEY
    vault_path: "secret/myservice/api"
    required: false
```

### Monitoring

```yaml
monitoring:
  health_check: "http://localhost:8080/health"
  health_check_interval: "30s"
  
  metrics:
    - path: "/metrics"
      port: 8080
      format: "prometheus"
  
  alerts:
    - name: "service_down"
      condition: "up == 0"
      severity: "critical"
      notify: ["#ops-alerts"]
```

### Backup & DR

```yaml
backup:
  enabled: true
  schedule: "daily"                     # cron or "daily|weekly"
  retention: "30d"                      # Keep how long?
  targets:
    - type: "database|volume|file"
      source: "myservice_volume"
      exclude: ["/tmp", "*.log"]
      destination: "s3://backup-bucket"
```

### Networking

```yaml
networking:
  vlan: "vlan30"
  mtu: 1500
  dns_enabled: true
  dns_names:
    - "myservice.internal"
    - "myservice"
  firewall_rules:
    - from: "any"
      to: "myservice"
      port: "8080"
      action: "allow"
    - from: "myservice"
      to: "postgres"
      port: "5432"
      action: "allow"
```

### Metadata (Custom Properties)

```yaml
metadata:
  data_classification: "internal"       # For compliance
  compliance:
    - "GDPR"
    - "ISO27001"
  documentation_url: "https://..."
  support_channel: "#ops"
  sla:
    availability: "99.5%"
    response_time: "2h"
    resolution_time: "24h"
  cost:
    cpu_cost_per_month: "$50"
    storage_cost_per_month: "$25"
  tags_custom:
    service_mesh: "enabled"
    observability: "full"
```

---

## 📁 Full Service Directory Structure

```
services/applications/myservice/
├── service.yaml                 # ✅ REQUIRED
├── docker-compose.yml
├── Dockerfile                   # If building from source
├── README.md                    # Documentation
├── config/
│   ├── app.conf
│   └── settings.yml
├── scripts/
│   ├── init.sh                  # One-time setup
│   ├── health_check.sh
│   └── backup.sh
├── volumes/
│   └── .gitkeep
└── secrets/
    ├── .gitkeep
    └── README.md                # How secrets are managed via Vault
```

---

## 🔄 Service Lifecycle

### Phase 1: Definition
```
Create service.yaml ✓
  ↓
Specify interfaces ✓
  ↓
Define dependencies ✓
  ↓
Create docker-compose ✓
```

### Phase 2: Validation
```
./orchestration/validate_services.py services/applications/myservice
  ✓ Schema valid
  ✓ Dependencies resolvable
  ✓ Interfaces well-formed
  ✓ Secrets in plan
```

### Phase 3: Deployment
```
./orchestration/deploy.sh myservice
  ↓
Ansible discovers service ✓
  ↓
Provisions infrastructure (Terraform) ✓
  ↓
Deploys container (Docker) ✓
  ↓
Registers with Traefik (if expose=true) ✓
  ↓
Registers with Authelia (if auth=oidc) ✓
  ↓
Registers DNS (if dns=true) ✓
  ↓
Registers with NetBox (metadata) ✓
  ↓
Shows in Homepage (if category set) ✓
```

### Phase 4: Discovery
```
All systems now aware:
  • Traefik: knows how to route requests
  • Authelia: knows auth requirements
  • Pi-hole: knows DNS names & IPs
  • NetBox: knows full service topology
  • Homepage: knows where to display it
  • Monitoring: collecting metrics
  • Backups: running on schedule
```

---

## ✅ Example: Adding Jellyfin (Media Streaming)

```bash
mkdir -p IaC/services/applications/jellyfin
cd IaC/services/applications/jellyfin
```

**service.yaml:**

```yaml
name: jellyfin
version: "10.8.13"
tier: "application"
description: "Open-source media system (TV, movies, music)"
owner: "ops-team"

tags:
  expose: "true"
  ingress: "https"
  auth: "oidc"
  auth_provider: "authelia"
  network: "vlan30"
  dns: "true"
  tier: "application"
  category: "Media"
  icon: "play"
  sla: "high"
  backup: "weekly"

deployment:
  platform: "docker"
  image: "jellyfin/jellyfin:latest"

interfaces:
  - name: "web"
    protocol: "https"
    port: 443
    internal_port: 8096
    path: "/jellyfin"
    hostname: "jellyfin.internal"

dependencies:
  - name: postgres
    role: "metadata_cache"
    required: false
    interface: "tcp/5432"

monitoring:
  health_check: "http://localhost:8096/health"
  health_check_interval: "60s"

backup:
  enabled: true
  schedule: "weekly"
  targets:
    - type: "volume"
      source: "jellyfin_config"

networking:
  vlan: "vlan30"
  dns_enabled: true
  dns_names:
    - "jellyfin.internal"
    - "media.internal"

metadata:
  data_classification: "internal"
```

**docker-compose.yml:**

```yaml
services:
  jellyfin:
    image: jellyfin/jellyfin:latest
    container_name: jellyfin
    restart: unless-stopped
    environment:
      - JELLYFIN_DATA_DIR=/config
      - JELLYFIN_LOG_DIR=/config/log
    volumes:
      - jellyfin_config:/config
      - jellyfin_cache:/cache
      - /mnt/media:/media:ro              # External media storage
    networks:
      - services
    labels:
      traefik.enable: "true"
      traefik.http.routers.jellyfin.rule: "Host(`jellyfin.internal`)"
      traefik.http.routers.jellyfin.entrypoints: "websecure"
      traefik.http.routers.jellyfin.middlewares: "authelia@docker"
      traefik.http.services.jellyfin.loadbalancer.server.port: "8096"

volumes:
  jellyfin_config:
  jellyfin_cache:

networks:
  services:
    external: true
```

**Deploy it:**

```bash
./orchestration/deploy.sh jellyfin
```

---

## ⚠️ Common Patterns

### Pattern 1: Stateless Service (No Volume)
```yaml
deployment:
  platform: "docker"
  image: "nginx:latest"
  
interfaces:
  - name: "web"
    protocol: "https"
    port: 443
    internal_port: 80
```

### Pattern 2: Stateful Service (Database)
```yaml
deployment:
  platform: "docker"
  image: "postgres:15"
  
interfaces:
  - name: "database"
    protocol: "tcp"
    port: 5432
    
provides:
  - interface: "tcp:5432"
    for_services:
      - nextcloud
      - gitlab
    contract: "PostgreSQL"
```

### Pattern 3: Microservices (Multiple Containers)
```yaml
name: app-stack
tier: "application"
deployment:
  service_template: |
    backend:
      image: app-backend:latest
      depends_on:
        - database
    frontend:
      image: app-frontend:latest
      depends_on:
        - backend
    database:
      image: postgres:15
```

### Pattern 4: With External Storage (NAS)
```yaml
deployment:
  platform: "docker"
  service_template: |
    jellyfin:
      image: jellyfin:latest
      volumes:
        - jellyfin_config:/config
        - type: nfs
          source: "nas.internal:/mnt/media"
          target: /media
```

---

## 🐛 Troubleshooting

### Service won't start?
```bash
# Check logs
docker logs myservice

# Check dependencies
./orchestration/check_dependencies.sh myservice

# Validate config
./orchestration/validate_services.py services/applications/myservice
```

### Service not in homepage?
```bash
# Check if category tag is set
grep "category:" services/applications/myservice/service.yaml

# Resync homepage
./orchestration/sync_all.sh homepage
```

### Service not accessible?
```bash
# Check Traefik registration (if expose=true)
docker logs traefik | grep myservice

# Check Authelia (if auth=oidc)
docker logs authelia | grep myservice

# Check DNS (if dns=true)
nslookup myservice.internal
```

### Secrets not working?
```bash
# Verify Vault has the secret
vault kv get secret/myservice/...

# Check Ansible is loading secrets
ansible-playbook -i inventory/services.yml playbooks/deploy_service.yml --tags=myservice -vv
```

---

## 📚 Next Steps

1. **Add your first service** using this guide
2. **Run the full deployment**: `./orchestration/deploy.sh --all`
3. **Check the dashboard**: Homepage should show all services
4. **Monitor**: Prometheus/Grafana for metrics
5. **Iterate**: Refine service.yaml as you learn

