# Lets the GitHub Actions workflow push to ECR using short-lived OIDC credentials, no stored keys.

variable "github_repo" {
  description = "GitHub repository allowed to push, as owner/name"
  type        = string
  default     = "Umair539/donut-games"
}

variable "github_branch" {
  type    = string
  default = "main"
}

# An account can have only one of these per URL. Set to false if you already created it.
variable "create_github_oidc_provider" {
  type    = bool
  default = false
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

resource "aws_iam_role" "github_push" {
  name = "${var.name}-github-push"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/${var.github_branch}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_push" {
  name = "push-to-ecr-public"
  role = aws_iam_role.github_push.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr-public:BatchCheckLayerAvailability",
          "ecr-public:InitiateLayerUpload",
          "ecr-public:UploadLayerPart",
          "ecr-public:CompleteLayerUpload",
          "ecr-public:PutImage",
        ]
        Resource = aws_ecrpublic_repository.app.arn
      },
    ]
  })
}

output "github_role_arn" {
  description = "Save as the AWS_ROLE_ARN secret in the GitHub repo"
  value       = aws_iam_role.github_push.arn
}

output "github_ecr_public_uri" {
  description = "Save as the ECR_PUBLIC_URI variable in the GitHub repo"
  value       = aws_ecrpublic_repository.app.repository_uri
}
