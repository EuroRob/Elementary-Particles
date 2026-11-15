🧱 2. Terraform: define your new VMs

Extend your current model slightly:
```
vms = {
  postgres = {
    cores        = 2
    memory       = 4096
    datastore_id = "postgres"
    disk_size    = 50

    env      = "prod"
    role     = "db"
    critical = true
    backup   = "daily"
  }

  web = {
    cores        = 2
    memory       = 4096
    datastore_id = "general"
    disk_size    = 40

    env      = "prod"
    role     = "web"
    critical = true
    backup   = "daily"
  }

  pihole = {
    cores        = 1
    memory       = 1024
    datastore_id = "general"
    disk_size    = 10

    env      = "prod"
    role     = "dns"
    critical = true
    backup   = "daily"
  }

  gpu = {
    cores        = 8
    memory       = 16384
    datastore_id = "nvme"
    disk_size    = 100

    env      = "prod"
    role     = "gpu"
    critical = true
    backup   = "snapshot"
  }
}
```