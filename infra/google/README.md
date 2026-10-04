# Deploying to Google Cloud (free-tier e2-micro)

Run these in order from this folder. The AWS setup in `infra/aws/` stays live until step 6.

You need: Terraform, the gcloud CLI logged in (`gcloud auth application-default login`), and a
project with billing linked (the free tier still needs a billing account).

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

Push to `main` or run the workflow by hand. The `push-gar` job runs next to the ECR one. It's
skipped while `GAR_IMAGE` is unset.

## 4. Create the server

```bash
terraform apply
```

Note the `ipv6_address` output.

## 5. Point DNS at it

In Cloudflare, change the **AAAA** record to the new `ipv6_address` (proxy on, SSL mode Flexible,
as before). Port 80 only accepts Cloudflare's addresses, so `curl` from your own machine won't
work. Test through the domain.

## 6. Retire AWS

Once the site works from Google: run `terraform destroy` in `infra/aws/` and delete that folder, then
delete the `push` job and `AWS_ROLE_ARN` / `ECR_PUBLIC_URI` from GitHub.

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

If the server can't reach Docker's install site or the registry over IPv6, change both
`stack_type`s to `IPV4_IPV6` (and give the subnet an `ip_cidr_range`). An in-use external IPv4
address is listed at $0.005/hour (about $3.65/month). Sources disagree on whether the free tier
covers it, so check your bill.
