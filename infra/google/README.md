# Deploying to Google Cloud (free-tier e2-micro)

Run these in order from this folder. The AWS setup in `infra/aws/` stays live until step 6.

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
has no open web port, and only accepts WebSockets from the Pages site.

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
(`.github/workflows/gar.yml`). It runs next to the ECR workflow and fails until the three
variables above are set.

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

f. Then the image no longer needs the pages: remove `COPY web` from the `Dockerfile` and `web/**`
   from `gar.yml`'s paths, so changing the pages doesn't restart the server and end games.

## 6. Retire AWS

Once the site works from Google: run `terraform destroy` in `infra/aws/` and delete that folder, then
delete `.github/workflows/ecr.yml` and `AWS_ROLE_ARN` / `ECR_PUBLIC_URI` from GitHub.

## Staying free

- e2-micro, 30 GB standard disk, us-west1/us-central1/us-east1 only. Don't change the disk type
  or region.
- 1 GB/month egress from North America. That's fine for the game's traffic, but it's the limit to
  watch.
- Artifact Registry: 0.5 GB of storage. The cleanup policy keeps the last 2 images.
- Set a budget alert in the console (Billing > Budgets) in case something goes over.

## If something doesn't work

SSH: `gcloud compute ssh donut-games --zone us-east1-b` (needs IPv6 on your machine), or use the
serial console in the Compute Engine page.

- Startup log: `sudo journalctl -u google-startup-scripts`
- Deploy log: `sudo cat /var/log/donut-update.log`
- Container: `sudo docker ps` and `sudo docker logs donut-games`
- Force an update now: `sudo /opt/donut-games/update.sh`
- Tunnel: `sudo systemctl status cloudflared` and `sudo journalctl -u cloudflared`, or
  **Zero Trust > Networks > Tunnels** in Cloudflare, which shows whether it's connected
- The server directly, bypassing Cloudflare: `curl http://127.0.0.1:8000/healthz` on the VM

If the server can't reach Docker's install site or the registry over IPv6, change both
`stack_type`s to `IPV4_IPV6` (and give the subnet an `ip_cidr_range`). An in-use external IPv4
address is listed at $0.005/hour (about $3.65/month). Sources disagree on whether the free tier
covers it, so check your bill.
