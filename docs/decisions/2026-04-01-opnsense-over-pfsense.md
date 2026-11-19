---
type: Note
state: organized
ep_tier: network
ep_particle: fw-01
belongs_to: "[[OPNSENSE_FIREWALL_ARCHITECTURE]]"
related_to:
  - "[[NETWORK_STACK_DEPLOYMENT]]"
  - "[[PROXMOX_HOST_IaC]]"
decision_date: "2026-04-01"
status: active
---

# ADR: OPNSense over pfSense

**Date:** 2026-04-01  
**Status:** Active  
**Tier:** network | **Particle:** fw-01  

## Context

The EP network stack required a perimeter firewall VM to manage VLAN routing,
DNS, DHCP, and the WireGuard port-forward. The two primary candidates for a
Proxmox-hosted virtual firewall were OPNSense and pfSense.

## Options Considered

| Factor | OPNSense | pfSense CE |
|--------|----------|------------|
| REST API | ✓ `opnsense-api` — full JSON API | ✗ Limited; community plugin only |
| Ansible support | ✓ First-class via API | ✗ Fragile; SSH-only |
| Update cadence | ✓ Monthly security releases | Slower since Netgate acquisition |
| Community governance | ✓ Open; decoupled from vendor | Vendor-controlled |
| IaC compatibility | ✓ API allows Terraform/Ansible | ✗ Requires workarounds |

## Decision

**OPNSense 25.1** on fw-01.

The deciding factor was the REST API (`opnsense-api`). The entire EP philosophy
is IaC-first: everything managed by Terraform/Ansible/n8n, nothing by clicking
in a GUI. OPNSense's API makes firewall rules, static routes, NAT, and DHCP
reservations fully scriptable. pfSense CE would have required fragile SSH-based
automation.

## Consequences

- All OPNSense configuration lives in Ansible roles under
  `ansible/Roles/opnsense/`
- Static route `10.8.0.0/24 → vpn-01` added by `static_routes.yml` — this was
  the missing piece causing WireGuard return-path failures (see
  `incidents/` for context)
- DHCP reservations for all EP VMs are managed via the API, not manually
- `fw-01` is deployed from ISO (not cloud-init) due to OPNSense installer requirements
