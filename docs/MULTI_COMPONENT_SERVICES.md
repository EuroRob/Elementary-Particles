---
type: Topic
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[TIER_PATTERNS]]"
  - "[[DOCKER_IMAGE_GUIDE]]"
  - "[[INTERCONNECTIONS]]"
  - "[[HOW_TO_ADD_SERVICE]]"
---

# Multi-Component Services (Service Suites)

How to deploy distributed applications like Immich across multiple VMs using the modular architecture.

---

## 🎯 Core Concept: Service Suite

A **Service Suite** is a collection of related services that work together but run on different VMs/nodes.

```
Immich Suite:
  ├─ immich-server     (web-vm)          TIER-3
  ├─ immich-web       (web-vm)          TIER-3
  ├─ immich-ml        (gpu-vm)          TIER-3
  ├─ redis            (web-vm)          TIER-2
  └─ postgres         (postgres-vm)     TIER-2
```

**Each is its own service with explicit dependencies:**
```
postgres → redis → immich-server → immich-web
                ↘ immich-ml ↙
```

---

## 📁 Folder Structure (Recommended)

```
services/
└── applications/
    └── immich/                           # Service Suite Grouping
        ├── immich-server/
        │   ├── service.yaml              # Component 1
        │   ├── docker-compose.yml
        │   └── .env.example
        │
        ├── immich-web/
        │   ├── service.yaml              # Component 2
        │   ├── docker-compose.yml
        │   └── README.md
        │
        └── immich-ml/
            ├── service.yaml              # Component 3
            ├── docker-compose.yml
            ├── Dockerfile
            └── README.md
```

**Alternative (flat):**
```
services/
└── applications/
    ├── immich-server/
    ├── immich-web/
    └── immich-ml/
```

Either works. **I recommend the grouped structure** for clarity—humans can quickly see "this is Immich."

---

## 🔑 Key Principle

Each component has its own:
- ✅ `service.yaml` (definition)
- ✅ `docker-compose.yml` (how to run it)
- ✅ Explicit dependencies
- ✅ Deployment target (which VM)
- ✅ Resource requirements

**But they're linked via dependencies:**

```yaml
# immich-server depends on postgres + redis
# immich-ml depends on postgres + redis
# immich-web depends on immich-server
```

---

## 📋 Real Example: Immich Service Suite

### Component 1: immich-server

```yaml
# services/applications/immich/immich-server/service.yaml
---
name: immich-server
version: "1.105.1"
tier: "application"

description: "Immich backend API server"
owner: "media-team"
runbook: "https://docs.internal/immich"

tags:
  expose: "false"              # Internal API
  auth: "none"                 # Trust internal network
  network: "vlan30"
  tier: "application"
  category: "Media"
  icon: "server"
  sla: "high"                  # Important but not critical
  backup: "daily"              # Configuration backup
  environment: "production"

deployment:
  platform: "docker"
  namespace: "vlan30"
  deployment_target: "web-vm"  # ← EXPLICIT: Deploy to web-vm
  replicas: 1
  
  image: "ghcr.io/immich-app/immich-server:release"
  
  service_template: |
    immich-server:
      image: ghcr.io/immich-app/immich-server:release
      container_name: immich-server
      environment:
        DB_HOST: postgres-01              # ← Explicit reference to postgres VM
        DB_PORT: 5432
        DB_DATABASE: immich
        DB_USERNAME: immich
        DB_PASSWORD: ${IMMICH_DB_PASSWORD}
        REDIS_SOCKET_URL: redis://redis:6379
        UPLOAD_LOCATION: /usr/src/app/upload
        LOG_LEVEL: log
      volumes:
        - /opt/immich/server:/usr/src/app/upload
      networks:
        - services
      depends_on:
        - redis
      restart: unless-stopped
      labels:
        traefik.enable: "false"            # Not exposed directly

interfaces:
  - name: "api"
    protocol: "http"
    port: 3001
    internal_port: 3001
    hostname: "immich-server.internal"    # Internal use only

dependencies:
  - name: postgres
    role: "primary_database"
    required: true
    interface: "tcp/5432"
    authentication: "password"
    tags:
      tier: "storage"
      
  - name: redis
    role: "session_cache"
    required: true
    interface: "tcp/6379"
    tags:
      tier: "storage"

provides:
  - interface: "http:3001"
    for_services:
      - immich-web
    contract: "REST API"
    authentication: "none"

secrets:
  - name: IMMICH_DB_PASSWORD
    vault_path: "secret/immich/db_password"
    required: true

monitoring:
  health_check: "http://localhost:3001/api/admin/system-config"
  health_check_interval: "30s"
  
  metrics:
    - path: "/metrics"
      port: 3001
      format: "prometheus"

backup:
  enabled: true
  schedule: "daily"
  targets:
    - type: "volume"
      source: "immich_server_config"

networking:
  vlan: "vlan30"
  deployment_target: "web-vm"        # ← Deploy to web VM
  dns_enabled: false
  firewall_rules:
    - from: "immich-web"
      to: "immich-server"
      port: "3001"
      action: "allow"
    - from: "immich-server"
      to: "postgres-vm"
      port: "5432"
      action: "allow"

metadata:
  suite: "immich"                    # ← Mark as part of suite
  component: "backend"
  data_classification: "confidential"
```

**docker-compose.yml (immich-server):**
```yaml
version: '3.8'

services:
  immich-server:
    image: ghcr.io/immich-app/immich-server:release
    container_name: immich-server
    environment:
      DB_HOST: postgres-01              # ← CRITICAL: Point to postgres VM
      DB_PORT: 5432
      DB_DATABASE: immich
      DB_USERNAME: immich
      DB_PASSWORD: ${IMMICH_DB_PASSWORD}
      REDIS_SOCKET_URL: redis://redis:6379  # ← Same VM
      UPLOAD_LOCATION: /usr/src/app/upload
      LOG_LEVEL: log
    volumes:
      - immich_server_uploads:/usr/src/app/upload
      - immich_server_thumbs:/usr/src/app/server/upload/thumbs
      - immich_server_logs:/var/log
    networks:
      - services
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3001/api/admin/system-config"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  immich_server_uploads:
  immich_server_thumbs:
  immich_server_logs:

networks:
  services:
    external: true
```

---

### Component 2: immich-web (Frontend)

```yaml
# services/applications/immich/immich-web/service.yaml
---
name: immich-web
version: "1.105.1"
tier: "application"

description: "Immich web UI frontend"
owner: "media-team"

tags:
  expose: "true"               # ← User-facing
  ingress: "https"
  auth: "oidc"                 # ← Protected by Authelia
  auth_provider: "authelia"
  dns: "true"
  network: "vlan30"
  tier: "application"
  category: "Media"
  icon: "photo-line"
  color: "#4C8EDF"
  sla: "high"
  backup: "weekly"

deployment:
  platform: "docker"
  deployment_target: "web-vm"  # ← On same VM as immich-server
  
  image: "ghcr.io/immich-app/immich-web:release"
  
  service_template: |
    immich-web:
      image: ghcr.io/immich-app/immich-web:release
      container_name: immich-web
      environment:
        IMMICH_API_URL_EXTERNAL: https://immich.internal/api
        IMMICH_API_URL_INTERNAL: http://immich-server:3001/api
      networks:
        - services
      depends_on:
        - immich-server              # ← Explicit dependency
      restart: unless-stopped

interfaces:
  - name: "web"
    protocol: "https"
    port: 443
    internal_port: 80
    hostname: "immich.internal"
    path: "/"

dependencies:
  - name: immich-server
    role: "api_backend"
    required: true               # ← Cannot start without it
    interface: "http:3001"

provides:
  - interface: "https:443"
    for_services: []
    contract: "Web UI"

networking:
  vlan: "vlan30"
  deployment_target: "web-vm"
  dns_enabled: true
  dns_names:
    - "immich.internal"
    - "photos.internal"

metadata:
  suite: "immich"
  component: "frontend"
```

**docker-compose.yml (immich-web):**
```yaml
version: '3.8'

services:
  immich-web:
    image: ghcr.io/immich-app/immich-web:release
    container_name: immich-web
    environment:
      IMMICH_API_URL_EXTERNAL: https://immich.internal/api
      IMMICH_API_URL_INTERNAL: http://immich-server:3001/api
    networks:
      - services
    depends_on:
      - immich-server
    restart: unless-stopped
    labels:
      traefik.enable: "true"
      traefik.http.routers.immich.rule: "Host(`immich.internal`)"
      traefik.http.routers.immich.entrypoints: "websecure"
      traefik.http.routers.immich.middlewares: "authelia@docker"
      traefik.http.services.immich.loadbalancer.server.port: "80"

networks:
  services:
    external: true
```

---

### Component 3: immich-ml (GPU-Optimized)

```yaml
# services/applications/immich/immich-ml/service.yaml
---
name: immich-ml
version: "1.105.1"
tier: "application"

description: "Immich machine learning (facial recognition, classification)"
owner: "media-team"

tags:
  expose: "false"              # Internal only
  auth: "none"
  network: "vlan30"
  tier: "application"
  category: "Media"
  sla: "medium"                # Best-effort (non-blocking)
  backup: "never"              # Stateless ML cache

deployment:
  platform: "docker"
  deployment_target: "gpu-vm"  # ← CRITICAL: Deploy to GPU VM
  replicas: 1
  
  resources:
    cpu: "4"                   # GPU VM has 8 cores
    memory: "8Gi"              # GPU VM has 16GB
    gpu: "1"                   # ← GPU required
  
  image: "ghcr.io/immich-app/immich-machine-learning:release"
  
  service_template: |
    immich-ml:
      image: ghcr.io/immich-app/immich-machine-learning:release
      container_name: immich-ml
      environment:
        TRANSFORMERS_CACHE: /cache
        MACHINE_LEARNING_WORKERS: 2
        MACHINE_LEARNING_WORKER_TIMEOUT: 120
      volumes:
        - immich_ml_cache:/cache
      networks:
        - services
      deploy:                   # ← GPU reservation
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
      restart: unless-stopped

interfaces:
  - name: "api"
    protocol: "http"
    port: 3003
    internal_port: 3003
    hostname: "immich-ml.internal"

dependencies:
  - name: postgres
    role: "model_state"
    required: false           # ← Can gracefully degrade
    interface: "tcp/5432"

provides:
  - interface: "http:3003"
    for_services:
      - immich-server       # ← Server calls ML for processing
    contract: "ML API"

monitoring:
  health_check: "http://localhost:3003/api/models"
  health_check_interval: "60s"

networking:
  vlan: "vlan30"
  deployment_target: "gpu-vm"  # ← Explicit GPU VM
  dns_enabled: false
  firewall_rules:
    - from: "immich-server"
      to: "immich-ml"
      port: "3003"
      action: "allow"

metadata:
  suite: "immich"
  component: "ml-worker"
  hardware_requirement: "gpu"  # ← Important tag
```

---

## 🔗 Dependency Chain

When you deploy with `./orchestration/deploy.sh immich`, this happens:

```
1. Discover all immich services:
   - immich-server
   - immich-web
   - immich-ml
   
2. Build dependency graph:
   postgres → redis
            ↓
    immich-server → immich-web
              ↘ immich-ml ↙
   
3. Topo-sort:
   [1] postgres       (storage, no deps)
   [2] redis          (storage, no deps)
   [3] immich-server  (app, depends on postgres + redis) → deploy to web-vm
   [4] immich-ml      (app, optional postgres) → deploy to gpu-vm
   [5] immich-web     (app, depends on immich-server) → deploy to web-vm
   
4. Deploy in order:
   postgres-vm:   start postgres
   web-vm:        start redis, start immich-server, start immich-web
   gpu-vm:        start immich-ml
   
5. Verify:
   immich-web → https://immich.internal ✓ (Traefik routes, Authelia protects)
   immich-server ← https://immich.internal/api (internal via server)
   immich-ml ← http://immich-ml:3003 (called by server)
```

---

## 🚀 Deployment

### Deploy entire Immich suite:
```bash
# Deploy all immich components
./orchestration/deploy.sh immich-server
./orchestration/deploy.sh immich-web
./orchestration/deploy.sh immich-ml

# OR deploy all at once with discovery
./orchestration/deploy.sh --all
```

### Selective deployments:
```bash
# Update just the ML model (no service restart needed if optional dep)
./orchestration/deploy.sh immich-ml

# Update just the frontend
./orchestration/deploy.sh immich-web --service web-vm

# Scale the backend
# (edit service.yaml, increase replicas: 2)
./orchestration/deploy.sh immich-server --replicas 2
```

---

## 📊 VM Placement Matrix

| Component | Tier | Tier | Host VM | Reason |
|-----------|------|------|---------|--------|
| postgres | TIER-2 | storage | postgres-vm | Dedicated, always-on |
| redis | TIER-2 | storage | web-vm | Lightweight, needed by servers |
| immich-server | TIER-3 | app | web-vm | Medium CPU, no GPU |
| immich-web | TIER-3 | app | web-vm | Light CPU/memory |
| immich-ml | TIER-3 | app | gpu-vm | GPU required, high CPU |

---

## 🔥 Advanced: Service Suite Patterns

### Pattern 1: All-on-One
```yaml
# For small deployments or dev
deployment_target: "web-vm"    # All components
```

### Pattern 2: Distributed (Recommended)
```yaml
# Immich backend
deployment_target: "web-vm"

# Immich ML (GPU-intensive)
deployment_target: "gpu-vm"

# Database
deployment_target: "postgres-vm"
```

### Pattern 3: Micro-services (Future)
```yaml
# Each component could scale independently:
immich-server-1 → web-vm-1
immich-server-2 → web-vm-2
immich-ml-1     → gpu-vm-1
immich-ml-2     → gpu-vm-2  # Multiple GPU workers
```

---

## 🧩 Service Suite Metadata

Add to each component to show they're related:

```yaml
metadata:
  suite: "immich"              # ← Marks as part of suite
  component: "backend|frontend|ml-worker"
  suite_version: "1.105.1"
  suite_docs: "https://docs.internal/immich"
```

You could then query:

```bash
# Find all Immich components
python3 orchestration/discover_services.py | jq '.services | with_entries(select(.value.metadata.suite=="immich"))'

# Deployment order for Immich only
python3 orchestration/discover_services.py | jq '.stats.deployment_order[] | select(startswith("immich"))'
```

---

## 🧠 Design Principles

✅ **One service per elementary particle**
- immich-server is its own deployable unit
- immich-ml is separate
- Keeps concerns isolated

✅ **Explicit dependencies**
- immich-web explicitly depends on immich-server
- immich-server explicitly depends on postgres
- Deployment order calculated automatically

✅ **Flexible VM placement**
- Each component specifies its `deployment_target`
- Can move components between VMs in service.yaml alone
- No need to change docker-compose

✅ **Graceful failure handling**
- `required: true` = must have or deploy fails
- `required: false` = nice-to-have, degrades gracefully
- immich-ml is optional, server continues if it's down

---

## 🔄 Migration Path (for your existing Immich)

### Step 1: Extract current config
```bash
# From old immich-compose
docker compose config > immich-old-compose.yml
docker exec immich-server env | grep IMMICH > env-vars.txt
```

### Step 2: Create service definitions
```bash
mkdir -p services/applications/immich/{immich-server,immich-web,immich-ml}
cd services/applications/immich/immich-server
cp ../../_templates/service.example.yaml service.yaml
# Edit with immich-server specifics

# (repeat for immich-web, immich-ml)
```

### Step 3: Extract docker-compose pieces
```bash
# Split old monolithic docker-compose into:
# immich-server/docker-compose.yml
# immich-web/docker-compose.yml
# immich-ml/docker-compose.yml
```

### Step 4: Update inter-service references
**OLD (single compose):**
```yaml
services:
  immich-server:
    depends_on:
      - redis
      - postgres
  immich-web:
    depends_on:
      - immich-server
```

**NEW (separate composes):**
```yaml
# immich-server/docker-compose.yml
services:
  immich-server:
    environment:
      DB_HOST: postgres-01          # Reference external postgres
      REDIS_SOCKET_URL: redis://redis:6379  # Reference on same web-vm

# immich-web/docker-compose.yml
services:
  immich-web:
    environment:
      IMMICH_API_URL_INTERNAL: http://immich-server:3001  # Reference
```

### Step 5: Validate and deploy
```bash
python3 orchestration/validate_services.py services/applications/immich/immich-server
python3 orchestration/validate_services.py services/applications/immich/immich-web
python3 orchestration/validate_services.py services/applications/immich/immich-ml

./orchestration/deploy.sh immich-server
./orchestration/deploy.sh immich-web
./orchestration/deploy.sh immich-ml
```

---

## 🎯 One More Thing: GitOps Updates

When Immich releases a new version:

```bash
# Just update the version in ONE file:
vim services/applications/immich/immich-server/service.yaml
# Change: version: "1.105.1" → "1.106.2"

# And deploy:
./orchestration/deploy.sh immich-server

# That's it! All configuration, secrets, dependencies, placement are kept
```

No more hunting for docker-compose files, environment variables scattered everywhere, or mystery environment for why something works.

---

## ✅ Summary

**For multi-component services like Immich:**

1. **Create one service.yaml per component** (immich-server, immich-web, immich-ml)
2. **Each has own docker-compose.yml** (how to run on that node)
3. **Link via dependencies** (immich-web → immich-server)
4. **Specify deployment_target** (web-vm, gpu-vm, etc)
5. **Tags drive everything else** (expose, auth, monitoring, backups)
6. **Deploy with one command** (`./deploy.sh immich-server` or `--all`)

**Result:**
- ✅ Modular components
- ✅ Explicit dependencies
- ✅ Clear VM placement
- ✅ Automatic deployment order
- ✅ Individual component lifecycle
- ✅ Infrastructure as code (git-tracked)
