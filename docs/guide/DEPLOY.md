# Deploying from scratch

Back to the [README](../../README.md). This page is the order of operations for standing the platform up on an empty
Proxmox host: prepare the management machine, seed the secrets, run the preflight, build the templates, then bring up the
layers one at a time. [Using it](USING.md) covers changing a running system; this page covers building one.

**Read this first.** The scripts and the order below are the real ones. This public edition does not yet contain everything the
layers need: it has `scripts/preflight.sh`, `vault_preflight.py`, `bootstrap.sh`, one example Terraform environment and a
subset of the Ansible roles. It does not include the Terraform environments for dns-01, vpn-01, pg-01 and web-01, the
`deploy_all.yml` playbook that `bootstrap.sh` calls, or the template-building playbook. So today you can follow and adapt the procedure, and you
write the missing environments from [`terraform/envs/example-vm`](../../terraform/envs/example-vm) and the
[service template](../../services/_templates/service.example.yaml). Shipping a runnable minimum set is planned.

## What you need

Sizes, hosts and the software list are in [hardware and dependencies](REQUIREMENTS.md). In short:

- One Proxmox VE host with network access and an API token for Terraform.
- A management VM (the "IaC VM") that runs everything below: ansible-core 2.17 or newer, Terraform 1.6 or newer, Python 3.11, `ssh`.
- HashiCorp Vault (KV v2) as a container on that VM, with an AppRole for Terraform and Ansible.
- A network plan: VLANs for management, services, IoT and guests, and a firewall (OPNsense here).

## 1. Prepare the management VM

```bash
ansible-galaxy collection install -r ansible/requirements.yml     # the collections the roles use
export VAULT_ADDR=http://vault.internal:8200
export TF_VAR_role_id=<vault-approle-role-id>
export TF_VAR_secret_id=<vault-approle-secret-id>
```

Never paste real values into a shared terminal or a chat. Keep them in the Vault container's environment file and read them from there.

## 2. Seed Vault

`vault_preflight.py` writes the secrets the layers need into Vault from a seed file, then verifies them.

```bash
python3 vault_preflight.py --check-only     # verify without writing
python3 vault_preflight.py                  # seed from the (gitignored) seed file
python3 vault_preflight.py --shred          # securely delete the plaintext seed file afterwards
```

The seed file is never committed. The rule is that secrets live in Vault only; configuration reads them at run time.

## 3. Run the preflight

```bash
bash scripts/preflight.sh                   # check only; exit 1 means do not bootstrap
bash scripts/preflight.sh --fix             # also build missing cloud-init templates
bash scripts/preflight.sh --dry-run         # show what --fix would do
bash scripts/preflight.sh --start-layer 2   # only check what layer 2 and later need
```

It checks, in order: the Ansible version, the Python requirements, the Ansible collections, the environment variables, DNS
resolution, Vault (initialized and unsealed), the Proxmox host over SSH, the cloud-init templates, the Ansible inventory, and
a Terraform environment for each layer. A failure is a blocker, a warning is not. On the public edition expect failures for the
environments listed above until you add them.

## 4. Build the templates (step 0)

Every VM is cloned from a cloud-init template, so the templates must exist before layer 1. `bootstrap.sh --start-layer 0` runs the
template playbook, and `preflight.sh --fix` can do it too.

## 5. Bring up the layers

```bash
bash bootstrap.sh --dry-run                 # plan only: terraform plan, ansible --check
bash bootstrap.sh                           # layers 1 to 4
bash bootstrap.sh --start-layer 3           # resume after a failure
```

`bootstrap.sh` runs `terraform apply -auto-approve` for each layer's VM. Run the dry run first and read the plans.

| Layer | What | Notes |
|---|---|---|
| **1** | Firewall (manual gate), then DNS (Pi-hole) | The firewall installation is manual. The script pauses until you confirm it is up. DNS is verified before it continues |
| **2** | VPN (WireGuard) | Admin UI on the management VLAN |
| **3** | NAS shares and PostgreSQL with Elasticsearch | The NAS step needs the filesystem UUID from a discovery run first; the script says how to get it |
| **4** | Reverse proxy and platform services: Traefik, n8n, Prometheus, Grafana, NetBox | The script waits for Traefik's health endpoint |
| **5 to 8** | Git host, second application host, GPU host, media host | After layer 4, n8n drives these: import the orchestration workflow, then trigger it with a layer range. Or run the deploy playbook with `--tags layer5,layer6,layer7,layer8` by hand |

### Why this order

Each layer depends on the one before it. The firewall and DNS come first because nothing else can route or resolve names without them.
The VPN follows so you can reach the network remotely while you build. Storage and the database come next, because almost every
application needs one of them. The reverse proxy, automation and monitoring (layer 4) then give every later service a front door,
a way to be deployed and a way to be watched. Applications are last. Sizes for each layer are in [hardware and dependencies](REQUIREMENTS.md).

After each layer, verify the live system: the DNS answers, the service responds through its route, monitoring sees it. A script
that exits 0 is not the same as a service that works.

## 6. After the layers

- Add each new service as a particle: [how to add a service](../HOW_TO_ADD_SERVICE.md).
- Turn on patching and backups, and restore one backup to prove it works.
- Run `scripts/ci/check.sh` before every pull request.

## Known problems with this procedure

- `bootstrap.sh` has no automatic rollback; a half-applied layer is fixed by hand or by resuming from that layer.
- Some host names and IPs are placeholders from the sanitizing step. Replace them in the inventory and the Terraform variables.
- The layer numbers come from the private repository. Notes there call the later layers by slightly different webhook names; check the
  name in the workflow you import.
