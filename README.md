# Elementary Particles

Infrastructure as code for a self-hosted family cloud and household assistant: photos, files,
calendars, media, music, home automation and a local voice assistant, all on hardware you own,
with no cloud service needed for daily use.

This is the public, sanitized edition of a homelab that runs every day on three Proxmox hosts and
about 30 VMs. Names, domains and credentials have been replaced (`home.example.org`, `owner`,
`Partner`, `Child1`, `Child2`); the structure, code and decisions are the real ones.

## Agents as infrastructure

Two AI agents work on this repository, and both are treated as infrastructure with a written
access model rather than as a chat window:

| | Claude Code (cloud) | Code partner (on premise) |
| --- | --- | --- |
| **Does** | Builds the infrastructure: Terraform, Ansible, docs, scripts. Co-author of 392 of the 847 commits on the main branch (69 % since it started in July 2026). | Turns a spoken or typed request into a diff on its own branch and a draft pull request. Runs on a local model. |
| **May never touch** | Secrets outside its Vault lane; see [agent access lanes](docs/AGENT_ACCESS_LANES.md), [`vault/lanes.yml`](vault/lanes.yml) and the generated [policies](vault/policies). | `.env`, state files, keys, its own code and policy, CI, `CLAUDE.md`. No tool to apply, commit, push or run anything; no network beyond three hosts. See [code partner](docs/CODE_PARTNER.md) and [`code_partner_policy.yml`](integration/code_partner_policy.yml). |
| **A person approves** | Every change lands as a pull request on a protected main branch. A `checks` job ([`.forgejo/workflows/ci.yml`](.forgejo/workflows/ci.yml) running [`scripts/ci/check.sh`](scripts/ci/check.sh): YAML, Python, shell, `terraform fmt`, Ansible syntax, the offline tests, gitleaks) must be green before a person reviews and merges. | Apply, push and "open the pull request" are fixed phrases or buttons a person uses; merging is not in the code at all. |

The rule behind both: **AI suggests, code checks, people decide.** Every guardrail that matters
is enforced in code (path deny-lists, exactly-once edit matching, re-parsing, a secret scan on
every proposed line), not by asking the model nicely. The instructions Claude Code works from
are the root [`CLAUDE.md`](CLAUDE.md). What the agents produced, and how often it had to be
fixed, is measured from git history in [agent metrics](docs/AGENT_METRICS.md)
(`scripts/agent_metrics.py` recomputes it on any clone).

n8n is the orchestration hub: 25 live workflows are exported to git with secrets redacted
([`n8n/workflows/`](n8n/workflows), [operations](docs/N8N_OPERATIONS.md)), including the
NetBox → n8n → Ansible patch pipeline with a backup gate and rollback, and a watchdog that checks
the database path n8n's own `/healthz` does not.

## The philosophy

**Everything is an elementary particle.** Each piece of a household's digital life is a small,
self-contained unit with one job, a clear identity, its own data and explicit connections to the
rest. Particles compose into larger systems: a VM hosts services, services expose interfaces,
interfaces make up what the family actually uses. Nothing is a one-off that only works because
someone remembers how it was set up.

| Rule | What it means here |
| --- | --- |
| **One particle, one job** | A service is described by its own manifest (`service.yaml`): identity, tags, dependencies, interfaces, secrets. A VM is a particle too, with its own Terraform env and inventory entry. |
| **Tags are the control plane** | Whether a service is exposed, how it authenticates, its DNS name and its backups come from its tags. Traefik, Authelia, Pi-hole, NetBox and Homepage follow them. |
| **One source of truth** | Hosts, IPs and settings live in one inventory ([`ansible/inventory/services.yml`](ansible/inventory/services.yml)). Secrets live only in Vault, never in git. |
| **Everything stays in the house** | Data on your own disks, models on your own GPUs: two NVIDIA cards (RTX 4060 Ti, RTX 2070) for Ollama, Whisper and Piper, and two Intel iGPUs passed through to VMs for media transcoding and the camera NVR. Voice never goes through a cloud API. |
| **Layered and re-runnable** | A numbered build order: network → secrets → storage and database → reverse proxy → applications. Every layer can be run again without disturbing what is already up. |
| **AI suggests, code checks, people decide** | Models draft, summarise and converse; every suggestion passes checks written in code. Facts that must be right (chords, bar counts, "was it saved?") are checked by code, and changes to real things stay with a person. |

Longer versions: [solution overview](docs/SOLUTION_OVERVIEW.md) ·
[VMs as particles](docs/VM_ELEMENTARY_PARTICLES.md) ·
[relationship model](docs/ELEMENTARY_PARTICLES_ERM.md) ·
[how to add a service](docs/HOW_TO_ADD_SERVICE.md) · [tag reference](docs/TAG_REFERENCE.md)

## What's in this repository

| Folder | What's in it |
| --- | --- |
| [`services/`](services) | Service particles (`service.yaml`) and the [templates](services/_templates) to write your own |
| [`terraform/`](terraform) | Proxmox VM modules (`bpg/proxmox`) and one [example env](terraform/envs/example-vm): one env per VM |
| [`ansible/`](ansible) | Roles for the platform layer (base, Docker, Traefik, Authelia, PostgreSQL, monitoring, restic/Backrest, Proxmox hosts) and the inventory that drives them |
| [`integration/`](integration) | The code partner and its policy and tests; the MCP audit log and guardrails service; the showcase: a music studio suite (catalogue, practice drills, timing-drift checks, field-recording ingest, a studio partner) and the DOMO conversation agent for Home Assistant, with tests |
| [`n8n/workflows/`](n8n/workflows) | The live n8n workflows, exported with secrets redacted |
| [`vault/`](vault) | Who may read which secret: lanes and the generated Vault policies |
| [`docs/`](docs) | Philosophy, how-to guides, runbooks, agent design docs, [decision records](docs/decisions) and [incident write-ups](docs/incidents) |
| `vault_helper.py`, [`scripts/`](scripts) | Vault access helpers, the patch window and Homepage sync |

The private repository behind this one holds more (about 70 service particles, the voice
assistant, research agents, incident write-ups). Parts of it will be published here as they are
cleaned up.

## Getting started

You will need Proxmox VE, Terraform ≥ 1.6, Ansible ≥ 2.15, a HashiCorp Vault (KV v2) and Python 3.11.

1. Read [how to add a service](docs/HOW_TO_ADD_SERVICE.md) and copy
   [`services/_templates/service.example.yaml`](services/_templates/service.example.yaml).
2. Create a VM: copy [`terraform/envs/example-vm`](terraform/envs/example-vm), fill in
   `terraform.tfvars` from the example, then `terraform init && terraform apply`.
3. Put the host in [`ansible/inventory/services.yml`](ansible/inventory/services.yml) and run the
   playbooks in [`ansible/playbooks/`](ansible/playbooks).

Expect to adapt things: this is a working homelab shared as a reference, not a turnkey product.

## License

Copyright © 2026 Rob Carroll.
Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only).
If you run a modified version as a network service, you must offer its source to its users.

For a commercial license, consulting or help adapting this to your own setup, open an issue or
see [NOTICE.md](NOTICE.md).

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
