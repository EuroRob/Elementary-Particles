locals {

  middlewares = merge(
    local.middlewares,

    {
      "authelia" = {
        forwardAuth = {
          address            = "http://authelia:9091/api/verify?rd=https://auth.lab.local"
          trustForwardHeader = true

          authResponseHeaders = [
            "Remote-User",
            "Remote-Groups",
            "Remote-Email"
          ]
        }
      }
    }
  )

  auth_enabled = {
    for name, tags in local.parsed_vm_tags :
    name => try(tags.auth, "none")
  }

  auth_level = {
    for name, tags in local.parsed_vm_tags :
    name => try(tags.auth_level, "one_factor")
  }

  exposed = {
    for name, tagmap in var.tags :
    name => tagmap
    if try(tagmap.expose, "false") == "true"
  }

  config = {
    http = {
      routers = {
        for name, tagmap in local.exposed :
        name => {
          rule        = "Host(`${name}.${var.domain}`)"
          entryPoints = ["websecure"]
          service     = name
          tls         = {}
        }
      }

      services = {
        for name, tagmap in local.exposed :
        name => {
          loadBalancer = {
            servers = [
              {
                url = "http://${var.vms[name]}:${lookup(tagmap, "port", "80")}"
              }
            ]
          }
        }
      }
    }
  }
}

resource "local_file" "traefik_dynamic" {
  content  = yamlencode(local.config)
  filename = "/tmp/traefik-dynamic.yml"
}

resource "null_resource" "deploy" {
  depends_on = [local_file.traefik_dynamic]

  provisioner "file" {
    source      = local_file.traefik_dynamic.filename
    destination = "/etc/traefik/dynamic.yml"

    connection {
      type        = "ssh"
      host        = var.traefik_host
      user        = "iac"
      private_key = var.ssh_private_key
    }
  }

  provisioner "remote-exec" {
    inline = [
      "docker restart traefik"
    ]

    connection {
      type        = "ssh"
      host        = var.traefik_host
      user        = "iac"
      private_key = var.ssh_private_key
    }
  }
}