---
type: Event
state: organized
ep_tier: infrastructure
ep_particle: gpu-01
belongs_to: "[[PROXMOX_HOST_IaC]]"
related_to:
  - "[[DISK_SPACE_MONITORING]]"
  - "[[HOME_ASSISTANT_MIGRATION]]"
incident_date: "2026-08-04"
severity: high
status: resolved
---

# Incident: gpu-01 OOM Hang Broke Home Assistant Voice Assistant — Root Disk Also Found 100% Full

**Date:** 2026-08-04
**Service:** gpu-01 (VM 270) — Ollama, Whisper, Open-WebUI, immich-ml
**Severity:** High — broke the Home Assistant voice pipeline
**Status:** Resolved

## What Happened

User reported the Home Assistant voice assistant on iPhone was hanging after
speech-to-text completed — the transcription worked but no response ever
came back.

## Root Cause

Two separate, compounding problems, both on gpu-01:

1. **Guest OOM hang.** gpu-01's Proxmox VM config had `memory=16384MB`
   (ceiling) but `balloon=6144MB` (floor) — the balloon driver was supposed
   to inflate up to 16GB on demand. In practice the guest was stuck around
   5.6GB usable with **zero swap configured** (`swapon --show` returned
   nothing). When Ollama's memory demand exceeded that, there was no swap
   to fall back on and the balloon driver did not rescue it in time. The
   VM became unresponsive at the application layer — SSH and the Ollama
   API (port 11434) both timed out — while still answering ICMP (kernel
   network stack stayed up even though nothing above it did). n8n's
   "Home Assistant → Ollama (qwen2.5:7b)" workflow called out to Ollama
   and the request simply never returned.

2. **Root disk 100% full — 0 bytes free.** Found while trying to add a
   swapfile: gpu-01's 20GB OS root disk (`scsi0`, on `local-lvm`) was
   completely consumed by the CUDA 12.3 toolkit (~4.6GB) plus ~2.3GB of
   NVIDIA Nsight Compute/Systems profiling tools that aren't needed for
   inference workloads. This was severe enough that even Ansible's own
   control connection couldn't create a temp directory on the host. A full
   root disk this way is its own failure mode (nothing can log, write temp
   files, or update state) independent of the memory issue, and likely
   contributed to instability beyond just the voice pipeline.

Also discovered during triage: this `scsi0` disk was **never declared in
Terraform at all** — `IaC/Terraform/envs/gpu-01/main.tf` only ever declared
one disk (`virtio0`, the 500GB `/var/lib/ollama` data disk on `zfs-nvme`).
The 20GB boot disk was pure out-of-band drift since the VM's creation.
Additionally, `node_exporter` was not installed anywhere on gpu-01 (or, it
turned out, almost anywhere in the fleet — see "Also Found" below), so none
of this had any Prometheus visibility before it happened.

## Impact

- Home Assistant voice assistant non-functional (STT worked, response
  never came back) for an unknown period until reported.
- gpu-01 fully unresponsive at the application layer (SSH + Ollama API both
  hung); only recoverable via a Proxmox-level hard reset since no graceful
  in-guest shutdown was possible.

## Resolution

1. `qm reset 270` via Proxmox API to restore immediate service — confirmed
   via a real Ollama generation call, not just a port check.
2. **Memory:** Terraform (`envs/gpu-01/terraform.tfvars`) changed from
   `memory=16384 / floating=6144` to `memory=20480 / floating=20480` — a
   fixed 20GB allocation, ballooning disabled. The 6GB-floor design relied
   on the balloon driver inflating on demand; that's exactly what didn't
   save gpu-01 here, so the fix removes that dependency rather than tuning
   its parameters. Applied live via `terraform apply` — Proxmox supports
   memory hot-add, no reboot needed for this part. **Trade-off accepted
   knowingly**: the Proxmox host (`pve-01`) only had ~8GB genuinely free
   across all other running VMs at the time; a fixed 20GB guarantee for
   gpu-01 (vs. the previous 6GB floor) leaves materially less host-level
   headroom. Host free RAM after this change: ~4.2GB. Worth revisiting if
   other VMs start failing to start/grow.
3. **Boot disk:** grown 20GB → 60GB via the Proxmox API (`qm resize`),
   `growpart` + `resize2fs` in-guest, and — per user request — **live
   storage-migrated from `local-lvm` to `zfs-nvme`** (`qm move_disk`, zero
   downtime) with `discard=on`, `ssd=1`, `aio=io_uring`, `cache=none` to
   match the tuning already used on the data disk. `iothread=1` was **not**
   set — that requires `scsihw=virtio-scsi-single` (currently the shared
   `virtio-scsi-pci` controller) and a reboot; left as a follow-up rather
   than forcing another restart in the same session.
4. **Swap:** added an 8GB swapfile (`/swapfile`, `vm.swappiness=10` — an
   emergency net, not routine paging) via a new Ansible role (see below).
5. **Terraform reconciliation:** the boot disk is now declared in
   `main.tf` as a second `disk {}` block. **Important gotcha hit while
   doing this** — see "Lessons Learned."
6. **Monitoring gap closed:** built `Roles/node_exporter/` (didn't exist —
   see "Also Found") and deployed it to gpu-01; added `HostHighMemoryUsage`
   (>90%) and `HostHighSwapUsage` (>50%) alert rules to
   `Roles/monitoring/templates/rules/critical_infra.yml.j2`, matching the
   existing `HostLowDiskSpace`/`HostCriticalDiskSpace` pattern from the
   2026-08-02 web-01 incident. Confirmed live: gpu-01 shows as an `up`
   Prometheus target and both new alert rules loaded successfully.

## Also Found (fleet-wide gap, not just gpu-01)

`node_exporter` was referenced in every Prometheus scrape config
(`prometheus.yml.j2`'s `node_exporter` job iterates all hosts) but **had no
install task anywhere in the repo** — an assumption that was never true.
Only gpu-01 has been fixed so far; most other hosts still show as `down` in
Prometheus's `node_exporter` job. Fleet-wide rollout
(`playbooks/deploy_node_exporter.yml`, already written, just needs a run
without `--limit`) is tracked as follow-up work, not done in this incident.

## Lessons Learned

1. **bpg Terraform provider matches `disk {}` blocks positionally, not by
   `interface`.** Adding a new disk block *before* an existing one in
   `main.tf` made Terraform plan to mutate the *already-tracked* disk (the
   live 500GB Ollama data disk) into the new disk's identity — datastore,
   interface, and size all changing to match the new block. Applying that
   plan would have corrupted/reinterpreted a disk holding real model data.
   Caught by reading the plan output carefully before applying — **always
   read a Terraform plan's per-attribute diff for disk/network blocks, not
   just the top-line resource count, when a resource has multiple blocks of
   the same type.** New blocks must be appended after existing ones to stay
   additive.
2. **Proxmox disk resize is one-way and config-vs-reality can silently
   drift.** `qm resize` refuses any size decrease, even when the decrease
   would only correct stale config metadata to match live reality — it
   doesn't verify against the true underlying volume, just its own last
   recorded value. This makes drift hard to detect: the VM config can claim
   a size the live block device isn't actually presenting.
3. **Live virtio-blk capacity-change notifications don't always propagate
   to a running guest**, even when Proxmox reports the resize task as `OK`
   and the storage-layer volume genuinely is the new size. A clean reboot
   is the reliable fallback when `partprobe`/sysfs rescan doesn't pick up
   the new size.
4. A disk with two different volumes that happen to share the same
   volume-index name (`vm-270-disk-0` existed on **both** `local-lvm`
   *and* `zfs-nvme` — one per storage backend) is a trap for pattern-
   matching by name alone. Always confirm which Proxmox *interface*
   (`scsi0` vs `virtio0`) and *storage pool* a volume name belongs to
   before treating two same-named volumes as the same disk.
