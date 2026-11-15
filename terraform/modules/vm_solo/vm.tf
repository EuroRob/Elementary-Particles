locals {
  ssh_keys = compact([
    lookup(data.vault_kv_secret_v2.ssh.data, "iac_public_key", ""),     # Terraform / IaC operator
    lookup(data.vault_kv_secret_v2.ssh.data, "ansible_public_key", ""), # Ansible control node
    lookup(data.vault_kv_secret_v2.ssh.data, "ringo_key", ""),          # admin (personal)
  ])
}

resource "proxmox_virtual_environment_vm" "debian_vm" {
  name      = "tf-test-vm-01"
  node_name = "pve-01"

  clone {
    vm_id = 9000
  }

  cpu {
    cores = 2
  }

  memory {
    dedicated = 2048
  }

  agent {
    enabled = true
  }

  network_device {
    bridge = "vmbr0"
  }

  initialization {
    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }

    user_account {
      username = "debian"
      keys     = local.ssh_keys
    }
  }
}