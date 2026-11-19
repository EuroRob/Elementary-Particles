# Hardware and dependencies

Back to the [README](../../README.md). How much machine you need, and what else has to be in place, before
[deploying from scratch](DEPLOY.md).

**How to read the numbers.** They come from the Terraform defaults and the inventory in this repository, and from what the live
system runs today. Sizes in the live system are tuned in places (several VMs were cut on 2026-11-06 because one host was short
of memory), so treat every figure as a starting point, not a promise. The profiles at the end are my estimates from those
numbers, not tested minimums.

## The short answer

The full setup does not fit on one machine, and it is not meant to. It runs on three Proxmox hosts with two NVIDIA GPUs and two
NAS-class storage pools. A useful first goal is smaller: layers 1 to 4 on one host with about 48 GB of RAM.

## What layers 1 to 4 need

| VM | Layer | vCPU | RAM | Disk | Role |
|---|---|---|---|---|---|
| Management VM (IaC) | before 1 | not recorded here, plan 2 | plan 4 GB | plan 40 GB | Vault, Terraform, Ansible, the API service |
| fw-01 | 1 | 4 | 4 GB | VM disk | OPNsense firewall, with the IDS running |
| dns-01 | 1 | 2 | 1 GB | 16 GB | Pi-hole |
| vpn-01 | 2 | 2 | 2 GB | 20 GB | WireGuard |
| nas-01 | 3 | 4 | 3 GB | 32 GB OS plus data disks | OpenMediaVault, data disks passed through raw |
| pg-01 | 3 | 4 | 8 GB (Terraform default) | 80 GB | PostgreSQL and Elasticsearch |
| web-01 | 4 | 4 | 7 GB | 100 GB | Traefik, n8n, Prometheus, Grafana, NetBox |
| **Total** | | **22 vCPU** | **about 29 GB** | **about 250 GB plus data disks** | |

vCPUs can be oversubscribed; RAM should not be, beyond a little ballooning. The inventory lists pg-01 at 4 GB, so the live value
differs from the Terraform default. Check your own database size before trusting either.

## What the full setup runs on

| Host | Physical RAM | Role |
|---|---|---|
| pve-01 (primary) | 64 GB | The core platform plus the main GPU VM (RTX 4060 Ti 16 GB), Home Assistant, the git host and several small agents |
| pve-03 | 32 GB | Media, photos, the studio VM and the second GPU VM (RTX 2070), the second application host |
| pve-02 | not recorded here | Burst compute, powered by Wake-on-LAN and off for part of each day |

About 30 VMs run across them. Both main hosts are over-allocated today: the sum of VM memory is above physical RAM and
ballooning covers the gap, which caused swap pressure and a few incidents. Do not copy that on purpose. Budget RAM so that the
VMs that must always run fit in about 85 percent of the host's memory, after the host's own reserve and the ZFS cache.

**Storage.** Backups and bulk data live on ZFS pools on dedicated NAS VMs: one pool on three 6 TB disks, and a larger pool of
about 18 TB that the media and personal files sit on. Replication between the two NAS VMs runs over a 10 Gbit link. Fast
NVMe or SSD pools hold the VM disks. Plan capacity from your photo and media libraries, which dominate.

**GPUs.** Local AI (language models, speech to text, text to speech) needs an NVIDIA card with enough VRAM for the model you
choose, passed through to one VM. 16 GB is comfortable for a 14B-class model. Intel integrated GPUs are passed through to
the VMs that transcode video and run the camera recorder. Everything works without a GPU except the local AI, which you
can skip or point at a hosted model.

## Profiles

| Profile | Machine | You get |
|---|---|---|
| **Learn** | One Proxmox host, 32 GB RAM (tight) or 48 GB (comfortable), 8 or more cores, 500 GB SSD, an extra disk for NAS data | Layers 1 to 4: firewall, DNS, VPN, database, reverse proxy, automation, monitoring |
| **Household** | One host with 64 GB, or two with 64 and 32 GB, 1 to 2 TB NVMe, a NAS pool of several TB | Layers 1 to 8 without local AI: photos, media, files, documents, the git host |
| **Everything** | Three hosts, two NVIDIA GPUs, two ZFS pools, 10 Gbit between the NAS hosts | What this repository describes, including the AI concierge |

## Network

- A managed switch or a firewall with VLAN support. The design uses separate VLANs for management, services, IoT and guests.
- A firewall VM (OPNsense here) or hardware that can do per-VLAN rules. Per-VLAN blocking is part of the security model.
- Static addresses for the infrastructure. DHCP reservations for devices.
- 1 Gbit is enough for most things. The NAS replication link is the one place 10 Gbit pays off.

## Software and accounts

| Dependency | Version or detail |
|---|---|
| Proxmox VE | On each host. The Terraform provider is `bpg/proxmox`, and the playbooks assume an API token |
| Management VM | A Debian cloud image or similar |
| ansible-core | 2.17 or newer, plus the collections in [`ansible/requirements.yml`](../../ansible/requirements.yml) |
| Terraform | 1.6 or newer |
| Python | 3.11 |
| HashiCorp Vault | KV v2, as a container, with an AppRole |
| Docker | On the service VMs; the services run as containers |
| Cloud-init templates | Built once per Proxmox host, see [deploy](DEPLOY.md) |
| A domain name | For service names and certificates |
| A DNS provider with an API | Certificates come from Let's Encrypt over DNS-01, so the reverse proxy needs API access to your DNS zone. This setup uses IONOS; Traefik supports many providers |
| Git host | Forgejo here. Any host with Actions-compatible runners works for the checks |
| Optional | A VPS for a public front door, a hosted language model as a fallback, an email account for alerts |

## What this costs to run

Mostly electricity. Three hosts and a NAS pool draw real power around the clock, and the burst host is switched off for part of each
day for that reason. A hosted fallback model costs per use. Everything else is free software, with the licenses listed in
[THIRD_PARTY.md](../../THIRD_PARTY.md).
