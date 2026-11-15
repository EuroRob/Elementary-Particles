variable "vms" {
  description = "VM IPs"
  type        = map(string)
}

variable "tags" {
  description = "Parsed VM tags"
  type        = map(map(string))
}

variable "domain" {
  default = "lab.local"
}

variable "traefik_host" {}
variable "ssh_private_key" {
  sensitive = true
}