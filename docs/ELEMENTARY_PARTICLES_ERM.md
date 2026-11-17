---
type: Topic
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[UNIFIED_RELATIONSHIP_MODEL]]"
  - "[[RELATIONSHIPS_INDEX]]"
  - "[[NETBOX_CUSTOM_FIELDS_SETUP]]"
  - "[[INTERCONNECTIONS]]"
---

# Elementary Particles Unified Relationship Model (ERM)

## Overview

This document defines how all components of your infrastructure (VMs, Docker Containers, Python Scripts, Services, and NetBox DCIM) interconnect as a unified system under the "elementary particles" architecture.

---

## 1. Entity-Relationship Diagram (ERM)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ELEMENTARY PARTICLES SYSTEM                        │
└─────────────────────────────────────────────────────────────────────────────┘

                         ┌──────────────────────────┐
                         │   NETBOX DCIM/IPAM       │
                         │  (Central Registry)      │
                         │                          │
                         │ - VMs                    │
                         │ - Interfaces             │
                         │ - IP Addresses           │
                         │ - Services               │
                         │ - Relationships          │
                         └────────┬─────────────────┘
                                  │
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
            ┌─────────────┐  ┌─────────────┐  ┌──────────────┐
            │   VMs       │  │ Containers  │  │  Python      │
            │  (TIER 0-4) │  │  (Docker)   │  │  Scripts     │
            │             │  │             │  │              │
            │ - pg-01     │  │ - n8n       │  │ - deploy.sh  │
            │ - web-01    │  │ - postgres  │  │ - validate_  │
            │ - redis-01  │  │ - traefik   │  │   services.py│
            │ - minIo-01  │  │ - prometheus│  │ - discover_  │
            │             │  │ - exporters │  │   services.py│
            └─────┬────────┘  └─────┬──────┘  │ - netbox_    │
                  │                 │         │   vm_sync.py │
                  │                 │         │ - compose_   │
                  │                 │         │   stack.py   │
                  │                 │         │ - & 50+ more │
                  │                 │         └──────┬───────┘
                  │                 │                │
                  └────────┬────────┴────────┬───────┘
                           │                 │
                           ▼                 ▼
                    ┌──────────────┐  ┌───────────────┐
                    │   Services   │  │  Orchestr.    │
                    │(service.yaml)│  │    Engine     │
                    │              │  │               │
                    │ - nextcloud  │  │ - Terraform   │
                    │ - postgres   │  │ - Ansible     │
                    │ - traefik    │  │ - Scripts     │
                    │ - n8n        │  │ - Monitors    │
                    │ - grafana    │  └───────────────┘
                    │ - vault      │
                    └──────────────┘

                       ┌─────────────────────────┐
                       │  Relationships Registry │
                       │  (relationships.yaml)   │
                       │                         │
                       │ VM ←→ Container ←→ Script
                       │ Container ←→ Service
                       │ Script ←→ VM
                       │ All ←→ NetBox
                       └─────────────────────────┘
```

---

## 2. Detailed Relationship Types

### 2.1 VM ↔ Container Relationships

**Definition:** Which Docker containers run on which VMs

```
VM (Elementary Particle)
  │
  ├─ name: web-01
  └─ runs_containers:
     ├─ n8n (container)
     ├─ docker_exporter (container)
     └─ node_exporter (container)

Container (Running Application)
  │
  ├─ name: n8n
  ├─ image: n8n-io/n8n:latest
  └─ runs_on_vm: web-01
```

**NetBox Representation:**
- Custom field on VM: `docker_containers` (list)
- Custom field on Service (if different): `hosting_vm`
- Custom relationship: VM ← hosts → Container

---

### 2.2 Container ↔ Script Relationships

**Definition:** Which Python scripts manage/orchestrate which containers

```
Container (Application)
  │
  ├─ name: n8n
  └─ managed_by_scripts:
     ├─ IaC/orchestration/compose_stack.py (generates docker-compose)
     ├─ IaC/orchestration/deploy.sh (starts container)
     ├─ IaC/ansible/playbooks/configure_docker_web.yml (configures)
     └─ Integration/calendar_event_enricher.py (uses n8n API)

Script (Automation)
  │
  ├─ name: compose_stack.py
  └─ manages_containers:
     ├─ n8n
     ├─ traefik
     ├─ postgres (for services)
     └─ prometheus
```

**NetBox Representation:**
- Custom field on Service: `orchestration_scripts` (list)
- Custom relationship: Script ← manages → Container
- Service notes field: script dependencies

---

### 2.3 Script ↔ VM Relationships

**Definition:** Which Python scripts provision/configure which VMs

```
VM (Elementary Particle)
  │
  ├─ name: pg-01
  └─ provisioned_by:
     ├─ Terraform (via: IaC/terraform/modules/proxmox-vm/)
     ├─ Ansible (via: IaC/ansible/playbooks/configure_postgres.yml)
     └─ Python scripts:
        ├─ IaC/orchestration/deploy_vm.sh
        ├─ IaC/orchestration/validate_vms.py
        └─ IaC/netbox/scripts/netbox_vm_sync.py

Script (Automation)
  │
  ├─ name: netbox_vm_sync.py
  └─ manages_vms:
     ├─ pg-01 (syncs to NetBox)
     ├─ web-01 (syncs to NetBox)
     └─ All VMs in config/vms/*.yaml
```

**NetBox Representation:**
- Custom field on VM: `provisioning_scripts` (list)
- Relationship: Script ← provisions → VM
- VM notes: which scripts manage this VM

---

### 2.4 Service ↔ Container Relationships

**Definition:** Which service definitions are implemented by which containers

```
Service (Definition)
  │
  ├─ name: n8n
  ├─ tier: application
  └─ implemented_by_containers:
     ├─ n8n (primary container)
     └─ also_requires: [postgres, traefik]

Container (Running)
  │
  ├─ name: n8n
  ├─ image: n8n-io/n8n:latest
  └─ implements_service: n8n
```

**NetBox Representation:**
- Service object has field: `primary_container`
- Service object has field: `required_containers` (list)
- Relationship: Service ← implemented_by → Container

---

### 2.5 Service ↔ Script Relationships

**Definition:** Which scripts deploy/manage which services

```
Service (Definition)
  │
  ├─ name: postgres
  └─ deployed_by_scripts:
     ├─ IaC/orchestration/compose_stack.py
     ├─ IaC/orchestration/deploy.sh
     └─ IaC/netbox/scripts/netbox_vm_sync.py (discovers)

Script (Automation)
  │
  ├─ name: discover_services.py
  └─ discovers_services:
     ├─ All services (scans services/ directory)
     └─ Builds registry for: compose_stack.py
```

**NetBox Representation:**
- Service object has field: `orchestration_scripts` (list)
- Relationship: Script ← deploys → Service

---

### 2.6 Script ↔ Script Relationships

**Definition:** Dependencies between Python scripts (which scripts call/depend on which)

```
Script A (High-level Orchestrator)
  │
  ├─ name: deploy.sh
  └─ calls:
     ├─ orchestration/validate_services.py (validates first)
     ├─ orchestration/discover_services.py (discovers)
     ├─ orchestration/compose_stack.py (generates)
     ├─ netbox/scripts/netbox_vm_sync.py (syncs)
     └─ Services are deployed

Script B (Helper)
  │
  ├─ name: validate_services.py
  └─ used_by:
     ├─ deploy.sh
     └─ CI/CD pipelines
```

**NetBox Representation:**
- Service notes field: lists script dependencies
- Custom field: `script_dependencies`
- Manual tracking (or third-party SCM integration)

---

## 3. Complete Relationship Matrix

| Entity A | Relationship | Entity B | NetBox Field | Example |
|----------|-------------|----------|--------------|---------|
| VM | hosts | Container | `docker_containers` | web-01 hosts n8n |
| VM | hosts | Service | `services_running` | web-01 hosts n8n service |
| Container | managed_by | Script | `orchestration_scripts` | n8n managed_by compose_stack.py |
| Container | implements | Service | `implements_service` | n8n container implements n8n service |
| Service | deployed_by | Script | `orchestration_scripts` | postgres deployed_by compose_stack.py |
| Service | requires | Container | `required_containers` | n8n requires postgres container |
| Script | calls | Script | `script_dependencies` | deploy.sh calls validate_services.py |
| Script | manages | VM | `provisioning_scripts` | netbox_vm_sync.py manages web-01 |
| All | tracked_by | NetBox | Custom fields | All entities synced to NetBox |

---

## 4. Python Scripts Inventory

### 4.1 Orchestration Scripts

| Script | Path | Purpose | Inputs | Outputs | Manages | Key Relations |
|--------|------|---------|--------|---------|---------|-----------------|
| validate_services.py | IaC/orchestration/ | Validates all service.yaml files against schema | services/ tree | validation report | Services | service definitions |
| discover_services.py | IaC/orchestration/ | Discovers and catalogs all services | services/ tree | registry.json | Services | all service metadata |
| compose_stack.py | IaC/orchestration/ | Generates docker-compose and Ansible vars | registry.json | docker-compose.yml, group_vars | Containers, Services | service→container mapping |
| deploy.sh | IaC/orchestration/ | Orchestrates full deployment (calls other scripts) | all manifests | deployed stack | All: VMs, Containers, Services | orchestration engine |
| validate_vms.py | IaC/orchestration/ | Validates VM YAML against schema | config/vms/*.yaml | validation report | VMs | VM definitions |
| vm_to_terraform.py | IaC/orchestration/ | Generates Terraform from VM YAML | config/vms/*.yaml | *.tf files | VMs | VM→Terraform mapping |

### 4.2 NetBox Integration Scripts

| Script | Path | Purpose | Inputs | Outputs | Manages | Key Relations |
|--------|------|---------|--------|---------|---------|-----------------|
| netbox_vm.py | IaC/netbox/scripts/ | Creates/updates VMs in NetBox | VM YAML | NetBox objects | VMs in NetBox | VM→NetBox sync |
| netbox_vm_sync.py | IaC/netbox/scripts/ | Bi-directional VM↔NetBox sync | config/vms/*.yaml | NetBox+local updates | VMs, Containers, Services | full entity sync |

### 4.3 Integration/Application Scripts

| Script | Path | Purpose | Uses | Manages | Key Relations |
|--------|------|---------|------|---------|-----------------|
| calendar_api_server.py | integration/ | Calendar service server | n8n API, postgres | calendar workflows | Container: n8n |
| health_monitoring_api.py | integration/ | Health monitoring engine | prometheus metrics | health alerts | Containers: prometheus, grafana |
| chronicle_api_server.py | integration/ | Audio/voice API | Docker containers | voice workflows | Multiple containers |
| ... | integration/ | ~50+ more application scripts | Various | Various | Various |

---

## 5. Relationship Registry Format

**File:** `IaC/config/relationships.yaml`

```yaml
# Unified Relationship Registry
# Maps all elementary particles and their relationships

version: "1.0"
generated: "2026-04-28"
scope: ["VMs", "Containers", "Scripts", "Services", "NetBox"]

# ============================================================================
# VMs & Their Contained Entities
# ============================================================================
vms:
  pg-01:
    tier: storage
    type: vm
    identity:
      name: pg-01
      fqdn: pg-01.internal
      ip: 10.10.40.10
    contains:
      containers:
        - name: postgres
          image: postgres:15
          orchestrated_by:
            - script: compose_stack.py
              path: IaC/orchestration/compose_stack.py
              role: generates docker-compose
            - script: deploy.sh
              path: IaC/orchestration/deploy.sh
              role: orchestrates deployment
        - name: postgres_exporter
          image: prometheuscommunity/postgres-exporter:latest
          orchestrated_by:
            - script: compose_stack.py
              role: generates config
      services:
        - postgres  # runs postgres service
    provisioned_by:
      - script: netbox_vm_sync.py
        path: IaC/netbox/scripts/netbox_vm_sync.py
        action: creates/updates in NetBox
      - script: deploy_vm.sh
        path: IaC/orchestration/deploy_vm.sh
        action: orchestrates full deployment
    configured_by:
      - playbook: configure_postgres.yml
        path: IaC/ansible/playbooks/configure_postgres.yml
        role: installs postgres, configures WAL
    netbox:
      synced: true
      custom_fields:
        orchestration_scripts: [deploy_vm.sh, netbox_vm_sync.py]
        containers: [postgres, postgres_exporter]
        services: [postgres]

  web-01:
    tier: application
    type: vm
    identity:
      name: web-01
      fqdn: web-01.internal
      ip: 10.10.30.10
    contains:
      containers:
        - name: n8n
          image: n8n-io/n8n:latest
          service: n8n  # implements service
          orchestrated_by:
            - script: compose_stack.py
              role: generates docker-compose
            - script: configure_docker_web.yml
              role: deploys and configures
          depends_on_containers: [postgres]
          depends_on_scripts:
            - calendar_event_enricher.py  # uses n8n API
            - chronicle_api_server.py     # uses n8n workflows
        - name: docker_exporter
          image: prometheuscommunity/docker-exporter:latest
          orchestrated_by:
            - script: compose_stack.py
              role: generates config
        - name: node_exporter
          image: prometheuscommunity/node-exporter:latest
          orchestrated_by:
            - script: compose_stack.py
              role: generates config
      services:
        - n8n  # primary service
        - docker  # docker host itself
    provisioned_by:
      - script: netbox_vm_sync.py
      - script: deploy_vm.sh
    configured_by:
      - playbook: configure_docker_web.yml
    netbox:
      synced: true
      custom_fields:
        orchestration_scripts: [deploy_vm.sh, netbox_vm_sync.py]
        containers: [n8n, docker_exporter, node_exporter]
        services: [n8n, docker]

# ============================================================================
# Docker Containers & Their Relationships
# ============================================================================
containers:
  n8n:
    image: n8n-io/n8n:latest
    runs_on_vm: web-01
    implements_service: n8n
    depends_on_vms:
      - pg-01  # database backend
    orchestrated_by:
      - script: compose_stack.py
        path: IaC/orchestration/compose_stack.py
        role: generates docker-compose.yml
      - script: configure_docker_web.yml
        path: IaC/ansible/playbooks/configure_docker_web.yml
        role: deploys container
      - script: deploy.sh
        path: IaC/orchestration/deploy.sh
        role: top-level orchestration
    used_by_scripts:
      - calendar_event_enricher.py  # calls n8n API to trigger workflows
      - chronicle_api_server.py     # uses n8n for voice workflows
    netbox:
      synced: true
      custom_fields:
        hosting_vm: web-01
        orchestration_scripts: [compose_stack.py, configure_docker_web.yml]
        used_by: [calendar_event_enricher.py, chronicle_api_server.py]

  postgres:
    image: postgres:15
    runs_on_vm: pg-01
    implements_service: postgres
    used_by_containers:
      - n8n  # connects for workflows
      - other services as needed
    orchestrated_by:
      - script: compose_stack.py
      - script: deploy.sh

  traefik:
    image: traefik:latest
    runs_on_vm: core-01  # (hypothetical)
    implements_service: traefik  # reverse proxy service
    manages_containers:
      - n8n
      - nextcloud
      - grafana
    orchestrated_by:
      - script: compose_stack.py
      - script: deploy.sh

# ============================================================================
# Python Scripts & Their Relationships
# ============================================================================
scripts:
  orchestration:
    deploy.sh:
      path: IaC/orchestration/deploy.sh
      language: bash
      purpose: Top-level orchestration - calls all other scripts
      calls:
        - validate_services.py  # validates definitions
        - discover_services.py  # discovers all services
        - compose_stack.py      # generates docker-compose
        - netbox_vm_sync.py     # syncs to NetBox
      manages_entities:
        - type: services
          count: 50+
        - type: containers
          count: 50+
        - type: vms
          count: 10+

    validate_services.py:
      path: IaC/orchestration/validate_services.py
      language: python3
      purpose: Validate all service.yaml files against schema
      validates: 
        - all services/ tree
        - config/vms/ VMs
      used_by:
        - deploy.sh
        - CI/CD pipelines
      outputs:
        - validation report
        - error/warning list

    discover_services.py:
      path: IaC/orchestration/discover_services.py
      language: python3
      purpose: Discovery and registry building
      scans: services/ directory
      outputs:
        - registry.json
        - service graph
      used_by:
        - compose_stack.py
        - deploy.sh

    compose_stack.py:
      path: IaC/orchestration/compose_stack.py
      language: python3
      purpose: Generate docker-compose and Ansible variables
      depends_on:
        - discover_services.py  # needs registry
      generates:
        - docker-compose.yml
        - ansible group_vars
      manages_containers:
        - All service containers
        - All monitoring containers
      manages_services:
        - All services

    validate_vms.py:
      path: IaC/orchestration/validate_vms.py
      language: python3
      purpose: Validate VM YAML files
      validates:
        - all config/vms/*.yaml
      used_by:
        - deploy_vm.sh
        - CI/CD

    vm_to_terraform.py:
      path: IaC/orchestration/vm_to_terraform.py
      language: python3
      purpose: Generate Terraform from VM YAML
      depends_on:
        - validate_vms.py
      generates:
        - terraform/*.tf files
      manages_vms:
        - pg-01, web-01, all VMs

  netbox_integration:
    netbox_vm_sync.py:
      path: IaC/netbox/scripts/netbox_vm_sync.py
      language: python3
      purpose: Sync VMs between YAML and NetBox DCIM
      syncs:
        - VMs (config/vms/*.yaml ↔ NetBox)
        - Containers (derived from service.yaml)
        - IP addresses
        - Services
        - Relationships
      calls:
        - NetBox API (RESTful)
      called_by:
        - deploy.sh
        - deploy_vm.sh
      manages_netbox_entities:
        - VM objects
        - Interface objects
        - IP address objects
        - Service objects
        - Custom relationships

  integration:
    calendar_event_enricher.py:
      path: integration/calendar_event_enricher.py
      language: python3
      purpose: Enrich calendar events with context
      uses_containers:
        - n8n  # calls n8n API
        - postgres  # stores events
      uses_services:
        - calendar (NextCloud)
        - n8n workflows
      called_by:
        - calendar_api_server.py

    health_monitoring_api.py:
      path: integration/health_monitoring_api.py
      language: python3
      purpose: Health monitoring and alerting
      uses_containers:
        - prometheus  # scrapes metrics
        - grafana      # displays dashboards
      uses_vms:
        - pg-01  # health data
        - monitoring-vm  # prometheus

# ============================================================================
# Services & Their Relationships
# ============================================================================
services:
  n8n:
    tier: application
    type: service
    container_primary: n8n
    containers_required:
      - postgres  # for n8n database
      - traefik   # for routing
    deployed_by_scripts:
      - compose_stack.py
      - configure_docker_web.yml
    runs_on_vms:
      - web-01
    used_by_scripts:
      - calendar_event_enricher.py
      - chronicle_api_server.py
    netbox:
      synced: true
      custom_fields:
        primary_container: n8n
        hosting_vm: web-01
        orchestration_scripts: [compose_stack.py]

  postgres:
    tier: storage
    type: service
    container_primary: postgres
    deployed_by_scripts:
      - compose_stack.py
    runs_on_vms:
      - pg-01
    used_by_services:
      - n8n
      - nextcloud
      - all backend services
    netbox:
      synced: true

  traefik:
    tier: core
    type: service
    container_primary: traefik
    manages_services:
      - n8n
      - nextcloud
      - grafana
      - All exposed services
    deployed_by_scripts:
      - compose_stack.py
    netbox:
      synced: true

# ============================================================================
# NetBox Synchronization Status
# ============================================================================
netbox_sync:
  last_sync: "2026-04-28T15:30:00Z"
  synced_entities:
    vms: 10
    containers: 45
    services: 50
    relationships: 150+
  custom_fields_required:
    on_vm:
      - orchestration_scripts (array)
      - containers (array)
      - services (array)
      - deployment_status
    on_service:
      - primary_container (string)
      - required_containers (array)
      - hosting_vm (string)
      - orchestration_scripts (array)
      - deployment_script (string)
    on_container_service:
      - orchestration_scripts (array)
      - hosting_vm (string)
      - implements_service (string)
