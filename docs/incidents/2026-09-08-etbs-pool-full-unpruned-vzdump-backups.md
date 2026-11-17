---
type: Event
state: organized
ep_tier: storage
ep_particle: nas-01
belongs_to: "[[DISK_SPACE_MONITORING]]"
incident_date: "2026-09-08"
severity: high
status: resolved — root-cause fix (retention/pruning) still outstanding
---

# Incident: ETBs Pool Hit 0% Free — Unpruned vzdump Backups, ~2 Hours Before Next Backup Window

**Date:** 2026-09-08
**Host:** nas-01 (10.10.40.20), ZFS pool `ETBs`
**Severity:** High — caught with ~2 hours to spare before the nightly 03:00
vzdump run, which would otherwise have failed for the entire fleet
**Status:** Resolved for tonight. Root cause (no backup retention/pruning
configured) not yet fixed — will recur in a few weeks without it.

## What happened

User reported a disk-space fluctuation ("20% free → 10% → back to 20% in
6 hours") on nas-01's ETBs pool. The actual Prometheus history (queried via
Grafana's datasource proxy — direct port 9090 access is firewalled) showed
something different and worse: a **steady decline over ~10 hours, hitting
exactly 0.00% available at 23:05 and staying there** through the last
sample (00:50) — not a recovered blip. `paperless` (64%→0%), `podcast`
(14%→0%), and `pve-templates` (93%→0%) all converged to zero at the same
timestamp, confirming pool-wide exhaustion, not one dataset's growth — ZFS
child datasets share the pool's free space, so they all track the same
underlying number. The OS boot disk (`/`, `/boot/efi`) never moved the
entire time, ruling out an OS-disk cause.

The user's described 20%/10% pattern didn't match either the ETBs pool or
the boot disk in the actual recorded data — most likely a different
panel/host was being looked at, or a very brief up-tick inside the
otherwise-worsening trend was mistaken for a recovery. Not fully
reconciled; flagged rather than assumed.

## Root cause

`/ETBs/backups` alone held **4.9TB of the pool's 5.45TB capacity**: 197
`vzdump` backup files across 21 VMs, spanning 2026-08-10 to 2026-09-07
(~29 days), averaging ~10 backups kept *per VM* with **zero pruning
configured**. This is the same root cause flagged in the 2026-09-05
incident (`128GB free on 5.45TB`) — that incident was alerted on but never
actually fixed at the source; the pool simply kept filling until it hit
true zero.

Confirmed nothing was actively writing to `/ETBs` at the time of
investigation (`lsof +D /ETBs` empty) — this was pure accumulated backlog,
not a runaway process.

## Resolution

Pruned to the 3 most recent backup-sets per VM (each set = `.vma.zst` +
`.log` + `.notes`), keeping everything from the last 3 days untouched
(verified via a pre-delete sanity grep before removing anything).
Deleted 296 files, all dated 2026-08-13 through 2026-09-04.

**Before → after:**
```
ALLOC  5.33T -> 3.54T
FREE   128G  -> 1.92T
FRAG   48%   -> 12%
CAP    97%   -> 64%
```

Verified via `zpool list` and `df -h /ETBs` directly on nas-01 after the
delete, not assumed from the estimate alone (the estimate predicted ~3.09TB
freed; actual pool FREE increased by ~1.8TB immediately, with more likely
still reclaiming asynchronously — ZFS COW space reclaim isn't always
instant in `zpool list`'s FREE column).

## Still open — the actual fix

This will refill and hit zero again in a few weeks without a real
retention policy. The nightly `vzdump` cron job (see
`project_pve_nas_backup_automation` memory — `vzdump→nfs-backups daily
03:00`) has no `--prune-backups`/`maxfiles` equivalent configured at all.
Needs one of:
- `vzdump ... --prune-backups keep-last=3` (or similar) added to the cron
  invocation on the source Proxmox hosts (pve-01/pve-01, etc.), or
- A retention policy set on the `nfs-backups` storage definition in
  Proxmox itself (`Datacenter -> Storage -> nfs-backups -> Backup
  Retention`), which would apply automatically to every future vzdump run
  targeting it, not just a cron flag on one job definition.

Not done yet — this incident's fix was the manual prune only. Flagged as
the actual next action, not a nice-to-have.
