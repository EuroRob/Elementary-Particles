---
type: Event
state: organized
ep_tier: platform
ep_particle: pve-exporter
belongs_to: "[[project_pve_monitoring]]"
related_to:
  - "IaC/Grafana/disk_space_hosts_vms.json"
incident_date: "2026-09-08"
severity: medium
status: resolved for pve-02; pve-03 blocked on a missing WoL MAC
---

# Incident: pve-02/pve-03 Were Never Actually Monitored — Four Stacked Gaps Found Live

**Date:** 2026-09-08
**Hosts:** pve-02 (pve-02, 10.10.0.12), pve-03 (pve-03, 10.10.0.13)
**Severity:** Medium — no data loss, but two of three Proxmox hosts had
zero storage/host-level observability this whole time, discovered only
because they were asked for by name.
**Status:** pve-02 fully resolved and verified live. pve-03 blocked — no
WoL MAC ever captured for it, so it can't be woken remotely right now.

## What was asked

Extend the new disk-space Grafana dashboard to cover pve-02 and pve-03 too,
even though they're WoL burst hosts that aren't always on.

## What was actually found — four independent gaps, not one

**1. `pve_exporter` was a bare, unsupervised background process.** Not in
systemd, not in Docker, not deployed via any Ansible role — two stray
copies were running from `pip install --user`, started by hand on
2026-08-16 and 2026-09-04, both still bound to :9100 with no restart-on-
failure. If either had died, or the IaC VM had rebooted, Proxmox
host/storage monitoring for the entire fleet would have gone silently
dark. Fixed: wrapped in a `systemd --user` service (lingering already
enabled for this account, so it survives reboots) — `~/.config/systemd/
user/pve-exporter.service`.

**2. `pve.yml`'s auth config only ever had pve-01's credentials.**
Prometheus's scrape config already listed all three PVE hosts as targets
(`10.10.0.11/.12/.13:8006`), and had for a while — but every request used
one hardcoded `module: [default]` param, and `default` in `pve.yml` only
had pve-01's Proxmox API token. pve-02 and pve-03 are standalone (non-
clustered) nodes with their own separately Vault-provisioned tokens
(`secret/proxmox/api/pve-02`, `pve-03` — confirmed genuinely different
secret values from pve-01's, via a boolean Vault comparison, values never
printed). Scraping them with pve-01's token always 401'd. This was
invisible: only pve-01 ever showed up in `pve_storage_info`/`pve_node_info`,
which looked identical to "pve-02/pve-03 are just asleep" — it wasn't only
that; even the rare moment they were awake, auth was wrong too.

Fixed: added `pve-02`/`pve-03` sections to `pve.yml` (own user/token_name/
token_secret per node — pve-03's token turned out to use a different
user/token_name string entirely, `terraform-pve@pve`/`terraform`, not
`terraform@pve`/`terraform-token` like the other two — read from the
actual `token_id` string, not assumed). Changed the Prometheus `pve` job
from one shared `module` param to a per-target `pve_module` label +
`relabel_configs` (`__param_module` sourced from the label) so each node
authenticates with its own token. See `prometheus.yml.j2`'s inline
comment for the full mechanism.

**3. Fresh PVE installs still had the paid enterprise repo enabled.**
Blocked `apt update` entirely on pve-02 (401 on
`enterprise.proxmox.com`, and apt fails hard on any single broken repo,
not just the affected one) — this blocked node_exporter installation and
would have blocked *any* future apt-based Ansible task on pve-02/pve-03.
pve-01 apparently already had this fixed by hand at some point; pve-02 and
pve-03 did not. Fixed properly, not just live: new
`Roles/proxmox_host/tasks/apt_repos.yml`, wired into `main.yml` as a new
Phase 0.5 (runs before anything else that needs apt), disables
`pve-enterprise.sources`/`ceph.sources`, adds the free
`pve-no-subscription` repo. Applied live to pve-02 via
`deploy_proxmox_host.yml --tags apt_repos`; **not yet applied to pve-03**
(unreachable — see below) or re-verified against pve-01 (pve-01's apt
already worked, so this task should be a no-op there, but hasn't been
run to confirm idempotency).

**4. A Docker single-file bind-mount staleness bug, distinct from the
already-known rules-file gap.** After fixing #2 above and deploying via
Ansible (`--tags monitoring`, reported "changed", ran the existing
`/-/reload` handler), the *host* file at `/opt/monitoring/prometheus.yml`
had the fix — but `docker exec prometheus cat /etc/prometheus/
prometheus.yml` kept showing the pre-fix content, for 3+ minutes across
two separate "changed" Ansible runs and an explicit reload call. Root
cause: `prometheus.yml` is bind-mounted as a **single file**
(`.../prometheus.yml:/etc/prometheus/prometheus.yml:ro`), not a directory
like `rules/` is. Docker pins a single-file bind mount to the inode
present when the container started; Ansible's `template` module writes
via atomic rename (a new inode), so the container's mount kept resolving
to the old, now-unlinked inode. Prometheus's own `/-/reload` re-reads
*the same stale path inside its mount namespace* — it has no way to know
the host swapped the underlying file. Directory bind mounts don't have
this problem (the container does a fresh directory-entry lookup every
time), which is exactly why the *rules* reload (fixed 2026-08-19, a
different incident) has been reliable all along while this one wasn't.

Fixed: `docker restart prometheus` (a real container restart, not just a
config reload) resolved it immediately, confirmed by re-reading the
container's live view. Added a dedicated `restart prometheus container`
handler in `Roles/monitoring/handlers/main.yml`, and repointed the
"Write Prometheus config" task's `notify` at it instead of the shared
reload-only handler — so every future `prometheus.yml` change gets a real
restart automatically, not just a reload that may or may not actually
take effect depending on inode timing. The rules/alertmanager reload
mechanisms are untouched (they don't have this failure mode).

## Verified live, end to end

- `pve-exporter.service` (user systemd) — running, both stray processes
  killed.
- `pve` Prometheus job: pve-01 **up**, pve-02 **up** (both auth'd
  correctly, both reporting real `pve_storage_info`/`pve_disk_*` data),
  pve-03 **down** ("No route to host" — genuinely offline, not an auth
  issue, confirmed via exporter logs).
- `node_exporter` job: pve-02 **up** and reporting real root-fs data.
- Dashboard (`homelab-disk-space-hosts-vms`) template variable and
  triage tables now include pve-02 automatically — no dashboard change
  needed, since nothing in it was hardcoded to pve-01 specifically.

## Still open

- **pve-03 cannot be woken remotely.** `host_vars/pve-03.yml`'s
  `pve_wol_mac` has always been empty — `wol_setup.yml`'s auto-detection
  was apparently never actually run against it (matches its "physical
  install in progress" status from 2026-08-23). Checked OPNsense's live
  ARP table and Kea's DHCP lease history for a residual MAC record —
  nothing (pve-03 uses a static IP, never DHCP, so it was never going to be
  in the lease table regardless). **Needs either**: the user provides the
  MAC (sticker on the NIC, BIOS/IPMI, or a router admin page), or someone
  powers pve-03 on locally once so `wol_setup.yml` can detect and persist
  it — after that, applying the same `apt_repos` + `node_exporter`
  sequence already proven on pve-02 should be quick.
- `apt_repos.yml` not yet run against pve-01 or pve-03 (pve-01 presumed
  already-fixed and therefore a no-op; pve-03 blocked as above) — worth a
  dry-run against pve-01 specifically to confirm idempotency, not assumed.
- The transient PromQL `duplicate series` error seen briefly on the
  dashboard's `group_left` panels right after this change (Prometheus
  briefly holding both the pre- and post-label-change sample sets for the
  same series) is expected to self-resolve as old samples age out of the
  staleness window (~5 min) — not re-verified after the fact, flagged as
  an assumption, not a confirmed resolution.
