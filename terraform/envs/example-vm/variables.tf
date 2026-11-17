# =============================================================================
# variables.tf — web-02 (Atlassian VM on pve-01)
# =============================================================================

variable "role_id" {
  description = "Vault AppRole role_id"
}

variable "secret_id" {
  description = "Vault AppRole secret_id"
  sensitive   = true
}

variable "vault_addr" {
  description = "HashiCorp Vault address"
  default     = "http://vault.internal:8200"
}

# ---------------------------------------------------------------------------
# Proxmox
# ---------------------------------------------------------------------------
variable "proxmox_node" {
  description = "Proxmox node to provision web-02 on"
  default     = "pve-01"
}

variable "proxmox_url" {
  description = "Proxmox API URL for pve-01"
  default     = "https://10.10.0.11:8006"
}

variable "vm_id" {
  description = "Proxmox VM ID for web-02"
  default     = 241
}

variable "ubuntu_template_id" {
  description = "VM ID of the Ubuntu 24.04 cloud-init template on pve-01"
  default     = 9002
}

# ---------------------------------------------------------------------------
# VM resources
# ---------------------------------------------------------------------------
variable "vm_cores" {
  description = "vCPU cores (Jira + Confluence JVMs need at least 4)"
  default     = 4
}

variable "vm_memory_mb" {
  description = "RAM in MB (Jira 2GB heap + Confluence 2GB heap + ~2GB OS/container overhead = 6GB)"
  default     = 6144
}

variable "vm_disk_gb" {
  description = "OS disk size in GB"
  default     = 64
}

variable "vm_data_disk_gb" {
  description = "Dedicated data disk size in GB, mounted at /opt (Jira + Confluence app data — Confluence restore alone is ~85GB)"
  default     = 200
}

variable "vm_datastore" {
  description = "Proxmox datastore for web-02 disk"
  default     = "zfs-nvme"
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
variable "vm_vlan_tag" {
  description = "VLAN tag for web-02 (Services VLAN 30)"
  default     = 30
}

variable "vm_static_ip" {
  description = "Static IP for web-02 on VLAN30"
  default     = "10.10.30.11"
}

variable "vm_gateway" {
  description = "Default gateway for VLAN30"
  default     = "10.10.30.1"
}

variable "vm_dns" {
  description = "Primary DNS server (Pi-hole on VLAN30)"
  default     = "10.10.30.10"
}
