---
type: Operation
state: organized
ep_tier: platform
ep_particle: netbox
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[NETBOX_CUSTOM_FIELDS_SETUP]]"
  - "[[ELEMENTARY_PARTICLES_ERM]]"
  - "[[UNIFIED_RELATIONSHIP_MODEL]]"
---

# NetBox Status Automation

Event-driven orchestration that propagates NetBox VM status changes across Proxmox, Docker, monitoring, and backup systems.  Part of the **Elementary Particles** IaC platform.

---

## Overview

```
NetBox status change (e.g. active → offline)
    │
    ├─ [A] NetBox built-in Webhook  ─────────────────────┐
    │                                                     │
    └─ [B] netbox_change_listener.py                      │
           ├─ pg_notify (PostgreSQL trigger)              │
           └─ API poll (/api/extras/object-changes/)      │
                                                          ▼
                                              n8n: netbox-status-change-orchestration
                                                          │
                                              sync_wrapper.js /netbox/status-change
                                                          │
                                              netbox_status_handler.py
                                                ├─ Docker: compose down / up
                                                ├─ Proxmox API: shutdown / start
                                                ├─ NetBox: update custom_fields + tags
                                                ├─ Grafana: create / remove silence
                                                └─ PostgreSQL: audit log
```

---

## Status Transitions and Actions

| Old Status       | New Status        | Pipeline    | Actions                                                                               |
|------------------|-------------------|-------------|--------------------------------------------------------------------------------------|
| `active`         | `offline`         | shutdown    | Stop Docker → stop systemd → Proxmox shutdown → NetBox tags → Grafana silence        |
| `active`         | `failed`          | shutdown    | (same as above)                                                                       |
| `active`         | `decommissioning` | decommission| (same as shutdown + decommission tag added)                                          |
| `offline`/`failed` | `active`        | startup     | Proxmox start → wait SSH → Docker up → restore NetBox tags → remove Grafana silence  |
| `*`              | `planned`/`staged`| delegate    | Skipped — handled by `deploy_orchestrator.py`                                        |
| same → same      | —                 | noop        | No action                                                                             |

---

## Files Created

| File | Purpose |
|------|---------|
| `IaC/orchestration/netbox_status_handler.py` | Main action orchestrator |
| `IaC/orchestration/netbox_change_listener.py` | Event source: pg_notify or API poll |
| `IaC/orchestration/netbox_status_workflow.yaml` | n8n workflow definition (YAML) |
| `integration/n8n_workflows/netbox_status_change_workflow.json` | n8n workflow (importable JSON) |
| `integration/n8n_workflows/sync_wrapper.js` | Updated with `/netbox/*` endpoints |

---

## Quick Setup

### Step 1 — Environment variables

Add to your `.env` (or Vault):

```bash
# Proxmox
PROXMOX_HOST=192.168.10.2          # Proxmox node IP
PROXMOX_USER=root@pam
PROXMOX_TOKEN_NAME=n8n             # API token name
PROXMOX_TOKEN_VALUE=<secret>       # API token value

# NetBox
NETBOX_URL=https://netbox.internal
NETBOX_TOKEN=<netbox-api-token>

# Grafana
GRAFANA_URL=https://grafana.internal
GRAFANA_API_KEY=<grafana-api-key>

# SSH
SSH_USER=root
SSH_KEY_FILE=/home/n8n/.ssh/id_rsa
SSH_WAIT_TIMEOUT=120               # seconds to wait for SSH after VM start

# Elementary Particles PostgreSQL
DATABASE_URL=postgresql://user:pass@pg-01:5432/relationships
```

### Step 2 — Apply EP schema tables

```bash
# On the EP PostgreSQL instance (not NetBox DB):
python3 IaC/orchestration/netbox_change_listener.py --apply-schema
```

This creates:
- `elementary_particles.status_change_log` — audit trail
- `elementary_particles.netbox_listener_cursor` — poll cursor

### Step 3 — Choose a trigger method (pick one or both)

#### Option A: NetBox built-in Webhooks (simplest, recommended)

1. In NetBox: **Admin → Extras → Webhooks → Add**
2. Configure:
   ```
   Name:         EP Status Change
   Object types: virtualization | virtual machine
   Events:       ☑ Updates
   URL:          http://n8n:5678/webhook/netbox-status-change
   HTTP method:  POST
   Content type: application/json
   Body template (paste exactly):
   ```
   ```jinja2
   {
     "vm":         "{{ object.name }}",
     "old_status": "{{ snapshots.prechange.status }}",
     "new_status": "{{ object.status.value }}",
     "netbox_id":  {{ object.id }},
     "changed_by": "{{ username }}",
     "changed_at": "{{ timestamp }}"
   }
   ```
3. Save and test with **Test** button.

#### Option B: PostgreSQL pg_notify (near real-time, requires NetBox DB access)

Install the trigger **once** on the NetBox database:

```bash
export NETBOX_DB_URL="postgresql://netbox:pass@netbox-db:5432/netbox"
python3 IaC/orchestration/netbox_change_listener.py --install-trigger
```

Then run the listener as a service:

```bash
# Run once to test:
NETBOX_DB_URL="..." N8N_WEBHOOK_URL="http://n8n:5678/webhook/netbox-status-change" \
  python3 IaC/orchestration/netbox_change_listener.py --mode listen

# As a systemd service (copy the unit file):
cp IaC/netbox-change-listener.service /etc/systemd/system/
systemctl enable --now netbox-change-listener
```

#### Option C: API Polling (no DB access required)

```bash
NETBOX_URL="https://netbox.internal" \
NETBOX_TOKEN="..." \
N8N_WEBHOOK_URL="http://n8n:5678/webhook/netbox-status-change" \
  python3 IaC/orchestration/netbox_change_listener.py --mode poll --interval 30
```

### Step 4 — Import n8n workflow

```bash
# Via n8n CLI:
n8n import:workflow --input=integration/n8n_workflows/netbox_status_change_workflow.json

# Or: n8n UI → Workflows → Import from file
```

Then in n8n:
- Set the **Slack** credential → `Slack (EP Notifications)`
- Set the **PostgreSQL** credential → `PostgreSQL (Elementary Particles)` (DATABASE_URL)
- Set env vars `WRAPPER_URL`, `SLACK_CHANNEL` in n8n Settings → Environment

### Step 5 — Test

```bash
# Dry-run test (no real changes):
curl -X POST http://localhost:3000/netbox/status-change \
  -H "Content-Type: application/json" \
  -d '{"vm":"web-01","old_status":"active","new_status":"offline","dry_run":true}'

# Or via Python directly:
python3 IaC/orchestration/netbox_status_handler.py \
  --vm web-01 --old-status active --new-status offline --dry-run
```

---

## NetBox Custom Fields Required

Add these custom fields in **NetBox → Extras → Custom Fields**:

### On `virtualization.virtualmachine`

| Field name             | Type      | Description                                              |
|------------------------|-----------|----------------------------------------------------------|
| `monitoring_paused`    | Boolean   | True when alerts are silenced by status automation       |
| `health_status`        | Text      | Last known health (`active`, `offline`, `failed`, …)     |
| `previous_backup_tag`  | Text      | Backup schedule before VM was taken offline              |

### NetBox Tags used by automation

| Tag slug           | Applied when                          |
|--------------------|---------------------------------------|
| `monitoring-paused`| VM enters shutdown pipeline           |
| `monitoring-active`| VM enters startup pipeline            |
| `powered-off`      | VM is stopped in Proxmox              |
| `powered-on`       | VM is running in Proxmox              |

Create tags in **NetBox → Extras → Tags**.

---

## Shutdown Pipeline — Step Detail

1. **docker_stop** — SSH to VM, run `docker compose down --timeout 30`
2. **systemd_stop** — SSH to VM, stop any `systemd_services` listed in `config/vms/{vm}.yaml`
3. **proxmox_power** — Proxmox API ACPI shutdown → force-off after timeout
4. **netbox_tags** — Set `monitoring_paused=true`, `backup=on-demand`, `health_status=<new>`, save `previous_backup_tag`
5. **grafana_silence** — Create Alertmanager silence matching `instance=<vm>`

## Startup Pipeline — Step Detail

1. **proxmox_power** — Proxmox API start VM
2. **wait_ssh** — Poll TCP 22 until open (configurable timeout)
3. **docker_start** — SSH to VM, run `docker compose up -d`
4. **netbox_tags** — Restore `monitoring_paused=false`, `health_status=active`
5. **grafana_silence** — Delete all active silences for VM
6. **verify_services** — Confirm SSH still open + all Docker containers in `Up` state

---

## How VM Config is Loaded

`netbox_status_handler.py` reads VM details from the Elementary Particles manifest:

```
IaC/config/vms/{vm_name}.yaml          → IP, Proxmox node, backup tag, docker path
IaC/config/relationships.yaml          → containers list, lifecycle metadata
```

If the manifest is missing, SSH steps are skipped but Proxmox and NetBox steps still run.

### Adding `systemd_services` to a VM manifest

If a VM runs non-Docker services that need stopping, add to its YAML:

```yaml
# IaC/config/vms/my-vm.yaml
systemd_services:
  - my-custom-service
  - another-service
```

### Customizing the Docker compose directory

```yaml
docker:
  compose_dir: /opt/myapp       # default: /opt/docker
```

---

## Listener as a systemd Service

Create `/etc/systemd/system/netbox-change-listener.service`:

```ini
[Unit]
Description=NetBox Change Listener (Elementary Particles)
After=network.target

[Service]
User=n8n
WorkingDirectory=/home/n8n/wger
EnvironmentFile=/home/n8n/wger/.env
ExecStart=/usr/bin/python3 IaC/orchestration/netbox_change_listener.py --mode listen
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now netbox-change-listener
journalctl -u netbox-change-listener -f
```

---

## Docker Compose (add listener as a sidecar)

Add to `integration/n8n_workflows/docker-compose.yml`:

```yaml
services:
  netbox-listener:
    build:
      context: .
      dockerfile: Dockerfile
    command: python3 IaC/orchestration/netbox_change_listener.py --mode poll --interval 30
    environment:
      - NETBOX_URL=${NETBOX_URL}
      - NETBOX_TOKEN=${NETBOX_TOKEN}
      - N8N_WEBHOOK_URL=http://n8n:5678/webhook/netbox-status-change
      - DATABASE_URL=${DATABASE_URL}
      - WGER_HOME=/home/n8n/wger
    volumes:
      - ${WGER_HOME}:/home/n8n/wger
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3001/"]
      interval: 30s
      retries: 3
    depends_on:
      - n8n
```

---

## Monitoring

### Query audit log

```sql
-- Recent status changes
SELECT vm_name, old_status, new_status, pipeline, overall_result,
       started_at, finished_at,
       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_s
FROM   elementary_particles.status_change_log
ORDER  BY started_at DESC
LIMIT  20;

-- Failed pipelines
SELECT * FROM elementary_particles.status_change_log
WHERE  overall_result = 'failed'
ORDER  BY started_at DESC;

-- VMs currently offline
SELECT vm_name, new_status, started_at
FROM   elementary_particles.status_change_log
WHERE  new_status IN ('offline','failed','decommissioning')
  AND  NOT EXISTS (
    SELECT 1 FROM elementary_particles.status_change_log l2
    WHERE  l2.vm_name = status_change_log.vm_name
      AND  l2.new_status = 'active'
      AND  l2.started_at > status_change_log.started_at
  );
```

### Check listener health via wrapper

```bash
curl http://localhost:3000/netbox/listener/health
curl http://localhost:3000/netbox/status-log?limit=10
curl http://localhost:3000/netbox/status-log?vm=web-01
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Nothing triggers | NetBox webhook URL reachable from NetBox container? Test with `curl`. |
| SSH steps fail | `SSH_KEY_FILE` set? Key authorized on target VM? Run `--dry-run` first. |
| Proxmox steps skip | `PROXMOX_HOST` and `PROXMOX_TOKEN_VALUE` set? Token has VM.PowerMgmt privilege? |
| Grafana silence not created | `GRAFANA_URL` set? API key has `alerting:write` scope? |
| pg_notify not firing | Trigger installed? (`--install-trigger`). NetBox status column name is `status` in `virtualization_virtualmachine`. |
| Duplicate events | Poll listener + webhook both active; consider disabling one. |
| `partial` result | Non-critical step failed (e.g. Grafana unreachable). Review `steps_json` in audit log. |

---

## Elementary Particles Alignment

This integration follows all EP conventions:

- **Config from** `config/vms/*.yaml` and `config/relationships.yaml`
- **Logged to** `elementary_particles.status_change_log` (PostgreSQL)
- **Invoked via** `sync_wrapper.js` HTTP endpoints (`/netbox/status-change`)
- **n8n workflow** uses webhook trigger + manual trigger + Slack + PostgreSQL audit
- **Structured JSON** output consumable by n8n downstream nodes
- **dry-run** mode honors `--dry-run` flag at every step
- **Graceful degradation**: missing credentials skip that step rather than aborting

Related EP components:
- `deploy_orchestrator.py` — used when status transitions to `planned`/`staged` (first boot)
- `netbox_deployment_sync.py` — handles the reverse: deployment completion → update NetBox
- `sync_relationships.py` — daily relationship discovery (separate from runtime events)
