---
type: Topic
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[ELEMENTARY_PARTICLES_VM_DEPLOYMENT]]"
  - "[[VM_DEPLOYMENT_GUIDE]]"
  - "[[VM_SERVICE_INTEGRATION_GUIDE]]"
---

# VM Elementary Particles - Deployment Architecture

> **Mission:** Deploy VMs on Proxmox using the same atomic, composable, tag-driven approach as services. Each VM is an elementary particle with clearly defined interfaces, dependencies, and tier placement.

---

## 🧬 Core Concepts: VM as Particle

### 1. **VM Classification Hierarchy**

```
VMs (Host Machines) → Services (Containers) → Interfaces (Connections)

TIER-0 VMs: Platform Infrastructure
  └─ Terraform, Vault, NetBox admin hosts
  
TIER-1 VMs: Core Infrastructure Hosts
  └─ Traefik, Authelia, Pi-hole, Kubernetes masters
  
TIER-2 VMs: Storage & Compute Nodes
  └─ Postgres, Redis, MinIO, compute workers  ← pg-01 lives here
  └─ Ceph, ZFS, backup targets
  
TIER-3 VMs: Application Hosts
  └─ Docker daemon hosts, app servers
  
TIER-4 VMs: Specialized Workloads
  └─ GPU compute, ML training, analytics
```

### 2. **VM Elementary Particle Definition**

Each VM is defined by its manifest (`vm.yaml`):

```yaml
# Core Identity
name: "pg-01"                          # Unique fqdn-style identifier
type: "vm"                             # Always "vm" (vs service, container)
tier: "storage"                        # TIER-0 through TIER-4

# Infrastructure
infrastructure:
  hypervisor: "proxmox"                # Deployment target
  template: "ubuntu-22.04-cloud"       # Base template/image
  provision_method: "terraform"        # How to provision
  config_method: "ansible"             # How to configure
  
# Resource Profile
resources:
  cpus: 2                              # Processor cores
  memory_gb: 4                         # RAM in GB
  storage:
    - name: "root"
      size_gb: 30
      datastore_id: "postgres"         # Storage location
      io_profile: "production"
    - name: "wal"
      size_gb: 10
      datastore_id: "postgres"         # WAL on fast storage
      io_profile: "high-iops"
  network:
    interfaces:
      - name: "eth0"
        vlan: "vlan40"                 # Storage VLAN
        dhcp: false
        gateway: "10.0.40.1"
        ip: "10.0.40.10"

# Roles/Personas
roles:
  primary: "database"                  # What's its main job?
  secondary:
    - "datastore"
    - "backup-target"

# Dependencies
dependencies:
  - vm: "vault-01"                     # Needs this VM running first
    purpose: "secrets"
    interface: "https/8200"
  - service: "monitoring-prometheus"    # Needs this service
    purpose: "metrics-scrape"
    interface: "http/9090"

# Service Deployments (What runs on this VM)
services_deployed:
  - name: "postgres"                   # Service name
    version: "15.2"
    platform: "baremetal"              # baremetal vs docker vs k8s
    storage_role: "primary-datastore"
    interfaces:
      - protocol: "tcp"
        port: 5432
        vlan: "vlan40"
        consumers: ["app-01", "analytics-01"]

# Tags (drives automation/policy)
tags:
  # Operational
  tier: "storage"
  environment: "production"
  owner: "dba-team"
  criticality: "critical"
  
  # Lifecycle
  auto_scale: "false"                  # Can't scale DB VMs easily
  backup: "daily"                      # Backup strategy
  patching_window: "sunday-02:00"      # When to update
  patching_strategy: "rolling"         # Solo VMs = manual
  
  # Network/Security
  network: "vlan40"                    # Primary VLAN
  dns: "true"                          # Register as pg-01.internal
  exposed: "false"                     # Not internet-facing
  encryption: "required"               # All comms encrypted
  
  # Application-specific
  postgres_role: "primary"             # Master or replica?
  wal_enabled: "true"                  # Write-ahead logging
  streaming_replication: "false"       # (add replicas later)
  backup_format: "wal"                 # WAL archive instead of snapshots
  
  # Monitoring
  monitoring: "prometheus"
  alert_channel: "pagerduty-dba"
  
  # Custom metadata
  data_classification: "sensitive"
  sla: "99.9%"
  runbook_url: "https://docs.internal/postgres"

# Configuration Sources (Secrets management)
config:
  providers:
    - type: "vault"                    # Fetch from Vault
      path: "secret/postgres/pg-01"
      items:
        - postgres_password
        - replication_user_password
        - backup_password
    - type: "envfile"                  # Or from .env
      path: ".env.pg-01"               # Local encrypted .env
      items:
        - POSTGRES_DB
        - POSTGRES_USER
        - PG_WAL_LEVEL
        - PG_MAX_WAL_SENDERS

# Ansible Variables (passed to provisioner)
ansible:
  groups:
    - "databases"
    - "tier2_storage"
    - "postgres_cluster"
  roles:
    - "base"                           # OS setup
    - "security"                       # SSH, firewall
    - "postgres"                       # DB installation
    - "postgres-wal"                   # WAL configuration
    - "monitoring-agent"               # Prometheus exporter
    - "backup-agent"                   # Backup config
  
  variables:
    postgres_version: "15"
    postgres_data_dir: "/var/lib/postgresql/15/main"
    postgres_wal_dir: "/wal/pg_wal"
    postgres_max_connections: 200
    postgres_shared_buffers: "1GB"
    postgres_effective_cache_size: "2GB"
    postgres_wal_level: "replica"      # For streaming replication
    postgres_max_wal_senders: 3
    postgres_wal_keep_size: "500MB"    # Keep 500MB of WAL

# Metrics & Observability
observability:
  prometheus:
    scrape_interval: "15s"
    metrics:
      - "postgres_up"
      - "pg_stat_statements"
      - "pg_buffercache"
  alerts:
    - "PostgresDown"
    - "PostgresDiskFull"
    - "PostgresSlowQueries"
  logging:
    - type: "syslog"
      destination: "graylog-01:5140"
    - type: "audit"
      destination: "vault-01:8200"

# Network policies
network_policies:
  ingress:
    - from_vlan: "vlan30"              # App VLAN can connect
      to_port: 5432
      protocol: "tcp"
    - from_service: "pg-backup-agent"
      to_port: 5432
      protocol: "tcp"
  egress:
    - to_vm: "vault-01"                # Can reach Vault
      to_port: 8200
    - to_service: "monitoring-prometheus"

# Backup & Disaster Recovery
backup:
  enabled: true
  strategy: "wal-archiving"            # WAL archive to MinIO
  schedule: "continuous"               # Continuous WAL shipping
  target: "s3://backup-pg01"
  retention_days: 30
  verification: "weekly"               # Test restore weekly
  recovery_sla_hours: 1

# Capacity & Growth Planning
capacity:
  current_disk_usage_gb: 15
  projected_annual_growth_percent: 20
  max_connections_planning: 500
  next_resize_date: "2026-10-01"

provides:
  - interface: "postgres:5432"
    consumers: ["app-01", "app-02", "analytics-01"]
    contract: "PostgreSQL protocol, v15.2"
    sla: "99.9% availability"

# Deployment Sequence
deployment:
  order: 1                             # Deploy VM first, services after
  depends_on:
    - "vault-01"
    - "network-vlan40"
  parallel_with: []
  
  terraform_module: "proxmox-vm"
  terraform_workspace: "prod"
  
  post_deploy_checks:
    - "ping pg-01.internal"
    - "ssh connectivity"
    - "systemctl status postgresql"
    - "postgres health check"
    - "replication status" (if replica)

# Cost & Resources
metadata:
  cost_center: "database-ops"
  monthly_cost_usd: 85
  deprecation_date: null
  support_contact: "dba-team@company"
  documentation_url: "https://wiki.internal/pg-01"
  created_date: "2024-01-15"
  last_modified: "2026-04-28"
```

---

## 📊 VM Lifecycle Map

```
1. MANIFEST VALIDATION
   ↓
   [vm.yaml] → [json-schema validation] → ✓ Valid
   
2. TERRAFORM PROVISIONING (IaC)
   ↓
   [main.tf uses vm.yaml] → [Proxmox API] → VM created
   
3. ANSIBLE CONFIGURATION (Config Mgmt)
   ↓
   [site.yml] → [inventory = services.yml] → OS setup + Postgres install
   
4. SERVICE DISCOVERY & REGISTRATION
   ↓
   [service-discovery script] → [NetBox API] → VM registered
   [traefik-generator] → Traefik routes (if expose: true)
   [pihole-dns] → DNS entry (if dns: true)
   
5. RUNTIME MANAGEMENT
   ↓
   [monitoring] → Prometheus scrape
   [backup-agent] → WAL archiving
   [patching] → Sunday updates
```

---

## 🔄 Comparison: Services vs VMs

| Aspect | Service | VM |
|--------|---------|-----|
| **Manifests** | `service.yaml` | `vm.yaml` |
| **Deployment** | Docker Compose | Terraform |
| **Config** | Environment vars + Compose | Ansible playbooks |
| **Scalability** | Horizontal (replicas) | Vertical only |
| **Cold Start** | Seconds | Minutes |
| **Template** | Docker image | Proxmox template |
| **Storage** | Volumes | VM disks |
| **Networking** | Docker networks | VLANs + physical NIC |
| **Discovery** | Service registry | NetBox + DNS |

---

## 📋 Atomic Units Within a VM

Even a single VM can have multiple "roles":

```yaml
VM: pg-01
├── Role: PostgreSQL primary
│   ├── Service: postgres (7432)
│   ├── Storage: /var/lib/postgresql/15/main
│   └── Interface: postgres:5432
│
├── Role: WAL archiver
│   ├── Service: pg_basebackup (integrated)
│   ├── Storage: /wal/pg_wal
│   └── Interface: S3:backup-pg01
│
├── Role: Monitoring source
│   ├── Service: postgres_exporter
│   ├── Port: 9187
│   └── Interface: metrics:9187
│
└── Role: Backup target
    ├── Service: WAL archiver daemon
    ├── Network: vlan40 + s3 backend
    └── Interface: S3/backup API
```

Each role has its own:
- Configuration (in Ansible)
- Storage (in Terraform)
- Monitoring (in Prometheus)
- Documentation

---

## 🚀 Deployment Flow for pg-01

```
User runs: ./orchestration/deploy_vm.sh pg-01
    ↓
[Step 1] Load vm.yaml manifest
    ↓
[Step 2] Validate schema + dependencies
    ↓
[Step 3] Generate Terraform from vm.yaml
    ↓
[Step 4] terraform apply (creates VM on Proxmox)
    ↓
[Step 5] Wait for VM to boot + SSH accessible
    ↓
[Step 6] Generate ansible inventory from vm.yaml
    ↓
[Step 7] Run Ansible playbooks:
         - base role (SSH, firewall)
         - postgres role (install postgres)
         - postgres-wal role (WAL config)
         - monitoring-agent role (exporter)
    ↓
[Step 8] Post-deploy validation:
         - PostgreSQL port 5432 listening
         - Replication working
         - Backup archiving configured
    ↓
[Step 9] Register in NetBox:
         - VM object
         - Network interfaces
         - Services provided
    ↓
[Step 10] Register DNS (if dns: true)
    ↓
[Step 11] Start monitoring + alerting
    ↓
SUCCESS: pg-01 ready for applications
```

---

## 🔐 Secrets Management Flow

```
┌─────────────────────────────┐
│   Vault (Source of Truth)   │
│  secret/postgres/pg-01/...  │
└─────────────┬───────────────┘
              │
              ├─→ [Terraform] 
              │   - Initial postgres_password
              │   - Replication users
              │   - Backup passwords
              │   └─→ Ansible inventory
              │
              └─→ [Ansible]
                  - Fetch from Vault via vault CLI
                  - Write to /etc/postgres/.pgpass
                  - Set via psql commands
                  - Restrict permissions (chmod 600)
```

---

## 📂 File Structure for VM Definition

```
IaC/
├── terraform/
│   ├── modules/
│   │   └── proxmox-vm/              # Generic VM module
│   │       ├── main.tf
│   │       ├── variables.tf
│   │       └── outputs.tf
│   │
│   ├── environments/
│   │   └── prod/
│   │       └── postgres-vms/
│   │           ├── pg-01.tf         # Generated from vm.yaml
│   │           ├── pg-02.tf         # Replica (future)
│   │           └── terraform.tfvars
│   │
│   └── generators/
│       └── vm_to_terraform.py        # vm.yaml → .tf
│
├── ansible/
│   ├── roles/
│   │   ├── postgres/
│   │   ├── postgres-wal/            # WAL-specific config
│   │   ├── postgres-replica/        # Replica setup
│   │   └── postgres-backup/         # Backup agent
│   │
│   ├── playbooks/
│   │   ├── provision_vms.yml        # Main playbook
│   │   └── configure_postgres.yml   # DB-specific
│   │
│   ├── inventory/
│   │   └── services.yml             # SINGLE SOURCE OF TRUTH
│   │
│   └── group_vars/
│       ├── postgres_cluster/
│       └── tier2_storage/
│
├── config/
│   ├── vm.schema.json               # VM manifest schema
│   └── vm-examples/
│       └── pg-01.yaml               # Concrete example
│
├── services/
│   └── storage/
│       └── postgres/
│           └── vm.yaml              # Alternative location?
│
└── orchestration/
    ├── deploy_vm.sh                 # Deploy single VM
    ├── vm_to_terraform.py           # Generate .tf from manifest
    ├── validate_vms.py              # Schema validation
    └── discover_vms.py              # Inventory discovery
```

---

## 🎯 Key Takeaways

1. **VM as Atomic Particle**: Just like services, VMs have:
   - Clear identity & tier placement
   - Dependencies (other VMs/services it needs)
   - Interfaces (what ports/protocols/networks)
   - Tags (driving automation)
   - Manifests (YAML definitions)

2. **Single Source of Truth**: `services.yml` in Ansible inventory
   - Lists all VMs and services
   - All tags, configuration, dependencies
   - Drives Terraform, Ansible, NetBox, Monitoring

3. **Separation of Concerns**:
   - **Terraform**: Only infrastructure (VMs, networks, storage)
   - **Ansible**: Only configuration (packages, settings, services)
   - **Scripts**: Orchestration (discovery, validation, registration)

4. **Configuration Layering**:
   - `vm.yaml` → Most specific (this VM)
   - `group_vars/postgres_cluster/` → Cluster settings
   - `group_vars/tier2_storage/` → Tier defaults
   - `defaults/main.yml` → Role defaults

5. **Secrets Flow**:
   - Source: Vault
   - Retrieved: By Terraform + Ansible via vault CLI
   - Used: In Terraform for initial setup, Ansible for config
   - Stored: Only in Vault + restricted files in /etc/postgres/

6. **Multi-Tenancy & Isolation**:
   - Each VM has its own Ansible inventory entry
   - Each role is scoped (postgres_cluster group)
   - Tags separate concerns (monitoring, backup, patching)
   - Network policies enforce isolation
