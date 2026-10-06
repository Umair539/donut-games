# Deploying to Google Cloud (free-tier e2-micro)

This is the live setup. The steps below are how it was built, in order, from this folder. For day-to-day changes see [Changing the startup script](#changing-the-startup-script) and [If something doesn't work](#if-something-doesnt-work).

You need: Terraform, the gcloud CLI logged in (`gcloud auth application-default login`), a
project with billing linked (the free tier still needs a billing account), and a Cloudflare API
token in `CLOUDFLARE_API_TOKEN` (see the top of `cloudflare.tf` for its permissions). If it's in
the repo's `.env`, load it in PowerShell with:

```powershell
Get-Content ..\..\.env | ForEach-Object { $n,$v = $_ -split '=',2; Set-Item "env:$n" $v }
```

How it fits together: the web pages are on Cloudflare Pages at `donutgames.co.uk`, deployed by
`.github/workflows/pages.yml`. The game server is the e2-micro, reached at
`server.donutgames.co.uk` through a Cloudflare Tunnel, which the VM opens outwards. So the VM
has no open web port, and only accepts WebSockets from the Pages site. The container saves its
rooms to the `donut-data` Docker volume when it stops, so games carry on through a deploy (see
the main README).

Put your project ID in `terraform.tfvars` (gitignored):

```hcl
project_id = "your-project-id"
```

## 1. Create the registry and GitHub access

```bash
terraform init
terraform apply -target=google_artifact_registry_repository_iam_member.github_push -target=google_service_account_iam_member.github_push_wif -target=google_iam_workload_identity_pool_provider.github -target=google_artifact_registry_repository_iam_member.public_read
```

## 2. Give GitHub the outputs

In the repo on GitHub: **Settings > Secrets and variables > Actions > Variables**. None of these
are secret.

| Name | Value |
| --- | --- |
| `GCP_WIF_PROVIDER` | `github_wif_provider` output |
| `GCP_SERVICE_ACCOUNT` | `github_service_account` output |
| `GAR_IMAGE` | `image` output |

## 3. Push the first image

Push to `main` or run **Build and push to Google Artifact Registry** by hand
(`.github/workflows/gar.yml`). It fails until the three variables above are set.

## 4. Create the server, tunnel and Pages site

```bash
terraform apply
```

Then let GitHub deploy the pages. In the repo: **Settings > Secrets and variables > Actions**.

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `CLOUDFLARE_ACCOUNT_ID` | `cloudflare_account_id` in `cloudflare.tf` |
| Secret | `CLOUDFLARE_API_TOKEN` | a separate token with only Account > Cloudflare Pages: Edit |

Run **Deploy web pages to Cloudflare Pages** by hand once. After that it runs on every push that
changes `web/`.

## 5. Moving an existing server over from the AAAA record

If the server already ran the old way (port 80 behind an AAAA record), do this instead of step 4.
The site is down for a few minutes between b and e.

a. Create everything except the root domain's record, which would clash with the AAAA record:

```bash
terraform apply -target=cloudflare_zero_trust_tunnel_cloudflared_config.server -target=cloudflare_dns_record.server -target=cloudflare_pages_project.web -target=google_compute_instance.server
```

b. Reset the VM so the new startup script runs (installs cloudflared, moves the container off
   port 80). It's back in about a minute:

```bash
gcloud compute instances reset donut-games --zone us-east1-b
```

c. Set the GitHub variable and secret from step 4 and run the Pages workflow.

d. Check `https://server.donutgames.co.uk/healthz` gives `{"ok":true}`, and play a game at the
   `pages_url` output.

e. In Cloudflare DNS, delete the **AAAA** record for `donutgames.co.uk`, then run a full
   `terraform apply`. It puts the Pages site on the domain and removes the old port 80 firewall
   rule.

## Deploying from GitHub over SSH

After pushing an image, `gar.yml` SSHes in through the tunnel at `ssh.donutgames.co.uk` and runs
`update.sh`, so the new version is live within seconds. Cloudflare Access only lets the GitHub
service token through, and the deploy key can only run `update.sh`. Cron still checks hourly in
case a deploy didn't happen. Setup (`deploy.tf`):

1. Add **Access: Apps and Policies: Edit** and **Access: Service Tokens: Edit** to your Cloudflare
   API token, then `terraform apply`.
2. Reset the VM so the startup script adds the deploy user:
   `gcloud compute instances reset donut-games --zone us-east1-b`
3. Get the VM's SSH host key through the tunnel, with
   [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
   installed. Run it before step 1's Access app exists, or log in when the browser opens. The
   login fails, but the key is saved to `kh`:

   ```bash
   ssh -o UserKnownHostsFile=kh -o StrictHostKeyChecking=accept-new -o HostKeyAlgorithms=ssh-ed25519 -o ProxyCommand="cloudflared access ssh --hostname %h" nobody@ssh.donutgames.co.uk
   ```

4. In GitHub, **Settings > Secrets and variables > Actions**:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `DEPLOY_SSH_KEY` | `terraform output -raw deploy_ssh_key` |
   | Secret | `CF_ACCESS_CLIENT_ID` | `terraform output -raw cf_access_client_id` |
   | Secret | `CF_ACCESS_CLIENT_SECRET` | `terraform output -raw cf_access_client_secret` |
   | Variable | `SSH_KNOWN_HOSTS` | the line in `kh` from step 3 |

## 6. Shut down AWS

The server ran on AWS Lightsail before this. Once the site works from Google, run
`terraform destroy` in `infra/aws/`. That has been done. The folder and `ecr.yml` (manual runs
only) are kept in the repo for completeness.

## Changing the startup script

GitHub only deploys new images. The startup script (`startup.sh.tftpl`, which also writes
`update.sh`, the deploy step) only reaches the VM through Terraform, and only runs on boot:

```bash
terraform plan    # should only change the VM's startup-script, in place
terraform apply
gcloud compute instances stop donut-games --zone us-east1-b
gcloud compute instances start donut-games --zone us-east1-b
```

Use stop and start rather than `reset`. Stopping shuts the VM down cleanly, so the container saves
its rooms first. `reset` is a power cut, and games come back from the last 30-second save. The VM
is back in about a minute, within the 2 minutes players have to reconnect after a restart.

## Staying free

- e2-micro, 30 GB standard disk, us-west1/us-central1/us-east1 only. Don't change the disk type
  or region.
- 1 GB/month egress from North America. That's fine for the game's traffic, but it's the limit to
  watch.
- Artifact Registry: 0.5 GB of storage. The cleanup policy keeps the last 2 images.
- Set a budget alert in the console (Billing > Budgets) in case something goes over.

## If something doesn't work

Nothing can SSH in except GitHub's deploy, which can only run `update.sh`. So a fix goes out as a
new deploy, and these show what's happening without logging in:

- Is it up: `https://server.donutgames.co.uk/healthz` should give `{"ok":true}`
- Deploy: the **Deploy to the server** step in GitHub Actions. Re-running the workflow deploys again.
- Tunnel: **Zero Trust > Networks > Tunnels** in Cloudflare shows whether the VM is connected
- Boot log, including the startup script, cloudflared and the hourly check's output on boot:
  `gcloud compute instances get-serial-port-output donut-games --zone us-east1-b`
- Last resort: stop and start the VM (see [Changing the startup script](#changing-the-startup-script)).
  That reruns the startup script, which reinstalls anything missing and restarts the container.
  If the VM is stuck and won't stop, `gcloud compute instances reset donut-games --zone us-east1-b`
  forces it.

If the server can't reach Docker's install site or the registry over IPv6, change both
`stack_type`s to `IPV4_IPV6` (and give the subnet an `ip_cidr_range`). An in-use external IPv4
address is listed at $0.005/hour (about $3.65/month). Sources disagree on whether the free tier
covers it, so check your bill.
