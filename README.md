<p align="center">
  <img src="assets/banner.jpg" alt="Elementary Particles" width="900">
</p>

# Elementary Particles

Infrastructure as code for a self-hosted family cloud and household assistant: photos, files,
calendars, media, music, podcasts, home automation and a local voice assistant, all on hardware you own,
with no cloud service needed for daily use.

This is the public, sanitized edition of a homelab that runs every day on three Proxmox hosts and
about 30 VMs. Names, domains and credentials have been replaced (`home.example.org`, `owner`,
`Partner`, `Child1`, `Child2`); the structure, code and decisions are the real ones.

## Why this exists

I run a homelab because it is fun, especially wiring several open source projects into one
system and creating my own custom ones. Keeping that system healthy is the part that is not fun. Every project has its own
updates, its own disk that fills up at the wrong moment, and its own way of failing quietly. I
have a full-time job and a family, as well as other hobbies, so I cannot watch it all the time. It has to keep working when
I am not looking.

Two things shaped how this is built. AI keeps getting better at finding vulnerabilities and attack
surfaces, so a neglected service is a bigger risk every month, and patching has to be routine and
not heroic. And as the system grew, I needed to see trouble coming, like a container running out of
space and taking something down.

The other reason is the people who use it. A feature that makes my wife's day easier and less
stressful is worth more than a clever dashboard, and it is the reason I keep going late at night.
It also gets my oldest ready for school: the time, the weather forecast and whether he should wear
a jacket, even when he does not want to.

Beyond reliability, the goal is an AI concierge with individualized agents for the household. It curates my photos, my music recordings, automates the
house, watches for bad weather, helps with workouts, cooking recipes and warns me when it predicts a problem. It boils the news down to what is useful, keeps me company with music or podcasts and helps plan trips, even looks for exciting new job opportunities and the latest tech trends. It is built to grow one small piece at a time and there is more in the pipeline.

I build this with Claude Code and a local code partner. I decide what gets built, and I review and
merge every change. The sections below show how the agents are kept in check.

## The philosophy

**Everything is an elementary particle.** Each piece of a household's digital life is a small,
self-contained unit with one job, a clear identity, its own data and explicit connections to the
rest. Particles compose into larger systems: a VM hosts services, services expose interfaces,
interfaces make up what the family actually uses. Nothing is a one-off that only works because
someone remembers how it was set up.

If you know Kubernetes, the ideas will look familiar: a declarative manifest per service, labels
that drive behavior, and layers you can run again to converge on the described state. The
difference is scale. There is no cluster and no scheduler. It is Terraform, Ansible and a few VMs
on hardware you own, aimed at a household and not a datacenter.

| Rule | What it means here |
| --- | --- |
| **One particle, one job** | A service is described by its own manifest (`service.yaml`): identity, tags, dependencies, interfaces, secrets. A VM is a particle too, with its own Terraform env and inventory entry. |
| **Tags are the control plane** | Whether a service is exposed, how it authenticates, its DNS name and its backups come from its tags. Traefik, Authelia, Pi-hole, NetBox and Homepage follow them. |
| **One source of truth** | Hosts, IPs and settings live in one inventory ([`ansible/inventory/services.yml`](ansible/inventory/services.yml)). Secrets live only in Vault, never in git. |
| **Everything stays in the house** | Data on your own disks, models on your own GPUs: two NVIDIA cards (RTX 4060 Ti, RTX 2070) for Ollama, Whisper and Piper, and two Intel iGPUs passed through to VMs for media transcoding and the camera NVR. Voice never goes through a cloud API. |
| **Layered and re-runnable** | A numbered build order: network → secrets → storage and database → reverse proxy → applications. Every layer can be run again without disturbing what is already up. |
| **AI suggests, checks code, people decide** | Models draft, summarize and converse; every suggestion passes checks written in code. Facts that must be right (chords, bar counts, "was it saved?") are checked by code, and changes to real things stay with a person. |

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
| [`integration/`](integration) | The code partner and its policy and tests; the MCP audit log and guardrails service; the showcase: a music studio suite (catalog, practice drills, timing-drift checks, field-recording ingest, a studio partner) and the DOMO conversation agent for Home Assistant, with tests |
| [`n8n/workflows/`](n8n/workflows) | The live n8n workflows, exported with secrets redacted |
| [`vault/`](vault) | Who may read which secret: lanes and the generated Vault policies |
| [`docs/`](docs) | Philosophy, how-to guides, runbooks, agent design docs, [decision records](docs/decisions) and [incident write-ups](docs/incidents) |
| `vault_helper.py`, [`scripts/`](scripts) | Vault access helpers, the patch window and Homepage sync |

The private repository behind this one holds more (about 70 service particles, the voice
assistant, research agents, incident write-ups). Parts of it will be published here as they are
cleaned up.

## Agents as infrastructure

Two AI agents work on this repository and both are treated as infrastructure with a written
access model rather than as a chat window:

| | Claude Code (cloud) | Code partner (on premise) |
| --- | --- | --- |
| **Does** | Builds the infrastructure: Terraform, Ansible, docs, scripts. Co-author of 435 of the 919 commits on the main branch (68 % since it started in July 2026). | Turns a spoken or typed request into a diff on its own branch and a draft pull request. Runs on a local model. |
| **May never touch** | Secrets outside its Vault lane; see [agent access lanes](docs/AGENT_ACCESS_LANES.md), [`vault/lanes.yml`](vault/lanes.yml) and the generated [policies](vault/policies). | `.env`, state files, keys, its own code and policy, CI, `CLAUDE.md`. No tool to apply, commit, push or run anything; no network beyond three hosts. See [code partner](docs/CODE_PARTNER.md) and [`code_partner_policy.yml`](integration/code_partner_policy.yml). |
| **A person approves** | Every change lands as a pull request on a protected main branch. A `checks` job ([`.forgejo/workflows/ci.yml`](.forgejo/workflows/ci.yml) running [`scripts/ci/check.sh`](scripts/ci/check.sh): YAML, Python, shell, `terraform fmt`, Ansible syntax, the offline tests, gitleaks) must be green before a person reviews and merges. | Apply, push and "open the pull request" are fixed phrases or buttons a person uses; merging is not in the code at all. |

### Keeping pull requests honest

Three small pieces, on two machines, so that a person only reviews work a machine has already checked:

| Piece | Where it runs | What it does | What it can touch |
| --- | --- | --- | --- |
| **`checks`** ([`ci.yml`](.forgejo/workflows/ci.yml)) | Forgejo Actions runner on the repo-lane VM | Runs [`scripts/ci/check.sh`](scripts/ci/check.sh) on every pull request. The gate itself (`scripts/ci/`, `.gitleaks.toml`) is taken from the **base branch**, so a pull request cannot weaken the checks that judge it; a change to the gate is flagged on the PR. Required to merge. | A per-job token. No stored credential, network pinned to the git host. |
| **`drift`** ([`drift.yml`](.forgejo/workflows/drift.yml), [`drift.py`](scripts/ci/drift.py)) | Same runner | After every merge and every six hours, merges each open pull request onto the *current* main branch and re-runs the checks. Sets a `checks-drift` status and comments only when the verdict changes. Advisory, deliberately not a required check. | The same per-job token. |
| **PR watch** ([n8n workflow](n8n/workflows)) | n8n, a different host | Hourly: lists open pull requests and posts to the alerts room when one is older than 24 hours, has no or failing checks, fails against the current main branch, or is a draft. | A read-only bot account. It cannot write to the repository. |

The runner can run code but holds no secrets; the watcher holds a credential but can only read. Neither is a model: the checks are deterministic, and a person reads and merges. The agent that writes the code is separate and described above.

The rule behind both: **AI suggests, checks code, people decide.** Every guardrail that matters
is enforced in code (path deny-lists, exactly-once edit matching, re-parsing, a secret scan on
every proposed line), not by asking the model nicely. The instructions Claude Code works from
are the root [`CLAUDE.md`](CLAUDE.md). What the agents produced, and how often it had to be
fixed, is measured from git history in [agent metrics](docs/AGENT_METRICS.md)
(`scripts/agent_metrics.py` recomputes it on any clone).

n8n is the orchestration hub: 20 live workflows are exported to git with secrets redacted
([`n8n/workflows/`](n8n/workflows), [operations](docs/N8N_OPERATIONS.md)), including the
NetBox → n8n → Ansible patch pipeline with a backup gate and rollback, and a watchdog that checks
the database path n8n's own `/healthz` does not.


## Getting started

You will need Proxmox VE, Terraform ≥ 1.6, Ansible ≥ 2.15, a HashiCorp Vault (KV v2) and Python 3.11.

1. Read [how to add a service](docs/HOW_TO_ADD_SERVICE.md) and copy
   [`services/_templates/service.example.yaml`](services/_templates/service.example.yaml).
2. Create a VM: copy [`terraform/envs/example-vm`](terraform/envs/example-vm), fill in
   `terraform.tfvars` from the example, then `terraform init && terraform apply`.
3. Put the host in [`ansible/inventory/services.yml`](ansible/inventory/services.yml) and run the
   playbooks in [`ansible/playbooks/`](ansible/playbooks).

Expect to adapt things: this is a working homelab shared as a reference, not a turnkey product.


## Known gaps

This is a homelab that is hardened in the open, not a finished product. Open items you will notice:

- **n8n webhooks have no authentication.** About ten webhook nodes in [`n8n/workflows/`](n8n/workflows)
  accept unauthenticated requests. They are reachable from the home network and the VPN only, never from
  the internet. Header authentication for them is planned.
- **Some services also publish their port directly** on the VM address, next to the authenticated route
  through the reverse proxy. Per-port firewall rules are planned.
- **Agent sessions and the human share one Git identity today.** The rule that agents never push to the
  main branch is written down and followed; giving agents their own account so the host can enforce it
  is planned.
- **GitHub Actions only runs the CLA check.** The pipeline described above is a Forgejo Actions workflow
  (`.forgejo/`); it does not run on GitHub.

## License

Copyright © 2026 Rob Carroll.
Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only).
If you run a modified version as a network service, you must offer its source to its users.

**Commercial licensing is available.** If the AGPL does not fit your use, for example you want to build
this into a proprietary product or run it as a closed service, a commercial license can be arranged with
the maintainer. Open an issue titled "Commercial license" (no details needed) and you will be contacted
privately. Consulting and help adapting this to your own setup are available the same way.

The name "Elementary Particles" and the banner image are not licensed under the AGPL. Please do not use them in a way that suggests your fork is the original or is endorsed by the maintainer.

Contributions are welcome and need a signed Contributor License Agreement; see
[CONTRIBUTING.md](CONTRIBUTING.md).
