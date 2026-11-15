terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.50"
    }
    vault = {
      source = "hashicorp/vault"
    }
  }
}

provider "vault" {
  address = "http://127.0.0.1:8200"

  auth_login {
    path = "auth/approle/login"

    parameters = {
      role_id   = var.role_id
      secret_id = var.secret_id
    }
  }
}

provider "proxmox" {
  endpoint = data.vault_kv_secret_v2.proxmox.data["api_url"]

  api_token = "${data.vault_kv_secret_v2.proxmox.data["token_id"]}=${data.vault_kv_secret_v2.proxmox.data["token_secret"]}"

  insecure = true
}