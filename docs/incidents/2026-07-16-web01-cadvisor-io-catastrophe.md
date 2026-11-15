---
type: Event
state: organized
ep_tier: infrastructure
ep_particle: web-01
belongs_to: "[[WEB-01_DEPLOYMENT_GUIDE]]"
related_to:
  - "[[PROXMOX_VM_RECOVERY]]"
  - "[[VM_DEPLOYMENT_GUIDE]]"
incident_date: "2026-07-16"
severity: critical
status: resolved
---

# Incident: web-01 cAdvisor IO Catastrophe

**Date:** 2026-07-16  
**Service:** web-01 (VMID 240) — all Docker services  
**Severity:** Critical  
**Status:** Resolved  

## What Happened

web-01 became completely unresponsive — SSH timed out at banner exchange, Traefik
stopped serving HTTP, and all hosted services (n8n, NetBox, Grafana) went offline.
The VM remained running (ping responded, Proxmox API showed `status: running`) but
no userspace connections were accepted.

Disk reads on the host reached **~450 MB/s sustained** from a single VM, saturating
the storage pool and starving every other process on the VM of IO slots. CPU IO-wait
climbed above 40%, causing all processes to enter D-state (uninterruptible sleep).

## Root Cause

`cAdvisor` (container metrics exporter) was configured with `restart: unless-stopped`
in the monitoring `docker-compose.yml`. cAdvisor works by recursively walking
`/var/lib/docker/overlay2` — every container's full filesystem layer tree — on each
scrape cycle. On a host running 8+ Docker services with layered images, this
generates enormous sequential read IO with no built-in throttle.

At 450 MB/s reads, the LVM thin pool's IO bandwidth was consumed entirely by one
container, starving sshd, Traefik, Docker daemon, and the kernel's own network
processing queues.

Secondary factors:
- **Memory balloon** (`floating: 2048` in Terraform) allowed Proxmox to deflate the
  VM from 8192 MB to its minimum, triggering kernel page allocation failures
  (`Out of puff! can't get N pages`) during a subsequent reboot.
- **Proxmox VM lock** (`/var/lock/qemu-server/lock-240.conf`) was left by a failed
  `qmshutdown` task, blocking further API-driven shutdown/reset attempts until
  manually cleared with `qm unlock 240` from the Proxmox node shell.

## Impact

- **Duration:** ~3 hours of complete web-01 unavailability
- **Data loss:** None — all persistent data lives in pg-01 (separate VM). Prometheus
  had a metrics gap; Grafana SQLite was intact after fsck.
- **Services affected:** Traefik (all reverse-proxied services), n8n, NetBox,
  Grafana, Prometheus, Authelia

## Resolution

1. Applied Proxmox disk read throttle via API (`mbps_rd=25` on scsi0) — reduced
   reads from 450 MB/s to 25 MB/s, CPU dropped from 40% to 4%.
2. Disabled memory balloon (`balloon: 0`) via API to give VM its full 8192 MB.
3. User cleared Proxmox VM lock via node shell: `qm unlock 240`.
4. Performed hardware reset via Proxmox UI (ACPI shutdown was unresponsive due to
   lock and prior IO starvation).
5. Mounted the stopped VM's disk on the Proxmox host using `kpartx` to edit
   `/opt/monitoring/docker-compose.yml` directly — removing the cadvisor service
   without needing SSH or a running VM.
6. Started VM cleanly; services recovered.

## IaC Changes

| File | Change |
|---|---|
| `Roles/monitoring/templates/docker-compose.yml.j2` | Removed entire `cadvisor` service block |
| `Roles/monitoring/templates/prometheus.yml.j2` | Removed `cadvisor` scrape job |
| `Roles/monitoring/tasks/main.yml` | Removed cadvisor from image pull loop |
| `Terraform/envs/web-01/main.tf` | Added `speed { read = var.web01_disk_read_mbps }` to disk block |
| `Terraform/envs/web-01/variables.tf` | Added `web01_disk_read_mbps = 50` |

## Prevention

- **cAdvisor removed permanently** from the monitoring stack. Node exporter covers
  host-level metrics; per-container metrics can be obtained from Docker stats API
  without filesystem walks.
- **Disk read cap (50 MB/s)** added to web-01 Terraform config. No legitimate
  web/proxy/app workload reads disk at 450 MB/s; the cap catches runaway processes
  before they saturate the storage pool.
- **Memory balloon disabled** for web-01 — app VMs with Docker should have dedicated
  memory, not a floating floor.

## Lessons Learned

- cAdvisor is dangerous on hosts with many Docker containers and layered images.
  Node exporter is sufficient for infrastructure-level observability.
- Always set `mbps_rd` on web/app VM disks. Databases need unrestricted IO;
  reverse proxies and app containers do not.
- A VM that responds to ICMP but not TCP on port 22 = kernel alive, userspace in
  D-state from IO starvation. Do not attempt repeated reboots — throttle the IO
  first, then reboot once.
- Proxmox VM lock files (`/var/lock/qemu-server/lock-NNN.conf`) are OS-level, not
  API-clearable. Always use `qm unlock <vmid>` from the node shell.
- When SSH is unavailable on a stopped VM, `kpartx` + mount is faster and safer
  than repeated boot attempts. See `[[PROXMOX_VM_RECOVERY]]`.
