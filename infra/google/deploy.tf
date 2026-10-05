# Lets GitHub Actions tell the VM to deploy as soon as a new image is pushed, rather than waiting
# for the hourly check. GitHub's runners can't reach the IPv6-only VM, so they SSH in through the
# Cloudflare Tunnel at ssh.<domain>.
#
# Two locks: Cloudflare Access only lets requests with the service token reach SSH at all, and
# the deploy user's key can only run update.sh, never a shell.

resource "cloudflare_dns_record" "ssh" {
  zone_id = var.cloudflare_zone_id
  name    = local.ssh_host
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.server.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

resource "cloudflare_zero_trust_access_service_token" "deploy" {
  account_id = var.cloudflare_account_id
  name       = "${var.name}-github-deploy"
  duration   = "forever" # the default is a year, after which deploys would silently stop
}

resource "cloudflare_zero_trust_access_policy" "deploy" {
  account_id = var.cloudflare_account_id
  name       = "${var.name}-github-deploy"
  decision   = "non_identity" # a service token, not a person logging in
  include = [{
    service_token = { token_id = cloudflare_zero_trust_access_service_token.deploy.id }
  }]
}

resource "cloudflare_zero_trust_access_application" "ssh" {
  account_id       = var.cloudflare_account_id
  name             = "${var.name}-ssh"
  type             = "self_hosted"
  domain           = local.ssh_host
  session_duration = "24h"
  policies = [{
    id         = cloudflare_zero_trust_access_policy.deploy.id
    precedence = 1
  }]
}

# The deploy user's SSH key. The private half ends up in the Terraform state, which is local and
# gitignored.
resource "tls_private_key" "deploy" {
  algorithm = "ED25519"
}

# Copy these three into the GitHub repo's Actions secrets, see README.
output "deploy_ssh_key" {
  description = "DEPLOY_SSH_KEY secret. Show with: terraform output -raw deploy_ssh_key"
  value       = tls_private_key.deploy.private_key_openssh
  sensitive   = true
}

output "cf_access_client_id" {
  description = "CF_ACCESS_CLIENT_ID secret"
  value       = cloudflare_zero_trust_access_service_token.deploy.client_id
  sensitive   = true
}

output "cf_access_client_secret" {
  description = "CF_ACCESS_CLIENT_SECRET secret"
  value       = cloudflare_zero_trust_access_service_token.deploy.client_secret
  sensitive   = true
}
