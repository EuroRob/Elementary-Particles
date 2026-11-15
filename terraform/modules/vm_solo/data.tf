data "vault_kv_secret_v2" "proxmox" {
  mount = "secret"
  name  = "proxmox"
}
data "vault_kv_secret_v2" "ssh" {
  mount = "secret"
  name  = "ssh"
}