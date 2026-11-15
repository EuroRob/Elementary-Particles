locals {
  common_tags = {
    project = "homelab"
    managed = "terraform"
  }

  ssh_keys = compact([
    lookup(data.vault_kv_secret_v2.ssh.data, "iac_public_key", ""),     # Terraform / IaC operator
    lookup(data.vault_kv_secret_v2.ssh.data, "ansible_public_key", ""), # Ansible control node
    lookup(data.vault_kv_secret_v2.ssh.data, "ringo_key", ""),          # admin (personal)
  ])
}

module "vm" {
  for_each = var.vms

  source = "./modules/vm"

  name        = "${each.key}-${each.value.env}-01"
  node_name   = "pve-01"
  template_id = 9000

  cores  = each.value.cores
  memory = each.value.memory
  bridge = "vmbr0"

  datastore_id = each.value.datastore_id
  disk_size    = each.value.disk_size

  ssh_keys = local.ssh_keys

  # 🔥 TAG MAGIC
  tags = [
    "env:${each.value.env}",
    "role:${each.value.role}",
    "critical:${each.value.critical}",
    "backup:${each.value.backup}",
    "project:${local.common_tags.project}",
    "managed:${local.common_tags.managed}"
  ]
}