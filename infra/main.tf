terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "eu-west-2" # London
}

variable "name" {
  type    = string
  default = "donut-games"
}

resource "aws_ecr_repository" "app" {
  name         = var.name
  force_delete = true # lets terraform destroy remove it even when it holds images
}

# The workflow only pushes :latest, so each push leaves the previous image untagged. Delete those.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after a day"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}

locals {
  image = "${aws_ecr_repository.app.repository_url}:latest"
}

# Lightsail instances can't have IAM roles, so the server pulls with the keys of this user, which
# can only read this one repository. The keys are stored in Terraform state and on the instance.
resource "aws_iam_user" "pull" {
  name = "${var.name}-ecr-pull"
}

resource "aws_iam_user_policy" "pull" {
  name = "pull-from-ecr"
  user = aws_iam_user.pull.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = aws_ecr_repository.app.arn
      },
    ]
  })
}

resource "aws_iam_access_key" "pull" {
  user = aws_iam_user.pull.name
}

# IPv6-only is the cheapest bundle: $3.50/month, 512 MB, no public IPv4 address.
resource "aws_lightsail_instance" "server" {
  name              = var.name
  availability_zone = "${var.region}a"
  blueprint_id      = "debian_12"
  bundle_id         = "nano_ipv6_3_0"
  ip_address_type   = "ipv6"

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    image      = local.image
    registry   = split("/", local.image)[0]
    region     = var.region
    access_key = aws_iam_access_key.pull.id
    secret_key = aws_iam_access_key.pull.secret
  })
}

resource "aws_lightsail_instance_public_ports" "server" {
  instance_name = aws_lightsail_instance.server.name

  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
  }
  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
  }
}

output "ipv6_address" {
  description = "Create an AAAA record pointing here, proxied through Cloudflare (that gives you HTTPS)"
  value       = aws_lightsail_instance.server.ipv6_addresses[0]
}

output "image" {
  description = "Where the GitHub workflow pushes to"
  value       = local.image
}
