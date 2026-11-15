# Homelab IaC — Claude Context
<!--
  SCOPE HINTS — read the sub-directory CLAUDE.md for the task you are doing:
  Ansible work  → IaC/Ansible/CLAUDE.md
  Terraform work → IaC/Terraform/CLAUDE.md
  OPNSense/FW   → IaC/Ansible/Roles/opnsense/CLAUDE.md
  Do NOT read IaC/docs/Iac_Chat*.md — those are conversation archives.
  Do NOT read IaC/docs/ unless asked. Reference IaC/docs/ARCHITECTURE_INDEX.md instead.
-->

## Security Guardrails — READ BEFORE ANY SECRET OPERATION
These rules apply to every session. No exceptions, even in agent/autonomous mode.

1. **Never print secret values to stdout — including hashes/digests of them.** When generating or handling passwords, tokens, keys, or client secrets: generate in a subshell, pipe directly to the destination, and never `echo`, `tee`, `cat`, `grep`, or `printenv` the raw value. This also covers a bcrypt/sha256 hash of a secret — treat it as sensitive too, not just the plaintext. Use `no_log: true` in all Ansible tasks that handle secrets. Before running any command that touches a secret path, ask "does this output include the actual secret bytes?" — if yes, redesign it: check length (`wc -c`, `length()`) instead of value, presence via `grep -c`, or a match via a script that prints only `MATCH`/`NO MATCH` (e.g. `bcrypt.checkpw(...)`) rather than the compared values. This has been violated multiple times in practice despite being written policy — treat it as a per-command discipline, not a rule to trust yourself to remember in the moment.
2. **Never commit secrets.** Files in `IaC/secrets/`, `terraform.tfvars` (non-example), `.env` files — always gitignored. Before any `git add`, check `git status` for these file types.
3. **Shred temp files.** Any temp file written with secret content must be deleted with `shred -u` or `rm -f` immediately after use. Do not leave secrets in `/tmp/`.
4. **Vault reads: redact on display.** When reading Vault paths to verify, show only key names, not values. Never log or print Vault token, role_id, secret_id, or any secret payload.
5. **Source, don't export.** Use `set -a && source /opt/vault/.env && set +a` in a subshell. Never paste token values into commands shown to the user.
6. **Confirm before seeding new Vault paths.** Before writing a new secret to Vault, state the path and key names (not values) and confirm it's the right path.
7. **No secrets in CLAUDE.md or memory files.** These files may be committed. Store only paths and key names — never values.
8. **Never remove entries from `/opt/vault/.env` on the user's behalf.** The user manages that file directly. When keys have been confirmed in Vault, inform the user which entries they can safely delete — but do not delete them. Accidental removal could silently break automation.
9. **Never generate or export a WireGuard peer's `.conf` file, QR code, or any credential-bearing peer config** — this includes calling wg-easy's `/api/wireguard/client/.../configuration` or `/qrcode.svg` endpoints on the user's behalf, even when asked and even to fix a real problem (e.g. a stale AllowedIPs). Added 2026-09-03 after doing exactly this for a peer named "Papa_Mac" — sent via a private out-of-band channel (not printed in chat), but the user correctly flagged that the file itself is a live, complete network credential (private key + preshared key) regardless of delivery channel, equivalent to handing out an SSH private key. **Creating new peers and exporting/re-exporting existing peer configs is the end user's job**, done directly via the wg-easy admin UI (`http://10.0.10.5:51821` / `vpn.internal`). What Claude *can* still do: list/inspect peer state (name, enabled, AllowedIPs, handshake time) for diagnosis, and make server-side changes (AllowedIPs defaults in the wireguard role, DNS, firewall rules, admin password resets) — the line is specifically at generating or handling the peer credential file itself. If a peer's config is stale after a server-side fix, tell the user to re-fetch/re-scan it themselves — don't fetch it for them.
10. **New or rotated credentials go to Vault — never to a file, not even a shredded scratch file, not even sent privately via SendUserFile.** Added 2026-09-04 after resetting the wg-easy admin password 4 times in one session (chasing unrelated Authelia/AllowedIPs/LAN-firewall issues that turned out not to be the password at all): the first 3 resets each got written to a local scratch file and delivered via SendUserFile, shredded locally right after — technically compliant with guardrail 1-3, but the user still ended up with 3 similarly-named files and no way to tell which was current. Reset #4 wrote the plaintext straight to `secret/vpn/wg-easy` → `admin_password` (a new key, alongside the existing `password_hash`) instead — one durable value, no ambiguity, nothing to shred. Going forward: pipe a generated credential directly into the Vault write, never through an intermediate file on disk at all if avoidable; if a file is briefly unavoidable, its content must never leave the local machine — hand the user the Vault path/key (per guardrail 6) and, if they need the raw value, the read command to fetch it themselves in their own terminal, not the value itself. This does not relax guardrail 9 (WireGuard peer `.conf`s specifically must not be generated at all, Vault or otherwise) — it governs bare credential values: passwords, tokens, API keys.
11. **Never clone a real, identifiable person's voice without their explicit consent — public figure or not.** Added 2026-09-07 during Concierge voice-persona design, after the user floated cloning specific actors' voices (Hugh Grant, Jon Stewart) as a reference for a TTS persona. A voice is not a generic style — TTS voice-cloning tools (Chatterbox and similar) make lifting one from a few seconds of audio technically trivial, which is exactly why this needs to be a standing rule, not a case-by-case judgment call. What's fine: aiming for a *character* (warm/posh/charming; wry/deadpan) via an original synthetic voice, a self-recorded reference clip from someone who consents, or a licensed voice actor's commissioned session. What's not: a reference clip sampled from a real person's actual performance (a movie, a show, an interview) without their consent, regardless of technical ease or how "just for personal homelab use" it feels. See `IaC/docs/CONCIERGE_VOICE_PERSONAS.md` and `Ansible/Roles/gpu/files/chatterbox-voices/README.md` for where this is enforced in practice.

## Identity
Elementary-particles homelab. Terraform + Ansible + HashiCorp Vault + n8n.
All automation runs **from the IaC VM** (10.0.0.15 / vault.internal / iac.internal).

## Node Map
| Host | IP | Role | Proxmox node |
|---|---|---|---|
| IaC VM | 10.0.0.15 | Vault + Terraform + Ansible + API server — **VM 103 ("Netsible") on pve-01**, not bare-metal (corrected 2026-09-13; never tracked as a managed VM anywhere until found during a monitoring gap investigation, see incident_iac_api_n8n_cascade_2026-09-13 memory) | pve-01 |
| pve-01 | 10.0.0.11 | Primary Proxmox hypervisor | — |
| pve-02 | 10.0.0.12 | Burst GPU (WoL-managed) | — |
| pve-03 | 10.0.0.13 | Burst media (WoL-managed) — physical install in progress 2026-08-23 | — |
| fw-01 | 10.0.10.1 | OPNSense firewall — manual install | pve-01 |
| dns-01 | 10.0.30.10 | Pi-hole DNS | pve-01 |
| vpn-01 | 10.0.10.5 | WireGuard + wg-easy | pve-01 |
| nas-01 | 10.0.40.20 | OpenMediaVault — **VM 232 on pve-01**, not bare-metal (corrected 2026-08-10; 3x 6TB HDD passthrough for the ETBs ZFS pool) | pve-01 |
| pg-01 | 10.0.40.10 | PostgreSQL 15 primary | pve-01 |
| web-01 | 10.0.30.20 | Traefik + n8n + Grafana + NetBox | pve-01 |
| git-01 | 10.0.30.31 | Forgejo (git host, port 3000) — cut over from GitLab CE 2026-07-29 | pve-01 |
| git-01-gitlab-decommissioned | 10.0.30.30 | **DECOMMISSIONED** — old GitLab CE, orphaned (still running, no longer routed); Pi-hole hostname already renamed | pve-01 |
| web-02 | 10.0.30.11 | Jira + Confluence | pve-01 |
| gpu-01 | 10.0.30.50 | Ollama + Open-WebUI + YOLO | pve-01 |
| gpu-02 | 10.0.30.51 | Ollama burst (WoL) | pve-02 |
| media-vm | 10.0.50.10 | Jellyfin | pve-03 |

> **pve-01 = pve-01, pve-02 = pve-02, pve-03 = pve-03** — same physical hosts; Proxmox
> was installed with hostnames "pve-01"/"pve-02"/"pve-03" respectively. The `pve-0N`
> form is only the Ansible/Terraform inventory alias (host_vars filename,
> inventory key, WoL target) — it is NOT the real Proxmox node name and must
> never be passed as `proxmox_node:`/`node_name` to the API or to Terraform.
> **Known pre-existing exception, not yet fixed**: `services.yml`'s
> `proxmox_hosts.pve-02` entry and Terraform `envs/gpu-02/variables.tf` both
> still default `proxmox_node` to the literal string `"pve-02"` instead of
> `"pve-02"` — gpu-02 currently only works because its `terraform.tfvars`
> overrides it correctly. Worth fixing the same way pve-03/pve-03 was fixed here,
> but out of scope for this change since gpu-02 is live — flag before touching.
> IaC VM and pve-01 will move to VLAN10 (10.0.10.15 / 10.0.10.11) after MGMT VLAN migration.

## DNS (Pi-hole local records — add after Layer 1)
```
vault.internal  → 10.0.0.15
iac.internal    → 10.0.0.15
```
Before dns-01 exists: `echo "10.0.0.15  vault.internal  iac.internal" >> /etc/hosts` on IaC VM.

## VLAN Scheme
| VLAN | Subnet | Purpose |
|---|---|---|
| 10 | 10.0.10.0/24 | Management (fw-01, vpn-01; future: IaC VM + pve-01) |
| 20 | 10.0.20.0/24 | IoT (isolated) |
| 30 | 10.0.30.0/24 | Services (VMs: dns, web, git, gpu, apps) |
| 40 | 10.0.40.0/24 | Storage (nas-01, pg-01) |
| 50 | 10.0.50.0/24 | Media |
| 99 | — | WAN/DMZ (OPNSense vtnet0) |

## Deployment Layers
| Layer | What | How |
|---|---|---|
| 1 | fw-01 (manual gate) + dns-01 | bootstrap.sh |
| 2 | vpn-01 | bootstrap.sh |
| 3 | nas-01 + pg-01 | bootstrap.sh |
| 4 | web-01 (Traefik, n8n, Grafana, NetBox) | bootstrap.sh |
| 5 | forgejo-01 (Forgejo git host) | n8n webhook — replaces git-01/GitLab CE, cut over 2026-07-29 |
| 6 | web-02 (Jira, Confluence, Immich, Outline) | n8n webhook |
| 7 | gpu-01 (Ollama, Open-WebUI, YOLO) | n8n webhook |
| 8 | media-vm (Jellyfin) | n8n webhook |
| 9 | AI query router (Claude API fallback) | n8n workflow import |

```bash
# Layers 1-4 (from IaC VM, before n8n exists)
bash IaC/bootstrap.sh

# Layers 5-8 (after web-01 is up)
curl -X POST http://10.0.30.20:5678/webhook/deploy-stack \
  -H "Content-Type: application/json" \
  -d '{"layer_start": 5, "layer_end": 8}'
```

## IaC VM Directory Layout
```
IaC/
  CLAUDE.md                   ← you are here
  bootstrap.sh                # Layers 1-4 sequential deploy
  vault_preflight.py          # Seed + verify Vault (run before bootstrap.sh)
  api_server.py               # FastAPI — n8n calls this for layers 5-8
  vault_helper.py             # Vault AppRole client helper
  iac-api.service             # systemd unit for api_server.py
  secrets/
    vault_seed.env.example    # Template — copy to vault_seed.env, fill, then run preflight
  Terraform/
    envs/                     # One subdir per VM: opnsense/ dns-01/ vpn/ pg-01/ web-01/ git-01/ web-02/ ...
  Ansible/
    ansible.cfg               # inventory = inventory/services.yml
    inventory/
      services.yml            # SINGLE SOURCE OF TRUTH — all hosts, IPs, vars
      host_vars/              # Per-host overrides: pve-01.yml, pve-02.yml, pve-03.yml, nas-01.yml
      group_vars/
        all.yml               # Global defaults incl. vault_addr
    playbooks/                # deploy_all.yml, bootstrap.sh, deploy_opnsense.yml, ...
    Roles/                    # pihole_dns, wireguard, proxmox_host, opnsense, postgres, ...
  orchestration/
    full_stack_deployment.yaml  # n8n workflow — import into n8n after Layer 4
  scripts/
    import_repo_to_gitlab.sh  # SUPERSEDED — repo now lives on Forgejo (forgejo-01); origin remote points at https://forgejo.home.example.org/owner/GitOps.git
    sync_homepage.py          # Sync service list → Homepage dashboard
    sync_netbox.py            # Sync inventory → NetBox IPAM
```

## Key Commands (on IaC VM)
```bash
# Pre-bootstrap (once)
export VAULT_ADDR=http://vault.internal:8200
export TF_VAR_role_id=<role-id>
export TF_VAR_secret_id=<secret-id>

# Seed Vault from vault_seed.env
python3 IaC/vault_preflight.py
python3 IaC/vault_preflight.py --check-only    # verify without writing
python3 IaC/vault_preflight.py --shred         # secure-delete vault_seed.env after seeding

# Bootstrap layers 1-4
bash IaC/bootstrap.sh
bash IaC/bootstrap.sh --start-layer 3         # resume from layer 3
bash IaC/bootstrap.sh --dry-run               # plan only

# Ansible — run one host/tag
ansible-playbook -i IaC/Ansible/inventory/services.yml \
  IaC/Ansible/playbooks/deploy_all.yml \
  --tags <tag> --limit <host>

# Ansible — check mode
ansible-playbook ... --check --diff

# Terraform — one env
cd IaC/Terraform/envs/<env>
terraform init && terraform plan
terraform apply -auto-approve

# IaC API service
sudo systemctl status iac-api
sudo journalctl -u iac-api -f

# Vault — curl only (no CLI on this machine)
source /opt/vault/.env
# Check status:
curl -sf $VAULT_ADDR/v1/sys/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('sealed:', d.get('sealed'))"
# Read (show keys only):
curl -sf -H "X-Vault-Token: $VAULT_TOKEN" $VAULT_ADDR/v1/secret/data/homelab/ssh | \
  python3 -c "import sys,json; print(list(json.load(sys.stdin)['data']['data'].keys()))"
# Write:
curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
  $VAULT_ADDR/v1/secret/data/homelab/ssh \
  -d "{\"data\": {\"iac_public_key\": \"$(cat ~/.ssh/id_iac.pub)\"}}"
```

## Secrets & Vault

> **CRITICAL: There is no `vault` CLI on this machine.** Vault runs as a Docker container.
> **All Vault access MUST use curl against the HTTP API.** Never attempt `vault kv get` or similar CLI commands.

- All secrets in Vault KV v2 (`secret/` mount). Never in git.
- Auth: Token — `VAULT_TOKEN` from `/opt/vault/.env`. Ansible/Terraform also use AppRole.
- Vault container on IaC VM. `VAULT_ADDR` from `/opt/vault/.env` (typically `http://127.0.0.1:8200`).
- Seed file: `IaC/secrets/vault_seed.env` (gitignored). Template: `vault_seed.env.example`.
- After seeding, run `--shred` to delete the plaintext file.

### Vault curl patterns (only supported access method)

```bash
# Always source credentials first (never paste token values inline):
source /opt/vault/.env

# Read a secret (KV v2 — data is nested under 'data.data'):
curl -sf -H "X-Vault-Token: $VAULT_TOKEN" \
  "$VAULT_ADDR/v1/secret/data/<path>" | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['data']['data']; print(list(d.keys()))"
  # ^ show only key names, never values

# Write / update a secret:
curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" \
  -H "Content-Type: application/json" \
  "$VAULT_ADDR/v1/secret/data/<path>" \
  -d '{"data": {"key1": "value1", "key2": "value2"}}'

# List secrets at a path:
curl -sf -X LIST -H "X-Vault-Token: $VAULT_TOKEN" \
  "$VAULT_ADDR/v1/secret/metadata/<path>" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['data']['keys'])"

# Extract a single field (for scripting — never echo the value):
FIELD=$(curl -sf -H "X-Vault-Token: $VAULT_TOKEN" \
  "$VAULT_ADDR/v1/secret/data/<path>" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['data']['data']['<key>'])")
```

## Conventions
- **Inventory is the source of truth** — edit `services.yml`, not playbooks directly.
- **Every PR fills `.forgejo/PULL_REQUEST_TEMPLATE.md`** (what changed, what ran, known risks, what needs
  a human look) and must have the `checks` job green. Run `scripts/ci/check.sh` locally before opening
  one; it is the same script CI runs (runner on code-01, role `forgejo_runner`).
- `proxmox_node: pve-01` for ALL VMs on pve-01 (pve-01 is the Proxmox node name).
- `proxmox_node: pve-02` for ALL VMs on pve-02 (pve-02 is the Proxmox node name).
- `proxmox_node: pve-03` for ALL VMs on pve-03 (pve-03 is the Proxmox node name).
- `vault_addr: http://vault.internal:8200` in all Terraform envs and Ansible group_vars.
- SSH keys: `~/.ssh/id_ansible` (Ansible control), `~/.ssh/id_iac` (operator).
- Cloud-init templates (9000=Debian12, 9001=Ubuntu22, 9002=Ubuntu24): now on all
  three nodes as of 2026-08-25 — pve-01 (built), pve-03 (copied from pve-01 via the
  cross-connect), pve-02 (copied from pve-01 via the control host as relay; pve-02's
  pre-existing 9002 is a *different* OVMF/q35 build for gpu-02's passthrough needs,
  left untouched — its 9000/9001 are the same general-purpose BIOS/scsi0 shape as
  pve-01/pve-03). See Terraform/CLAUDE.md for details.
- Terraform `role_id`/`secret_id` passed via env: `TF_VAR_role_id`, `TF_VAR_secret_id`.

## Proxmox Access — API only, no CLI
Proxmox is accessed **exclusively via its REST API**. There is no working SSH login to the Proxmox host itself for automation purposes.

- **API URL**: `https://10.0.0.11:8006/api2/json` (TLS, self-signed — always pass `-k` with curl)
- **Auth**: API token — `PROXMOX_TOKEN_ID` + `PROXMOX_TOKEN_SECRET` from `/opt/vault/.env`
- **Token header format**: `Authorization: PVEAPIToken=<token_id>=<token_secret>`
  - e.g. `terraform@pve!terraform-token=e4b8da72-...`
- **Vault path**: `secret/proxmox/api/pve-01` → keys: `api_url`, `token_id`, `token_secret`

```bash
# Example: list VMs via API
source /opt/vault/.env
curl -sk -H "Authorization: PVEAPIToken=${PROXMOX_TOKEN_ID}=${PROXMOX_TOKEN_SECRET}" \
  https://10.0.0.11:8006/api2/json/nodes/pve-01/qemu | jq '.data[].name'

# Example: list storage pools
curl -sk -H "Authorization: PVEAPIToken=${PROXMOX_TOKEN_ID}=${PROXMOX_TOKEN_SECRET}" \
  https://10.0.0.11:8006/api2/json/nodes/pve-01/storage | jq '.data[].storage'
```

**SSH note**: The `terraform` user SSH key (`id_iac.pub`) is NOT authorized on Proxmox — the bpg Terraform provider works around this via `user_account` in the `initialization` block instead (no SSH needed) for VM provisioning. **Correction 2026-08-10**: `root@10.0.0.11` (and the other PVE hosts) via `~/.ssh/id_ansible` DOES work (confirmed live) — inventory (`proxmox_hosts` group in services.yml) already has `ansible_user: root` + `id_ansible` configured for this. The `proxmox_host` Ansible role's SSH-dependent tasks (kernel tweaks, pvesm-over-SSH, ZFS dataset creation, etc.) are therefore reachable — they just also happen to be unverified/untested against real hardware, and several (`pve_storage_definitions`, `pve_zfs_pools`) reference fictional pool/path names that don't match any real host (confirmed real pools on pve-01: `SSDs`, `zfs-nvme`, plus per-purpose zvols `elasticsearch`/`postgres`/`general` — not `zfs-nvme/zfs-ssd/zfs-sata` as host_vars claims). Verify against live state before trusting that data. Prefer the Proxmox REST API (below) for anything new — it's the path actually exercised and known to work end-to-end (see `Roles/proxmox_host/tasks/nas_backup_storage.yml`).

## Active Constraints
- `pve-01` is pve-01's Proxmox node name — do NOT rename.
- `pve-02` is pve-02's Proxmox node name — do NOT rename.
- `pve-03` is pve-03's Proxmox node name — do NOT rename. (Physical install in
  progress 2026-08-23; set the Proxmox installer hostname to "pve-03" and give it
  10.0.0.13 on the flat/client VLAN, matching pve-01/pve-02 — NOT 10.0.10.13, the
  VLAN10 MGMT migration hasn't happened for any PVE host yet.)
- `192.168.x.x` is NOT used — all hosts on `10.0.x.x`.
- Proxmox API URL for pve-01: `https://10.0.0.11:8006`.
- Proxmox API URL for pve-02: `https://10.0.0.12:8006`.
- Proxmox API URL for pve-03: `https://10.0.0.13:8006` (once installed).
- **pve-01<->pve-03 10G direct cross-connect** (2026-08-23): `172.31.99.1` (pve-01,
  `enp3s0f1`) <-> `172.31.99.2` (pve-03, `nic1`), MTU 9000, deliberately outside
  the `10.0.x.0/24` VLAN scheme. Verified 9.89 Gbits/sec (iperf3). Use this
  link's IPs (not the management IPs) for bulk VM disk transfers between
  pve-01 and pve-03 — no Proxmox cluster exists, so this is a fast rsync/scp
  path, not live migration. Config is hand-staged in `/etc/network/interfaces`
  on both hosts, not yet Ansible-managed — see `host_vars/pve-01.yml` and
  `host_vars/pve-03.yml` for the full writeup.
- VLAN10 migration pending: IaC VM (10.0.0.15→10.0.10.15) and pve-01 (10.0.0.11→10.0.10.11). When done: update `pve_mgmt_ip` in host_vars/pve-01.yml and Pi-hole records only.

## Pi-hole v6 Operations (dns-01 — 10.0.30.10)

Pi-hole v6 is deployed as a Docker container on dns-01 (VLAN30). **dns-01 is the ONLY
DNS server for every VLAN + the VPN** — anything that restarts the container drops DNS
network-wide.

> **Corrected 2026-10-03.** This section used to say the REST API can't add hosts and that
> `pihole-FTL --config dns.hosts` + a container restart was the only way. That's wrong (and
> the restart is exactly what to avoid). The per-element REST endpoint below adds a record
> live, with no restart, and it persists: verified for `travel.home.example.org` — present in
> `/etc/pihole/pihole.toml` on disk, so it survives restarts. See `project_pihole_dns_status`
> memory (first confirmed 2026-09-21 adding `plex.home.example.org`).

### Authentication
Vault path: `secret/pihole/api` → keys: `webpassword`, `api_token`

```bash
source /opt/vault/.env
PIHOLE_PASS=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  "$VAULT_ADDR/v1/secret/data/pihole/api" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['data']['webpassword'])")

SID=$(curl -s -X POST 'http://10.0.30.10/api/auth' \
  -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PIHOLE_PASS\"}" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['session']['sid'])")
# ... use $SID ..., then log the session out (max_sessions is limited):
curl -s -X DELETE 'http://10.0.30.10/api/auth' -H "X-FTL-SID: $SID"
```

**Do NOT use `$PIHOLE` from `/opt/vault/.env`** — that is the old decommissioned Pi-hole (10.0.0.7).
The current Pi-hole is at 10.0.30.10 and uses `secret/pihole/api → webpassword`.

### Read current custom DNS hosts
```bash
curl -s "http://10.0.30.10/api/config/dns/hosts" -H "X-FTL-SID: $SID" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(h) for h in d['config']['dns']['hosts']]"
```

### Add one custom DNS host — REST, live, no restart (preferred)
`PUT` to the **per-element** path with the URL-encoded `"IP hostname"` string. Returns 201.
Check the record isn't already there first (read above).
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X PUT -H "X-FTL-SID: $SID" \
  "http://10.0.30.10/api/config/dns/hosts/$(python3 -c 'import urllib.parse;print(urllib.parse.quote("10.0.30.20 new-service.home.example.org"))')"
getent hosts new-service.home.example.org   # verify
```
Remove one: same path with `-X DELETE`. (`POST /api/config/dns/hosts` without the element is a
404 — that's where the old "REST can't add hosts" belief came from.)

### Do NOT
- **Run `Roles/pihole_dns` (or `pihole-FTL --config dns.hosts '[...]'`) to add a record.** Both
  REPLACE the entire list. The role writes `pihole_dns_records` from `services.yml` (stale — 19
  entries) over the live list (85 as of 2026-10-03), and the role restarts the container.
- Use the CLI + restart method at all unless the REST API is down — it blacks out DNS for
  every VLAN during the restart.

After any change, refresh the repo snapshot (read-only, no secrets in output):
`python3 scripts/pihole_export.py` → `pihole/pihole-dns-snapshot.yml`. Live Pi-hole is the source
of truth; the snapshot is a sanitized copy.

## Traefik on web-01 — Docker service integration pattern

> **Docker provider is DISABLED.** All routes are file-based. Docker labels on containers are IGNORED.
> Every new service requires an explicit YAML file in `/etc/traefik/dynamic/` on web-01.

### How to expose a new Docker service via Traefik

1. **Add the container to the `traefik` external network** in docker-compose:
   ```yaml
   networks:
     - traefik
   # at the bottom:
   networks:
     traefik:
       external: true
   ```
   No `ports:` binding needed — Traefik routes via the Docker network using the container hostname.

2. **Register the route** via the `traefik_register` Ansible role (preferred), or write the file directly:
   ```bash
   # Via Ansible (add to deploy_traefik.yml post_tasks or the service's own role):
   - include_role: { name: traefik_register }
     vars:
       traefik_service_name: "my-service"
       traefik_backend_url:  "http://my-container:PORT"
       traefik_domain:       "myservice.home.example.org"
       traefik_auth:         "one_factor"   # or "two_factor" or "bypass"
       traefik_healthcheck_path: "/health"
   ```

3. **Backend URL**: use the Docker container name as hostname (e.g., `http://podcast-api:8001`).
   Traefik resolves it via the shared `traefik` Docker network.

4. **No healthcheck with `curl` in minimal images** (e.g., qdrant, distroless containers).
   Use `service_started` instead of `service_healthy` in `depends_on`, or use `wget` if available.

5. **Route file location on web-01**: `/etc/traefik/dynamic/<service>.yml` (owned by root).
   Traefik hot-reloads this directory — no restart needed after adding/changing files.

6. **IaC tracking**: cross-VM routes go in `deploy_traefik.yml` post_tasks; service-local routes
   go in the service's own Ansible role (see `Roles/n8n/tasks/main.yml` as an example).

## Sub-context files (load these instead of scanning docs/)
| Task | Read this file |
|---|---|
| Ansible plays, roles, inventory | `IaC/Ansible/CLAUDE.md` |
| Terraform envs, modules, state | `IaC/Terraform/CLAUDE.md` |
| OPNSense firewall, NordLynx | `IaC/Ansible/Roles/opnsense/CLAUDE.md` |
| Bootstrap layer-by-layer debug | `IaC/bootstrap.sh` lines 1-50 (header) only |
| Architecture deep-dive | `IaC/docs/ARCHITECTURE_INDEX.md` |
| Vault secret map | `IaC/vault_preflight.py` `SECRET_MAP` dict |

---

## Claude Code + VS Code Remote — Setup & Workflow

### Architecture
```
MacBook Pro
  └── VS Code (local) + Remote SSH extension
       └── SSH → IaC VM (10.0.0.15 / iac.internal)
            ├── VS Code Server (auto-installed on first connect)
            ├── File explorer   = IaC VM filesystem
            ├── Terminal pane   = shell on the IaC VM
            └── claude (Claude Code CLI)  ← runs here, not on the Mac
                 └── reads CLAUDE.md hierarchy from cwd upward
```

Claude Code executes **entirely on the IaC VM**. The Mac is just the display.
Your Claude Pro subscription authenticates once on the IaC VM via `claude login`.

---

### One-time setup — Mac (VS Code)

1. Install the **Remote - SSH** extension (`ms-vscode-remote.remote-ssh`)
2. Add SSH config entry on the Mac:
   ```
   # ~/.ssh/config
   Host iac
     HostName 10.0.0.15          # or iac.internal once dns-01 is up
     User ansible                # or your admin user
     IdentityFile ~/.ssh/id_iac  # private key — public key must be in authorized_keys on IaC VM
     ForwardAgent yes            # allows IaC VM to use your Mac's SSH agent for git ops
   ```
3. VS Code → Remote Explorer → SSH → `iac` → Connect

---

### One-time setup — IaC VM (run once after first SSH)

```bash
# 1. Install Claude Code CLI (Node.js required)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
npm install -g @anthropic-ai/claude-code

# 2. Authenticate with Claude Pro
claude login
# Opens browser URL — complete OAuth on your Mac browser.
# Credentials are stored in ~/.claude/ on the IaC VM.

# 3. Verify
claude --version
claude "what files are in IaC/?" --print   # non-interactive test
```

---

### Daily workflow

```bash
# On IaC VM terminal (via VS Code Remote SSH terminal pane):

# Change to repo root or a specific sub-layer
cd ~/repo/IaC                       # loads IaC/CLAUDE.md
cd ~/repo/IaC/Ansible               # loads IaC/CLAUDE.md + IaC/Ansible/CLAUDE.md
cd ~/repo/IaC/Ansible/Roles/opnsense # loads all three CLAUDE.md files up the tree

# Start Claude Code interactive session
claude

# Or: one-shot task (token-efficient for targeted work)
claude "deploy dns-01 and verify Pi-hole is responding"
claude "check why the NordLynx tunnel is not establishing — read only nordlynx.yml and opnsense CLAUDE.md"
claude "resume bootstrap from layer 3 — pg-01 failed, show me the error"
```

---

### Token-efficiency rules when prompting Claude Code

1. **Always `cd` into the sub-layer before starting.** Claude Code loads CLAUDE.md files from cwd upward — starting in `IaC/Ansible/` loads only Ansible context, not Terraform or opnsense details.

2. **Name the exact files you want read** in your first prompt when debugging a specific layer:
   ```
   "Read bootstrap.sh layers 3-4 and IaC/Ansible/CLAUDE.md only. pg-01 Terraform apply failed with..."
   ```

3. **Never ask broad questions** like "explain the whole repo" — that triggers a full scan. Instead ask scoped questions:
   ```
   # Bad  → "what does this repo do?"
   # Good → "what does Layer 3 of bootstrap.sh deploy and which Terraform env does it use?"
   ```

4. **Use `--print` for read-only queries** (no interactive session, no tool calls, cheapest):
   ```bash
   claude --print "what is the Vault path for the OPNSense API credentials?"
   ```

5. **Reference sub-context files by name** to pre-scope Claude's attention:
   ```
   "Using IaC/Ansible/Roles/opnsense/CLAUDE.md as context, add a firewall rule to VLAN30..."
   ```

---

### Context loading order (how CLAUDE.md hierarchy works)

Claude Code walks **from cwd upward to the git root** and reads every `CLAUDE.md` it finds:

| cwd | Files loaded |
|---|---|
| `~/repo` | `IaC/CLAUDE.md` (none at root level) |
| `~/repo/IaC` | `IaC/CLAUDE.md` |
| `~/repo/IaC/Ansible` | `IaC/CLAUDE.md` + `IaC/Ansible/CLAUDE.md` |
| `~/repo/IaC/Ansible/Roles/opnsense` | `IaC/CLAUDE.md` + `IaC/Ansible/CLAUDE.md` + `IaC/Ansible/Roles/opnsense/CLAUDE.md` |
| `~/repo/IaC/Terraform/envs/pg-01` | `IaC/CLAUDE.md` + `IaC/Terraform/CLAUDE.md` |

Total context loaded at the deepest path ≈ 3 focused files (~15 KB) instead of scanning 1.4 MB of docs.

---

### `.claude/settings.json` (already committed at repo root)
The `.claude/settings.json` file at the repo root tells Claude Code to **never index**:
- `IaC/docs/Iac_Chat*.md` (843 KB of conversation archives)
- `docs/`, `wger/`, `extras/`, `logs/`, `IaC/Grafana/`, `IaC/n8n/`, `IaC/netbox/`

This applies automatically to every session — no manual flag needed.
