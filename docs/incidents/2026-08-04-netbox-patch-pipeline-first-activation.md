---
type: Event
state: organized
ep_tier: platform
ep_particle: netbox
belongs_to: "[[NETBOX_CUSTOM_FIELDS_SETUP]]"
related_to:
  - "[[NETBOX_STATUS_AUTOMATION]]"
incident_date: "2026-08-04"
severity: low
status: resolved
---

# Incident: NetBox Patch Lifecycle Pipeline Failed 3 Ways on First-Ever Activation

**Date:** 2026-08-04
**Service:** `rolling_update.yml`, `netbox_setup_patch_fields.yml`,
`deploy_patch_exporter.yml`, `netbox_patch_exporter.py`
**Severity:** Low — caught before any VM was touched, nothing broke in
production
**Status:** Resolved

## What Happened

This whole patch-lifecycle pipeline (snapshot-before-patch rolling updates,
a NetBox-backed exporter, and a `patch_lifecycle.json` Grafana dashboard)
was written in a single prior AI chat session
(`IaC/docs/Iac_Chat9.md`) but **never actually run against live
infrastructure**. Activating it for the first time surfaced three real bugs.

## Root Causes

1. **`netbox_url` pointed at a hostname that doesn't resolve.**
   `group_vars/all.yml` had `netbox_url: "http://netbox.internal"` — no such
   DNS record exists. The real, reachable endpoint is
   `https://netbox.home.example.org` (valid Let's Encrypt cert, confirmed).

2. **Vault credential-fetch tasks had no tags, so `--tags <specific-tag>`
   skipped them entirely.** `netbox_setup_patch_fields.yml`,
   `rolling_update.yml`, and `deploy_patch_exporter.yml` all had a "Fetch
   NetBox API token from Vault" + "Set netbox_token fact" task pair with no
   `tags:` at all. Ansible only runs untagged tasks when no `--tags` filter
   is given, or when the tag list includes `always`. Running
   `--tags create_fields` (as the playbook's own header comment
   recommends) skipped credential setup, so every subsequent task failed
   with `'netbox_token' is undefined`.

3. **Vault path was passed to `vault_kv2_get` in the wrong form.**
   `netbox_vault_path: "secret/netbox/api"` (the convention used everywhere
   else in this repo's *documentation* and curl examples) was passed
   directly as the module's `path:` parameter. But
   `community.hashi_vault.vault_kv2_get`'s `path` is **relative to
   `engine_mount_point`** (default `secret`) — passing the full
   `secret/netbox/api` string made it look for
   `secret/data/secret/netbox/api`, a 404. The correct, already-proven
   pattern lives in `Roles/restic/tasks/install.yml`:
   `path: "{{ var | regex_replace('^secret/', '') }}"` +
   `engine_mount_point: secret`.

4. **(Found after the above three, mid-field-creation) NetBox 4.2 changed
   how `select`-type custom fields are created.** POSTing an inline
   `choices: [...]` list (the pre-4.x API shape the playbook was written
   against) now fails with `"choice_set": ["Selection fields must specify
   a set of choices."]`. NetBox 4.x requires creating a separate
   `/api/extras/custom-field-choice-sets/` object first (`extra_choices` as
   a list of `[value, label]` pairs) and referencing it by ID via
   `choice_set` on the custom field.

## Resolution

All four fixed directly in the playbooks (not worked around):

- `group_vars/all.yml`: `netbox_url` → `https://netbox.home.example.org`,
  `netbox_api_validate_certs` → `true`.
- Added `tags: [always]` (or the play's own tag set, where a play-level tag
  already existed) to every Vault credential-fetch task in all three
  playbooks.
- Added `engine_mount_point: secret` + `regex_replace('^secret/', '')` to
  every `vault_kv2_get` call for `netbox_vault_path` in all three
  playbooks, matching the restic role's existing correct pattern.
- Added a choice-set create/lookup step to `netbox_setup_patch_fields.yml`
  before the `patch_status` field creation, and switched that one field's
  create body from `choices:` to `choice_set: <id>`.
- Also fixed a secondary bug in the same file: the final `Summary` debug
  task referenced `netbox_vm_map`, which is only set when `--tags seed_vms`
  runs — running `--tags create_fields` alone crashed on that too. Guarded
  with a `default()`/`is defined` check.

Verified live: all 7 patch custom fields exist in NetBox
(`proxmox_vm_id`, `proxmox_node`, `health_check_port`, `health_check_url`,
`docker_enabled`, `patch_status` with a working choice set, `patch_last_run`),
and 7 of 10 `patchable_vms` inventory hosts were seeded successfully. The
remaining 3 (`immich-01`, `dmz-nc-01`, `dmz-pg-01`) aren't registered as
NetBox VM objects yet — `dmz-nc-01` is believed to be the same host as the
already-registered `nc-01` (pending DMZ migration), so the real gap may be
smaller than it looks; not yet reconciled.

## Prevention

The underlying pattern — code written once in a chat session and never
executed — is exactly what `IaC/docs/Iac_Chat9.md`-sourced automation keeps
producing. None of these four bugs were subtle; all four would have been
caught by a single real `--check`-free run. **Any playbook whose header
comment says "run this" but whose git history shows it was never invoked
against a live host should be treated as unverified, not working, no matter
how complete it reads.**

## Still Open (tracked separately, not part of this incident)

- `deploy_patch_exporter.yml` (exporter container + Grafana dashboard) not
  yet run.
- `rolling_update.yml` not yet dry-run or real-run against any host.
- Recurring patch-cycle scheduling not yet set up.

See the active plan at `~/.claude/plans/encapsulated-wondering-noodle.md`
(Phase A) for the resumption point.
