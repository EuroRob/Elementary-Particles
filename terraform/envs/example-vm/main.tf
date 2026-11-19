# =============================================================================
# web-02 — general application VM, on pve-03 since 2026-08-25
#
# Originally provisioned for Jira + Confluence (both stopped as of this
# comment -- Jira ~6 weeks, Confluence ~2 weeks; Confluence's replacement is
# Outline, see orchestration/confluence_to_outline_migration.py). Now hosts
# Firefly III (+ importer), Tandoor Recipes, Paperless-ngx (+ tika/gotenberg/
# redis), Outline (+ redis), and Hometown News -- migrated in over time from
# web-01 to relieve its memory pressure. Always-on VM in VLAN30.
#
# Pre-flight:
#   1. Ubuntu 24.04 cloud-init template must exist on pve-03 (VM ID 9002).
#   2. pg-01 must have the relevant app databases/users created.
#      See: ansible/playbooks/deploy_web02.yml (db_setup tasks)
#   3. If Jira/Confluence are ever revived, their license keys live in Vault:
#      secret/atlassian/jira/license, secret/atlassian/confluence/license
# =============================================================================

terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = ">= 0.46.0"
    }
    vault = {
      source  = "hashicorp/vault"
      version = ">= 3.0.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------
provider "vault" {
  address = var.vault_addr

  auth_login {
    path = "auth/approle/login"
    parameters = {
      role_id   = var.role_id
      secret_id = var.secret_id
    }
  }
}

data "vault_kv_secret_v2" "proxmox" {
  mount = "secret"
  # Was hardcoded to pve-01 even after this VM moved to pve-03 on
  # 2026-08-25 -- pve-01 and pve-03 have completely independent, non-clustered
  # Proxmox auth (see Terraform/CLAUDE.md), so every apply against this VM
  # since the migration would have failed with "no such user" against
  # pve-03's API using pve-01's token. Fixed 2026-09-13, found while applying a
  # routine memory change -- this had evidently gone unexercised since the
  # migration itself (state matched live, but not via a real apply).
  name = "proxmox/api/pve-03"
}

data "vault_kv_secret_v2" "ssh" {
  mount = "secret"
  name  = "homelab/ssh"
}

# ---------------------------------------------------------------------------
# Proxmox provider — pve-03, since the 2026-08-25 migration
# ---------------------------------------------------------------------------
provider "proxmox" {
  endpoint  = var.proxmox_url
  api_token = "${data.vault_kv_secret_v2.proxmox.data["token_id"]}=${data.vault_kv_secret_v2.proxmox.data["token_secret"]}"
  insecure  = true
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------
locals {
  ssh_keys = compact([
    lookup(data.vault_kv_secret_v2.ssh.data, "iac_public_key", ""),     # Terraform / IaC operator
    lookup(data.vault_kv_secret_v2.ssh.data, "ansible_public_key", ""), # Ansible control node
    lookup(data.vault_kv_secret_v2.ssh.data, "ringo_key", ""),          # admin (personal)
  ])

  common_tags = [
    "env-production",
    "role-atlassian",
    "service-jira",
    "service-confluence",
    "tier-application",
    "managed-terraform",
    "always-on",
  ]
}

# Docker install, directories, networks, and Postgres db/user setup are all
# handled by ansible/playbooks/deploy_web02.yml — no custom cloud-init
# needed here. (A custom cloud-init snippet would require SSH file upload to
# the Proxmox host, which isn't authorized — see IaC/CLAUDE.md "Proxmox
# Access". Use the `user_account` block below instead, same as web-01/git-01.)

# ---------------------------------------------------------------------------
# web-02 VM
# ---------------------------------------------------------------------------
resource "proxmox_virtual_environment_vm" "web_02" {
  name        = "web-02"
  description = "Atlassian Jira + Confluence — managed by Terraform"
  node_name   = var.proxmox_node
  vm_id       = var.vm_id
  tags        = local.common_tags

  # Always-on — no WoL management
  on_boot = true
  started = true

  clone {
    vm_id = var.ubuntu_template_id
    full  = true
  }

  cpu {
    cores   = var.vm_cores
    sockets = 1
    type    = "host"
  }

  memory {
    dedicated = var.vm_memory_mb
  }

  disk {
    datastore_id = var.vm_datastore
    size         = var.vm_disk_gb
    interface    = "scsi0"
    file_format  = "raw"
    discard      = "on"
  }

  # Dedicated data disk for /opt (Jira + Confluence app data). The 64GB OS
  # disk alone can't hold an ~85GB Confluence restore — mounting this at
  # /opt means docker-compose.yml, deploy_web02.yml, and inventory paths
  # (/opt/jira/data, /opt/confluence/data, /opt/atlassian) need no changes.
  disk {
    datastore_id = var.vm_datastore
    size         = var.vm_data_disk_gb
    interface    = "scsi1"
    file_format  = "raw"
    discard      = "on"
  }

  network_device {
    bridge  = "vmbr0"
    model   = "virtio"
    vlan_id = var.vm_vlan_tag
  }

  initialization {
    ip_config {
      ipv4 {
        address = "${var.vm_static_ip}/24"
        gateway = var.vm_gateway
      }
    }
    dns {
      servers = [var.vm_dns]
      domain  = "internal"
    }
    user_account {
      username = "ansible"
      keys     = local.ssh_keys
    }
  }

  lifecycle {
    ignore_changes = [
      # Prevent Terraform from toggling power state after initial provision
      started,
    ]
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "vm_id" {
  description = "Proxmox VM ID for web-02"
  value       = proxmox_virtual_environment_vm.web_02.vm_id
}

output "vm_ip" {
  description = "Static IP of web-02"
  value       = var.vm_static_ip
}

output "jira_url" {
  description = "Jira URL"
  value       = "https://jira.home.example.org"
}

output "confluence_url" {
  description = "Confluence URL"
  value       = "https://wiki.home.example.org"
}
