---
type: Operation
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[ANSIBLE_INTEGRATION]]"
  - "[[TERRAFORM_INTEGRATION]]"
  - "[[VM_DEPLOYMENT_GUIDE]]"
  - "[[DEPLOYMENT_CHECKLIST]]"
  - "[[VM_SERVICE_INTEGRATION_GUIDE]]"
---

# Elementary Particles: VM Deployment Guide

Complete guide for deploying GPU, NAS, DNS, and Elasticsearch VMs using the n8n orchestration system.

## Overview

This document covers the complete deployment process for the four new VMs in the Elementary Particles infrastructure:

1. **GPU-01** - GPU-accelerated ML services (Ollama, Immich-ML, YOLO) on zfs-nvme
2. **NAS-01** - OpenMediaVault network storage on SSDs
3. **DNS-01** - Pi-hole DNS server on SSDs
4. **ES-01** - Elasticsearch search engine on zfs-nvme

All deployments use the same orchestration pattern as PostgreSQL with:
- Service definition files (service.yaml)
- Ansible playbooks for configuration
- Python orchestrator for execution
- PostgreSQL tracking
- NetBox integration
- n8n workflow automation

## Architecture

```
n8n Workflow (HTTP Trigger)
    ↓
Load Service Config (service.yaml)
    ↓
Validate Prerequisites
    ↓
Dry-run (Plan) or Apply (Deploy)?
    ↓
[DRY-RUN]              [APPLY]
Display Plan    →     Approval Gate
                      ↓
                   Execute Ansible
                      ↓
                   Verify Services
                      ↓
                   Register in NetBox
                      ↓
                   Update PostgreSQL
                      ↓
                   Notify Slack
```

## VM Specifications

### GPU-01: ML Services
```yaml
Name: gpu-01
Datastore: zfs-nvme
VLAN: vlan30
CPU: 16
Memory: 64Gi
Storage: 500Gi

Services:
  - Ollama (LLM inference) - Port 11434
  - Immich-ML (ML pipeline) - Port 3003
  - YOLO (Object detection) - Port 8000
```

### NAS-01: OpenMediaVault Storage
```yaml
Name: nas-01
Datastore: ssd
VLAN: vlan40
CPU: 8
Memory: 32Gi
Storage: 10Ti (plus RAID6 configuration)

Services:
  - OpenMediaVault (NAS OS)
  - SMB/CIFS (Port 445)
  - NFS (Port 2049)
```

### DNS-01: Pi-hole DNS
```yaml
Name: dns-01
Datastore: ssd
VLAN: vlan30
CPU: 2
Memory: 2Gi
Storage: 10Gi

Services:
  - Pi-hole (DNS + Ad-blocker)
  - DNS Service (Port 53/UDP, 53/TCP)
  - Web UI (Port 80, 443)
```

### ES-01: Elasticsearch
```yaml
Name: es-01
Datastore: zfs-nvme
VLAN: vlan30
CPU: 8
Memory: 16Gi
Storage: 200Gi

Services:
  - Elasticsearch (Search engine) - Port 9200
  - Kibana (Visualization) - Port 5601
```

## Service Definition Files

Each VM has a `service.yaml` file defining its characteristics:

- **Location:** `IaC/services/{tier}/{service}/service.yaml`
- **Contains:** Name, version, deployment resources, interfaces, dependencies, monitoring rules
- **Used by:** Terraform (infrastructure), Ansible (configuration), n8n (orchestration)

### File Structure

```yaml
---
name: service-name
version: "X.X"
tier: "tier-name"
description: "Service description"

tags:
  network: "vlanXX"
  expose: "true/false"
  tier: "storage/compute/search"
  sla: "critical/high/medium"

deployment:
  platform: "docker/bare-metal"
  deployment_target: "host-group"
  resources:
    cpu: "4"
    memory: "8Gi"
    storage: "100Gi"
  datastore: "zfs-nvme/ssd/default"

services:
  - name: "service-name"
    image: "docker-image:tag"
    port: PORT

interfaces:
  - name: "interface-name"
    protocol: "tcp/udp"
    port: PORT

dependencies: []

provides:
  - interface: "protocol:port"
    for_services: ["service-list"]
    contract: "API version/spec"

monitoring:
  health_check: "command"
  metrics_endpoint: "url"
  alert_rules: []

backup:
  enabled: true
  schedule: "cron"
  retention: "days"

networking:
  vlan: "vlanXX"
  dns_enabled: true
  dns_names: []
```

## Ansible Playbooks

### Playbook Structure

Each service has a dedicated playbook in `IaC/Ansible/playbooks/`:

```
deploy_gpu.yml
deploy_nas.yml
deploy_dns.yml
deploy_elasticsearch.yml
deploy_elementary_particles.yml (master orchestrator)
```

### Ansible Roles

Supporting roles in `IaC/Ansible/Roles/`:

```
gpu/
  tasks/
    - drivers.yml (NVIDIA drivers and CUDA)
    - ollama.yml (Ollama LLM service)
    - immich_ml.yml (ML pipeline)
    - yolo.yml (Object detection)
    
openmediavault/
  tasks/
    - install.yml (OMV installation)
    - storage.yml (RAID and filesystem)
    - shares.yml (SMB/NFS shares)
    
pihole_dns/
  tasks/
    - install.yml (Pi-hole container)
    - configure.yml (Adlists, DNS records)
    - monitoring.yml (Health checks)
    
elasticsearch/
  tasks/
    - install.yml (Elasticsearch + Kibana)
    - kibana.yml (Kibana UI)
    - configure.yml (Index templates, mappings)
    - verify.yml (Cluster health checks)
```

## Deployment Process

### Step 1: Trigger Deployment

#### Via n8n Dashboard
1. Navigate to n8n workflow: "Elementary Particles - VM Deployment"
2. Click "Execute Workflow"
3. Select target VM (gpu-01, nas-01, dns-01, es-01)
4. Choose mode (Dry-run or Apply)
5. Toggle approval if needed

#### Via HTTP (cURL)
```bash
curl -X POST http://n8n.internal:5678/webhook/elementary-particles-deploy \
  -H "Content-Type: application/json" \
  -d '{
    "target_vm": "gpu-01",
    "deployment_mode": "dry-run",
    "approval_required": true
  }'
```

#### Via n8n API
```bash
curl -X POST http://n8n.internal:5678/rest/workflows/elementary-particles-vm-deployment/execute \
  -H "Authorization: Bearer $N8N_API_TOKEN" \
  -d '{
    "target_vm": "es-01",
    "deployment_mode": "apply"
  }'
```

### Step 2: Dry-Run (Validation)

The workflow will:

1. **Load Service Config** - Read service.yaml for the target VM
2. **Validate Prerequisites** - Check:
   - Proxmox connectivity
   - Target node availability
   - Network/VLAN configuration
   - Storage space on datastore
   - DNS resolution capability
3. **Generate Plan** - Create Terraform and Ansible execution plan
4. **Display Plan** - Send to Slack for review
5. **No Changes Applied** - Safe to review without risk

Example dry-run output:

```
═════════════════════════════════════════════════════════════════════
DEPLOYMENT PLAN: gpu-01
═════════════════════════════════════════════════════════════════════
Description: GPU-accelerated ML services
Datastore: zfs-nvme
VLAN: vlan30
Resources: CPU=16, Memory=64Gi, Storage=500Gi
Mode: dry-run

DEPLOYMENT STEPS:
1. Validate target Proxmox node (pve-01)
2. Generate Terraform configuration for GPU-01
3. Create VM via Terraform (vlan30 bridge)
4. Configure networking
5. Run ansible playbook: deploy_gpu.yml
   - Install NVIDIA drivers
   - Deploy Ollama container
   - Deploy Immich-ML container
   - Deploy YOLO container
6. Verify all services running
7. Register in NetBox
8. Update PostgreSQL elementary_particles schema
═════════════════════════════════════════════════════════════════════
```

### Step 3: Review & Approve

1. **Slack Notification** - Workflow sends plan to #infrastructure channel
2. **Review Plan** - Check resources, services, configuration
3. **Approve/Reject** - Click approval button in Slack
4. **Timeout** - Default 1 hour timeout for approval

### Step 4: Deploy (Apply)

After approval, the workflow will:

1. **Execute Terraform** - Create VM on Proxmox
   - Allocate resources (CPU, memory, disk)
   - Configure networking
   - Set tags for inventory
2. **Wait for SSH** - Poll for connectivity (30 second wait)
3. **Run Ansible** - Configure services
   - Install base packages
   - Deploy Docker/containers
   - Configure health checks
4. **Verify Services** - Test connectivity and health:
   ```bash
   curl -f http://gpu-01:11434/api/tags  # Ollama
   curl -f http://nas-01/api/v1/system/info  # OMV
   dig @dns-01 google.com  # Pi-hole
   curl -u elastic:$PASSWORD https://es-01:9200/_cluster/health  # ES
   ```
5. **Register in NetBox** - Add VM to infrastructure database
6. **Update PostgreSQL** - Track in elementary_particles schema
7. **Slack Notification** - Send deployment success/failure

### Step 5: Post-Deployment

#### Access the Services

**GPU-01:**
```bash
# SSH access
ssh root@gpu-01.internal

# API endpoints
curl http://gpu-01:11434/api/tags          # Ollama
curl http://gpu-01:3003                    # Immich-ML
curl http://gpu-01:8000                    # YOLO

# Docker compose logs
docker logs ollama
docker logs immich-ml
docker logs yolo
```

**NAS-01:**
```bash
# SSH access
ssh root@nas-01.internal

# Web UI
https://nas-01/admin

# SMB mount (from Linux)
mount -t cifs //nas-01/share /mnt/nas -o username=user,password=pass

# NFS mount
mount -t nfs nas-01:/mnt/nas-storage /mnt/nas

# Check RAID status
ssh root@nas-01 mdadm --detail /dev/md0
```

**DNS-01:**
```bash
# SSH access
ssh root@dns-01.internal

# Web UI
http://dns-01/admin

# Test DNS
dig @dns-01 google.com
nslookup example.com dns-01

# Check gravity database
ssh root@dns-01 docker exec pihole sqlite3 /etc/pihole/pihole-FTL.db "SELECT count(*) FROM gravity_adlist;"
```

**ES-01:**
```bash
# SSH access
ssh root@es-01.internal

# Elasticsearch REST API
curl -u elastic:$PASSWORD https://es-01:9200/_cluster/health

# Kibana UI
http://es-01:5601

# Create index
curl -u elastic:$PASSWORD -X PUT https://es-01:9200/test-index

# Check indices
curl -u elastic:$PASSWORD https://es-01:9200/_cat/indices?v
```

#### Monitor in NetBox

1. Navigate to NetBox: https://netbox.internal
2. Go to DCIM → Devices
3. Click on your VM (gpu-01, nas-01, dns-01, es-01)
4. Check custom fields:
   - Datastore
   - VLAN
   - CPU cores
   - Memory allocation
   - Service status

#### Check PostgreSQL

```bash
psql -h postgres.internal -U postgres -d elementary_particles

# View all VMs
SELECT * FROM vms WHERE hostname LIKE '%gpu%' OR hostname LIKE '%nas%' OR hostname LIKE '%dns%' OR hostname LIKE '%es%';

# Check deployment history
SELECT * FROM elementary_particles.deployment_history ORDER BY deployed_at DESC LIMIT 10;

# View service dependencies
SELECT * FROM elementary_particles.service_dependencies WHERE service_name = 'gpu';

# Monitor health
SELECT hostname, status, last_health_check, services_healthy FROM elementary_particles.vm_health_status;
```

## Troubleshooting

### Common Issues

#### 1. Prerequisites Check Failed

**Problem:** Validation fails before deployment

**Solutions:**
- Check Proxmox node is online: `ping proxmox-node`
- Verify network connectivity: `ping 10.0.30.1` (VLAN30 gateway)
- Check datastore space: `df -h` on Proxmox
- Verify storage connectivity: `lsblk` on Proxmox

#### 2. Terraform Apply Failed

**Problem:** VM creation fails during infrastructure step

**Solutions:**
```bash
# Check Terraform logs
tail -f /var/log/deployment-*.log

# Verify Proxmox connection
qm list

# Check for IP conflicts
ping gpu-01.internal

# Review Terraform state
terraform state list
```

#### 3. Ansible Playbook Failed

**Problem:** Configuration management fails after VM creation

**Solutions:**
```bash
# SSH to the VM and check logs
ssh root@gpu-01
journalctl -xe

# Check Docker status
docker ps -a

# View playbook logs
cat /var/log/gpu-deployment.log

# Re-run playbook manually
cd /home/n8n/wger/IaC
ansible-playbook playbooks/deploy_gpu.yml -i inventory/production -vvv
```

#### 4. Service Health Check Failed

**Problem:** Services don't respond to health checks

**Solutions:**
```bash
# Check container logs
docker logs ollama
docker logs immich-ml
docker logs yolo

# Test port connectivity
nc -zv gpu-01 11434
nc -zv gpu-01 3003
nc -zv gpu-01 8000

# Check firewall
ssh root@gpu-01 ufw status

# Manual health check
curl -v http://gpu-01:11434/api/tags
```

#### 5. NetBox Registration Failed

**Problem:** VM doesn't appear in NetBox after deployment

**Solutions:**
```bash
# Check NetBox API connectivity
curl -H "Authorization: Token $NETBOX_TOKEN" https://netbox.internal/api/dcim/devices/

# Check PostgreSQL insert
psql -U postgres -d elementary_particles -c \
  "SELECT * FROM vms WHERE hostname = 'gpu-01';"

# Retry registration
python3 /home/n8n/wger/IaC/orchestration/netbox_deployment_sync.py --sync
```

## Monitoring & Observability

### Service Health

Each VM includes health checks accessible via:

```bash
# Ollama health
curl http://gpu-01:11434/api/tags
GET /api/tags returns available models

# Immich-ML health
curl http://gpu-01:3003
GET / returns service status

# OpenMediaVault health
curl http://nas-01/api/v1/system/info

# Pi-hole health
dig @dns-01 google.com
Returns DNS query results

# Elasticsearch health
curl -u elastic:$PASSWORD https://es-01:9200/_cluster/health
Returns cluster status (green/yellow/red)
```

### Metrics & Alerts

Metrics are collected via:
- Node Exporter (system metrics)
- cAdvisor (container metrics)
- Application-specific exporters
- Prometheus scraping endpoints

Alerts are triggered when:
- Service becomes unavailable
- Resource usage exceeds thresholds
- Replication lag detected (ES)
- DNS queries failing (Pi-hole)
- Storage usage critical (NAS)
- GPU memory critical (GPU-01)

View alerts in:
- AlertManager: http://alertmanager.internal
- Grafana dashboards: http://grafana.internal
- Slack notifications: #infrastructure-alerts

### Logs

Access logs from:

```bash
# Deployment logs
tail -f /var/log/deployment-gpu-01-*.log

# Service logs (via Docker)
docker logs -f ollama
docker logs -f pihole

# Ansible logs
tail -f /home/n8n/wger/logs/deployment_*.log

# System logs
ssh root@gpu-01 journalctl -xe
ssh root@nas-01 journalctl -u openmediavault
ssh root@dns-01 docker exec pihole tail -f /var/log/dnsmasq.log
ssh root@es-01 docker logs elasticsearch
```

## Advanced Usage

### Custom Variables

Override defaults by passing variables to n8n:

```bash
curl -X POST http://n8n.internal:5678/webhook/elementary-particles-deploy \
  -d '{
    "target_vm": "gpu-01",
    "deployment_mode": "apply",
    "custom_vars": {
      "gpu_memory_limit": "32g",
      "docker_network": "custom-net",
      "ollama_models": "mistral,neural-chat"
    }
  }'
```

### Rollback Procedure

To rollback a deployment:

```bash
# 1. Mark as failed in PostgreSQL
psql -U postgres -d elementary_particles -c \
  "UPDATE vms SET status = 'failed' WHERE hostname = 'gpu-01';"

# 2. Destroy VM in Proxmox
qm destroy 123  # VM ID

# 3. Clean up storage
rm -rf /mnt/zfs-nvme/gpu-01

# 4. Trigger redeploy
curl -X POST http://n8n.internal:5678/webhook/elementary-particles-deploy \
  -d '{"target_vm": "gpu-01", "deployment_mode": "apply"}'
```

### Multi-VM Deployment

Deploy all VMs sequentially:

```bash
#!/bin/bash
for vm in gpu-01 nas-01 dns-01 es-01; do
  echo "Deploying $vm..."
  curl -X POST http://n8n.internal:5678/webhook/elementary-particles-deploy \
    -d "{\"target_vm\": \"$vm\", \"deployment_mode\": \"apply\"}"
  
  # Wait for completion
  sleep 300
done
```

## Files Reference

### Service Definitions
- `IaC/services/compute/gpu/service.yaml`
- `IaC/services/storage/nas/service.yaml`
- `IaC/services/network/dns/service.yaml`
- `IaC/services/search/elasticsearch/service.yaml`

### Ansible Playbooks
- `IaC/Ansible/playbooks/deploy_gpu.yml`
- `IaC/Ansible/playbooks/deploy_nas.yml`
- `IaC/Ansible/playbooks/deploy_dns.yml`
- `IaC/Ansible/playbooks/deploy_elasticsearch.yml`
- `IaC/Ansible/playbooks/deploy_elementary_particles.yml` (orchestrator)

### Ansible Roles
- `IaC/Ansible/Roles/gpu/`
- `IaC/Ansible/Roles/openmediavault/`
- `IaC/Ansible/Roles/pihole_dns/`
- `IaC/Ansible/Roles/elasticsearch/`

### Orchestration
- `IaC/orchestration/elementary_particles_vm_deployment.yaml` (n8n workflow)
- `IaC/orchestration/deploy_orchestrator.py` (execution engine)
- `IaC/orchestration/sync_wrapper.js` (HTTP wrapper)

### Documentation
- `IaC/docs/DEPLOYMENT_GUIDE.md` (this file)
- `IaC/IMPLEMENTATION_GUIDE.md` (overall system)
- `IaC/SERVICE_ARCHITECTURE.md` (architecture overview)

## Support & Escalation

### Getting Help

1. **Check logs:** Review deployment logs in `/var/log/` or n8n execution logs
2. **Test manually:** Use diagnostic commands above to identify issues
3. **Review prerequisites:** Run validation to ensure environment ready
4. **Escalate:** Contact infrastructure team with:
   - Target VM name
   - Error message/logs
   - Deployment mode used
   - Time of failure

### Contact

- **Slack:** #infrastructure
- **Email:** infrastructure@internal
- **On-call:** Check PagerDuty rotation

## Appendix

### PostgreSQL Schema

```sql
-- VMs tracking
CREATE TABLE elementary_particles.vms (
  id SERIAL PRIMARY KEY,
  hostname VARCHAR(255) UNIQUE NOT NULL,
  vm_name VARCHAR(255),
  datastore VARCHAR(255),
  vlan VARCHAR(50),
  cpu INTEGER,
  memory VARCHAR(50),
  storage VARCHAR(50),
  status VARCHAR(50) DEFAULT 'pending',
  deployed_at TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ansible_group VARCHAR(255)
);

-- Deployment history
CREATE TABLE elementary_particles.deployment_history (
  id SERIAL PRIMARY KEY,
  vm_id INTEGER REFERENCES vms(id),
  deployment_mode VARCHAR(50),
  status VARCHAR(50),
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  log_file VARCHAR(500),
  error_message TEXT
);

-- Service dependencies
CREATE TABLE elementary_particles.service_dependencies (
  id SERIAL PRIMARY KEY,
  service_name VARCHAR(255),
  depends_on VARCHAR(255),
  interface VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Health status
CREATE TABLE elementary_particles.vm_health_status (
  id SERIAL PRIMARY KEY,
  vm_id INTEGER REFERENCES vms(id),
  services_healthy BOOLEAN,
  last_health_check TIMESTAMP,
  check_result JSON,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Terraform Variables

```hcl
variable "vm_definitions" {
  type = map(object({
    cores      = number
    memory     = number
    disk_size  = number
    datastore  = string
    vlan       = string
    role       = string
    environment = string
    critical   = bool
    backup     = bool
  }))
  
  default = {
    gpu-01 = {
      cores      = 16
      memory     = 65536  # 64GB in MB
      disk_size  = 500
      datastore  = "zfs-nvme"
      vlan       = "vlan30"
      role       = "ml-compute"
      environment = "production"
      critical   = true
      backup     = true
    }
    # ... other VMs ...
  }
}
```

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-29 | Initial release with 4 VM types |

