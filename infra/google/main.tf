terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

variable "project_id" {
  description = "GCP project ID, with a billing account linked"
  type        = string
}

# The free e2-micro is only free in us-west1, us-central1 and us-east1. us-east1 is closest to the UK.
variable "region" {
  type    = string
  default = "us-east1"
}

variable "zone" {
  type    = string
  default = "us-east1-b"
}

variable "name" {
  type    = string
  default = "donut-games"
}

# Only Cloudflare's proxy may reach port 80. From https://www.cloudflare.com/ips-v6
variable "cloudflare_ipv6_ranges" {
  type = list(string)
  default = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  repository_id = var.name
  location      = var.region
  format        = "DOCKER"

  # The free tier includes 0.5 GB of storage, so keep only the last two images.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 2
    }
  }
  cleanup_policies {
    id     = "delete-rest"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }

  depends_on = [google_project_service.apis]
}

# Public, so the server can pull without credentials. The image contains no secrets.
resource "google_artifact_registry_repository_iam_member" "public_read" {
  location   = google_artifact_registry_repository.app.location
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.reader"
  member     = "allUsers"
}

locals {
  registry = "${var.region}-docker.pkg.dev"
  image    = "${local.registry}/${var.project_id}/${google_artifact_registry_repository.app.repository_id}/${var.name}:latest"
}

# The default network has no IPv6, so it needs its own network and subnet.
resource "google_compute_network" "vpc" {
  name                    = var.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name             = var.name
  region           = var.region
  network          = google_compute_network.vpc.id
  stack_type       = "IPV6_ONLY"
  ipv6_access_type = "EXTERNAL"
}

resource "google_compute_firewall" "http" {
  name          = "${var.name}-http"
  network       = google_compute_network.vpc.id
  source_ranges = var.cloudflare_ipv6_ranges

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }
}

resource "google_compute_firewall" "ssh" {
  name          = "${var.name}-ssh"
  network       = google_compute_network.vpc.id
  source_ranges = ["::/0"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# Reserved so the address survives stopping or recreating the VM and the Cloudflare record stays
# valid. Static IPv6 addresses are free, unlike IPv4.
resource "google_compute_address" "server" {
  name               = var.name
  region             = var.region
  address_type       = "EXTERNAL"
  ip_version         = "IPV6"
  ipv6_endpoint_type = "VM"
  subnetwork         = google_compute_subnetwork.subnet.id
}

# IPv6-only, so there is no external IPv4 address to pay for.
resource "google_compute_instance" "server" {
  name         = var.name
  machine_type = "e2-micro"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30            # the free tier covers 30 GB
      type  = "pd-standard" # only standard disk is free, the default (balanced) is not
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id
    stack_type = "IPV6_ONLY"
    ipv6_access_config {
      network_tier                = "PREMIUM"
      external_ipv6               = google_compute_address.server.address
      external_ipv6_prefix_length = "96"
    }
  }

  # Set as metadata rather than metadata_startup_script, so editing it doesn't recreate the VM.
  # It runs on every boot.
  metadata = {
    enable-oslogin = "TRUE"
    startup-script = templatefile("${path.module}/startup.sh.tftpl", {
      image = local.image
    })
  }
}

output "ipv6_address" {
  description = "Create an AAAA record pointing here, proxied through Cloudflare (that gives you HTTPS)"
  value       = google_compute_address.server.address
}

output "image" {
  description = "Save as the GAR_IMAGE variable in the GitHub repo"
  value       = local.image
}
