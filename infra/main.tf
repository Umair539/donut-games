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

# Public ECR repositories can only be managed from us-east-1, whatever region the server is in.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# Public, so the instance can pull without credentials. The image contains no secrets.
resource "aws_ecrpublic_repository" "app" {
  provider        = aws.us_east_1
  repository_name = var.name
}

locals {
  image = "${aws_ecrpublic_repository.app.repository_uri}:latest"
}

# IPv6-only is the cheapest bundle: $3.50/month, 512 MB, no public IPv4 address.
resource "aws_lightsail_instance" "server" {
  name              = var.name
  availability_zone = "${var.region}a"
  blueprint_id      = "debian_12"
  bundle_id         = "nano_ipv6_3_0"
  ip_address_type   = "ipv6"

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    image = local.image
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
