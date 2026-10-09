# The web pages on Cloudflare Pages at the domain itself, and the game server behind a Cloudflare
# Tunnel at server.<domain>. The tunnel is an outbound connection from the VM, so nothing on the VM
# is open to the internet except SSH.
#
# Needs a Cloudflare API token in the CLOUDFLARE_API_TOKEN environment variable, with Account >
# Cloudflare Tunnel: Edit, Zone > DNS: Edit, Account > Cloudflare Pages: Edit, and for deploy.tf
# Account > Access: Apps and Policies: Edit and Account > Access: Service Tokens: Edit.

provider "cloudflare" {}

# Not secret, they're in the dashboard URL
variable "cloudflare_account_id" {
  type    = string
  default = "67e2c43a7c62257588605b51541a1caa"
}

variable "cloudflare_zone_id" {
  type    = string
  default = "a2c080a18a1a9ab456047a2b2877a24d"
}

variable "domain" {
  type    = string
  default = "donutgames.co.uk"
}

locals {
  server_host = "server.${var.domain}"
  ssh_host    = "ssh.${var.domain}"
}

# --- Game server ---

resource "cloudflare_zero_trust_tunnel_cloudflared" "server" {
  account_id = var.cloudflare_account_id
  name       = var.name
  config_src = "cloudflare" # routes below are managed here, not in a file on the VM
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "server" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.server.id
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "server" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.server.id
  config = {
    ingress = [
      {
        hostname = local.server_host
        service  = "http://127.0.0.1:8000" # the container, published on the VM's loopback only
      },
      {
        hostname = local.ssh_host # for deploys from GitHub, see deploy.tf
        service  = "ssh://127.0.0.1:22"
      },
      { service = "http_status:404" }, # the last rule must match everything
    ]
  }
}

resource "cloudflare_dns_record" "server" {
  zone_id = var.cloudflare_zone_id
  name    = local.server_host
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.server.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # automatic, required for proxied records
}

# --- Web pages ---

# Deployed by .github/workflows/deploy.yml, not built by Cloudflare
resource "cloudflare_pages_project" "web" {
  account_id        = var.cloudflare_account_id
  name              = var.name
  production_branch = "main"
}

resource "cloudflare_pages_domain" "web" {
  account_id   = var.cloudflare_account_id
  project_name = cloudflare_pages_project.web.name
  name         = var.domain
}

# Pages doesn't create this itself when the domain is added through the API. Cloudflare flattens a
# CNAME at the root of the domain, so this is allowed.
resource "cloudflare_dns_record" "web" {
  zone_id = var.cloudflare_zone_id
  name    = var.domain
  type    = "CNAME"
  content = cloudflare_pages_project.web.subdomain
  proxied = true
  ttl     = 1
}

output "pages_url" {
  description = "Where the Pages deploys appear before the domain is pointed at them"
  value       = "https://${cloudflare_pages_project.web.subdomain}"
}
