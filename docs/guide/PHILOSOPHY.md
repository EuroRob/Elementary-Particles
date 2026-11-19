# The philosophy

The ideas behind the structure. Back to the [README](../../README.md).

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
| **One source of truth** | Hosts, IPs and settings live in one inventory ([`ansible/inventory/services.yml`](../../ansible/inventory/services.yml)). Secrets live only in Vault, never in git. |
| **Everything stays in the house** | Data on your own disks, models on your own GPUs: two NVIDIA cards (RTX 4060 Ti, RTX 2070) for Ollama, Whisper and Piper, and two Intel iGPUs passed through to VMs for media transcoding and the camera NVR. Voice never goes through a cloud API. |
| **Layered and re-runnable** | A numbered build order: network → secrets → storage and database → reverse proxy → applications. Every layer can be run again without disturbing what is already up. |
| **AI suggests, checks code, people decide** | Models draft, summarize and converse; every suggestion passes checks written in code. Facts that must be right (chords, bar counts, "was it saved?") are checked by code, and changes to real things stay with a person. |

Longer versions: [solution overview](../SOLUTION_OVERVIEW.md) ·
[VMs as particles](../VM_ELEMENTARY_PARTICLES.md) ·
[relationship model](../ELEMENTARY_PARTICLES_ERM.md) ·
[how to add a service](../HOW_TO_ADD_SERVICE.md) · [tag reference](../TAG_REFERENCE.md)
