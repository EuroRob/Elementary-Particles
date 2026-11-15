# Agent access lanes: who may see what

**Status (2026-11-05): designed and tooling built. Step 0 (audit log) is on; steps 1–5 not switched over.**

The rule is the same everywhere: **AI suggests, checks code, people decide.** This doc applies it to
access. Each AI here gets the access its job needs, and no more:
- Claude Code is the cloud model that builds the infrastructure.
- DOMO and its partners are the on-premise models working on the application side.

Serge and Scout were separated this way from the start (per-person MCP servers, Scout's own narrow
database link). Claude Code never was.

## What Claude Code can reach today (checked 2026-11-05)

Claude Code runs as `netops` on the IaC VM. That user can read and use:

| What | Where | Opens |
|---|---|---|
| Vault **root token**, no expiry | `/opt/vault/.env` (`VAULT_TOKEN`) | every secret, every policy, every auth method |
| AppRole `terraform` (secret_id never expires) | same file (`TF_VAR_role_id/secret_id`, `VAULT_ROLE_*`) | policy `terraform-policy`: read, write and delete on all of `secret/`, plus creating tokens |
| 41 other credentials in plain text (52 entries in all) | same file | Nextcloud, Outline, Home Assistant, Immich, Firefly, wger, n8n, Jellyfin, … |
| `~/.ssh/id_ansible` | home directory | root on pve-01, pve-03, pve-02; sudo on every VM. That means every database (`sudo -u postgres psql`), every NFS share, every container |
| Continue's config | `~/.continue/config.yaml` (mode 644) | the owner's Nextcloud, Outline and wger MCP servers on gpu-01 |

There's also **no Vault audit log** (`sys/audit` is empty), so nothing records what was read.

`netops` does **not** have passwordless sudo. That's what makes the plan below work: a step behind
sudo needs the owner's password, and Claude Code can't type it.

## The lanes

| Lane | Who | Reads | Can't |
|---|---|---|---|
| **infra** | Claude Code (cloud), token `claude-iac` | hypervisors, firewall, DNS, Traefik, Forgejo, NetBox, Grafana, monitoring | read any database, backup, file share, identity or family-service secret |
| **runner** | the deploy runner: Ansible/Terraform runs owner starts | everything, as deploys need | delete secrets, change policies |
| **repo** | code partner (on premise, [CODE_PARTNER.md](CODE_PARTNER.md)) | the GitOps repo only | anything in Vault at runtime; any host but gpu-01, git-01, web-01 |
| **household** | each family service, Serge/Scout/Continue MCPs per person | its own data | anyone else's |

Per-person agents exist for the two adults only. The children have no agent of their own, by
decision, not by omission: an agent that can read a child's calendar, mail or notes is a decision
for the parents to make later, and nothing here grants it by default.

The lane of every Vault path is written down in [`vault/lanes.yml`](../vault/lanes.yml):
19 infra, 14 runner and 36 household top-level paths as of today. The policies are generated from it
([`vault/policies/`](../vault/policies/)).

A new secret can't slip into Claude Code's lane by accident:
- `scripts/vault_lanes.py check` fails if Vault has a path that `lanes.yml` doesn't name.
- Run it as a pre-deploy step, like `vault_preflight.py`.

Claude Code may still **create** a new secret anywhere: seeding a new service is fine without asking.
It can never read that secret back, nor update or delete one outside its lane.

### The limit to be honest about

A Vault policy alone doesn't separate anything while Claude Code holds `id_ansible`: root SSH reads
the database files directly. So the switch happens in steps. Each step is worth doing on its own,
and only step 3 closes the gap.

## Switching over

Each step is the owner's to run: every one either needs a token Claude Code shouldn't have or changes what
Claude Code can do. The commands are written so that no secret is printed.

### Step 0: audit log (DONE 2026-11-05 20:13 CET)

Turned on with the owner's OK. One thing to know: **Vault refuses every request if it can't write the
audit log**, so the log gets rotated by [`vault/logrotate-vault-audit`](../vault/logrotate-vault-audit).
Install it with `sudo install -m 0644 vault/logrotate-vault-audit /etc/logrotate.d/vault-audit`.
The file is 0600 and owned by the container's user, so reading it needs sudo too.

How it was turned on:

```bash
( set -a; source /opt/vault/.env; set +a; python3 scripts/vault_lanes.py audit --yes )
```

- Vault then writes every request to `/opt/vault/logs/audit.log`, mode 0600.
- Secret values are HMAC'd, never stored.
- From then on there's a record of what each token read.

### Step 1: Claude Code gets its own token

```bash
( set -a; source /opt/vault/.env; set +a
  python3 scripts/vault_lanes.py diff                # what will change
  python3 scripts/vault_lanes.py apply --yes         # policies claude-iac + deploy-runner, token role, AppRoles
  python3 scripts/vault_lanes.py issue --yes         # -> ~/.config/vault/claude-iac.token (0600)
  python3 scripts/vault_lanes.py verify )            # Vault's answer: reads exactly the infra lane
```

Then owner edits `/opt/vault/.env` himself (CLAUDE.md guardrail 8):
1. **Move out** to a root-only file, e.g. `/root/vault-break-glass.env` (0600):
   - `VAULT_TOKEN` (root);
   - the `terraform` AppRole ids (`TF_VAR_role_id`, `TF_VAR_secret_id`, `VAULT_ROLE_ID`, `VAULT_ROLE_SECRET_ID`);
   - the 41 other credentials.
2. **Put in** what Claude Code needs:
   - `VAULT_TOKEN` = the claude-iac token;
   - the `claude-iac` AppRole ids for Terraform runs;
   - `PROXMOX_*`.

`/opt/vault/.env` keeps its name, so the CLAUDE.md commands still work.

What Claude Code can still do afterwards:
- Terraform plan/apply on any VM (Proxmox and SSH public keys are infra);
- Pi-hole, OPNsense, Traefik, NetBox, Forgejo PRs, monitoring;
- creating a new service's secrets.

What becomes a runner job:
- any playbook that reads a household or runner secret (`assistant_stack`, `music_studio`'s
  database grant, `deploy_code01.yml`). It stops with a 403 at the Vault lookup, which is the sign
  to hand it over.

### Step 2: the deploy runner

On the IaC VM:
1. A Unix user `iacrun` owns:
   - the `deploy-runner` AppRole secret_id;
   - `id_ansible`, moved from netops;
   - the patch-window timers.
2. One entry point: `sudo -u iacrun /opt/iac/bin/run-deploy <playbook> [--tags …] [--limit …]`, from a
   clean checkout of `origin/master` (merged code only).
3. sudoers lets `netops` run exactly that command, **with a password**.
4. Claude Code prepares the change and the PR. owner merges and starts the run.

This is the patch-window pattern, extended to every deploy.

### Step 3: SSH without data access

1. Claude Code gets its own key (`id_claude`) for a user `claude-ro` on every VM.
2. That user has no sudo, plus the `systemd-journal` and `adm` groups: logs and service status, but
   not files, databases or Docker (the docker group is root).
3. Exceptions are hosts that hold no household data (dns-01, vpn-01, git-01, fw-01): they may grant
   sudo there.
4. `id_ansible` stays with `iacrun` (step 2).

### Step 4: looking at application data, on purpose

Diagnosing an app sometimes needs its data. Example: today's kitchen-speaker question needed the HA
token to read the voice pipeline's runs. Two ways, both chosen by owner each time:
- **The on-premise side does it.** A DOMO partner or Continue on gpu-01 uses that person's own
  narrow tools (the existing MCP servers). The data never reaches the cloud model.
- **A time-boxed grant.** owner issues a short-lived token for one path (planned:
  `vault_lanes.py grant home-assistant --ttl 1h`). It lands in Claude Code's audit trail like
  everything else.

### Step 5: Continue's household tools

As long as Continue and Claude Code run as the same Unix user, Claude Code can read anything
Continue can, including its MCP endpoints. Two options:
- move the household MCPs to the browser editor on code-01 (code partner, phase 2), where Claude
  Code doesn't run;
- or keep them in Continue, with their keys in a file only the owner's own login can read.

Until then, `chmod 600 ~/.continue/config.yaml` at least stops other local users reading it.

## Guardrails in Claude Code itself (a tripwire, not a wall)

`~/.claude/settings.json` can deny tool calls, e.g. `Read(//opt/vault/logs/**)`, `Bash(psql:*)`, or
`curl` to `nextcloud`/`outline` hosts. The same user can edit that file, so this only stops
accidents. The boundary is the Unix user plus the Vault policy above. Set it up with Claude Code's
`/update-config` when step 1 is done.

## Files

| File | What |
|---|---|
| [`vault/lanes.yml`](../vault/lanes.yml) | every Vault path → lane |
| [`vault/policies/claude-iac.hcl`](../vault/policies/claude-iac.hcl) | generated: Claude Code's policy |
| [`vault/policies/deploy-runner.hcl`](../vault/policies/deploy-runner.hcl) | generated: the runner's policy |
| [`scripts/vault_lanes.py`](../scripts/vault_lanes.py) | check / render / diff / apply / audit / issue / verify |

## Not done yet

- Steps 1–5: not switched over (step 0 is on).
- `run-deploy` and the `iacrun` user (step 2): designed, not written.
- `vault_lanes.py grant` (step 4): designed, not written.
- The AppRoles aren't bound to an address yet: Vault runs in Docker, so the right source address
  must first be read from the audit log (step 0).
