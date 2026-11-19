# What's in this repository

A map of the folders. Back to the [README](../../README.md).

This is the public, sanitized edition of a homelab that runs every day on three Proxmox hosts and
about 30 VMs. Names, domains and credentials have been replaced (`home.example.org`, `owner`,
`Partner`, `Child1`, `Child2`); the structure, code and decisions are the real ones.

| Folder | What's in it |
| --- | --- |
| [`services/`](../../services) | Service particles (`service.yaml`) and the [templates](../../services/_templates) to write your own |
| [`terraform/`](../../terraform) | Proxmox VM modules (`bpg/proxmox`) and one [example env](../../terraform/envs/example-vm): one env per VM |
| [`ansible/`](../../ansible) | Roles for the platform layer (base, Docker, Traefik, Authelia, PostgreSQL, monitoring, restic/Backrest, Proxmox hosts) and the inventory that drives them |
| [`integration/`](../../integration) | The code partner and its policy and tests; the MCP audit log and guardrails service; the showcase: a music studio suite (catalog, practice drills, timing-drift checks, field-recording ingest, a studio partner) and the DOMO conversation agent for Home Assistant, with tests |
| [`n8n/workflows/`](../../n8n/workflows) | The live n8n workflows, exported with secrets redacted |
| [`vault/`](../../vault) | Who may read which secret: lanes and the generated Vault policies |
| [`docs/`](../../docs) | Philosophy, how-to guides, runbooks, agent design docs, [decision records](../decisions) and [incident write-ups](../incidents) |
| `vault_helper.py`, [`scripts/`](../../scripts) | Vault access helpers, the patch window and Homepage sync |

The private repository behind this one holds more (about 70 service particles, the voice
assistant, research agents, incident write-ups). Parts of it will be published here as they are
cleaned up.
