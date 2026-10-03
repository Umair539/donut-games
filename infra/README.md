# Deploying to Lightsail (London)

Run these in order. Everything is in this folder; run the `terraform` commands from here.

You need: Terraform, AWS CLI with credentials (`aws sts get-caller-identity` should work), and the code pushed to GitHub.

## 1. Create just the GitHub role and the ECR repository

The server pulls its image from ECR, and the image has to be built by the GitHub workflow, so the
repository and the workflow's role come first. Targeting the role policy also creates what it
depends on (the role, the repository, and the OIDC provider if you ask for one).

```bash
terraform init
terraform apply -target=aws_iam_role_policy.github_push
```

If your AWS account has no GitHub OIDC provider yet (IAM > Identity providers), add
`-var create_github_oidc_provider=true`. If it already has one, leave it as is.

Terraform prints two outputs, `github_role_arn` and `github_ecr_repository_url`. You can show them again
with `terraform output`.

## 2. Give GitHub those two values

In the repo on GitHub: **Settings > Secrets and variables > Actions**.

| Kind | Name | Value |
| --- | --- | --- |
| Secret (Secrets tab) | `AWS_ROLE_ARN` | `github_role_arn` output |
| Variable (Variables tab) | `ECR_REPOSITORY_URL` | `github_ecr_repository_url` output |

## 3. Run the workflow to push the first image

The workflow is `.github/workflows/ecr.yml`. It has to be committed and pushed to `main`.

- Either push anything to `main`, or
- go to the **Actions** tab, choose **Build and push to ECR**, and click **Run workflow**.

It runs the tests, then builds the image and pushes it as `:latest`. Wait for it to go green. Check
the image is there: AWS console > ECR > Private repositories > donut-games (region eu-west-2).

If it fails at "Configure AWS credentials", the role's trust doesn't match: the repo name and
branch default to `Umair539/donut-games` and `main` (variables `github_repo`, `github_branch`).

## 4. Create the rest: the Lightsail server

```bash
terraform apply
```

(Add the same `-var create_github_oidc_provider=...` as in step 1 if you used it, so Terraform
doesn't try to remove it.) This creates the instance, which installs Docker on first boot and
pulls the image. Note the `ipv6_address` output.

## 5. Point DNS at it

In Cloudflare, add an **AAAA** record for the name you want (for example `games`) pointing at the
`ipv6_address`, with the **proxy turned on** (orange cloud). Under **SSL/TLS**, set the mode to
**Flexible**, because the server speaks plain HTTP on port 80.

Give the first boot a few minutes to install Docker and pull the image, then open your domain.

## Deploying changes later

Push to `main`. The workflow pushes a new `:latest`, and the server picks it up within 5 minutes
(a cron job runs `/opt/donut-games/update.sh`). Games in progress end when it restarts.

## If something doesn't work

You can only SSH in from a machine with IPv6 (`curl -6 ifconfig.me`). Use the Lightsail console
(Connect) or `ssh admin@<ipv6_address>` with the key from your Lightsail account.

- Install log: `sudo cat /var/log/cloud-init-output.log`
- Deploy log: `sudo cat /var/log/donut-update.log`
- Container: `sudo docker ps` and `sudo docker logs donut-games`
- Force an update now: `sudo /opt/donut-games/update.sh`

If the instance can't download Docker or the image, it may be an IPv6-only problem (some hosts have
no IPv6). The fix is switching `bundle_id` to `nano_3_0` and `ip_address_type` to `dualstack` in
`main.tf` (about $5/month instead of $3.50).

## Tear down

```bash
terraform destroy
```
