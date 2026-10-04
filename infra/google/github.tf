# Lets the GitHub Actions workflow push to Artifact Registry using short-lived OIDC credentials,
# no stored keys.

# The numeric IDs come from `gh api repos/<owner>/<repo>` (owner.id and id). Matching on IDs
# rather than names means a renamed or recreated repo can't inherit access.
variable "github_repo_id" {
  type    = number
  default = 1086501402
}

variable "github_branch" {
  type    = string
  default = "main"
}

# A deleted pool keeps its ID for 30 days, so change this if you destroy and recreate.
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
  }
  attribute_condition = "assertion.repository_id == '${var.github_repo_id}' && assertion.ref == 'refs/heads/${var.github_branch}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_push" {
  account_id   = "${var.name}-github-push"
  display_name = "GitHub Actions push to Artifact Registry"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account_iam_member" "github_push_wif" {
  service_account_id = google_service_account.github_push.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repo_id}"
}

resource "google_artifact_registry_repository_iam_member" "github_push" {
  location   = google_artifact_registry_repository.app.location
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_push.email}"
}

output "github_wif_provider" {
  description = "Save as the GCP_WIF_PROVIDER variable in the GitHub repo"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_service_account" {
  description = "Save as the GCP_SERVICE_ACCOUNT variable in the GitHub repo"
  value       = google_service_account.github_push.email
}
