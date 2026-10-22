# GitHub Actions deploy role via OIDC federation (no long-lived AWS keys). Trust is limited to one repository and
# one GitHub environment; permissions cover pushing images and rolling ECS services for this stack only.

variable "name" { type = string }
variable "github_repository" {
  description = "owner/name"
  type        = string
}
variable "github_environment" { type = string }
variable "create_oidc_provider" {
  description = "Only one GitHub OIDC provider may exist per account; set false if it already exists."
  type        = bool
  default     = true
}
variable "ecr_repository_arns" { type = list(string) }
variable "ecs_cluster_arn" { type = string }
variable "passable_role_arns" {
  description = "Task and execution roles of this stack; the deploy role may pass only these to ECS."
  type        = list(string)
}
variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

locals {
  provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trust" {
  #checkov:skip=CKV_AWS_358:Trust requires aud=sts.amazonaws.com and an exact sub of repo:<owner/name>:environment:<env>
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages",
    ]
    resources = var.ecr_repository_arns
  }
  statement {
    sid       = "EcsDeploy"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices", "ecs:RunTask", "ecs:DescribeTasks", "ecs:ListTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.ecs_cluster_arn]
    }
  }
  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"] # these actions do not support resource-level permissions
  }
  statement {
    sid       = "PassOnlyStackRoles"
    actions   = ["iam:PassRole"]
    resources = var.passable_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "role_arn" { value = aws_iam_role.deploy.arn }
