provider "aws" {
  region = var.region
  default_tags { tags = { Project = "opspilot", Environment = "prod" } }
}

variable "region" { type = string }
variable "domain_name" { type = string }
variable "certificate_arn" { type = string }
variable "image_tag" { type = string }
variable "github_repository" { type = string }
variable "create_github_oidc_provider" {
  type    = bool
  default = false # usually created once, by dev
}
variable "alarm_email" { type = string }

# Production-style: NAT per AZ, Multi-AZ database, Redis replica, deletion protection, two API tasks minimum.
# The demo environment stays enabled because the operations tools only act on it (by design).
module "stack" {
  source                      = "../../stack"
  environment                 = "prod"
  domain_name                 = var.domain_name
  certificate_arn             = var.certificate_arn
  image_tag                   = var.image_tag
  github_repository           = var.github_repository
  create_github_oidc_provider = var.create_github_oidc_provider
  alarm_email                 = var.alarm_email
  single_nat_gateway          = false
  db_instance_class           = "db.m7g.large"
  db_allocated_storage_gb     = 50
  db_multi_az                 = true
  db_backup_retention_days    = 14
  deletion_protection         = true
  redis_node_type             = "cache.t4g.small"
  redis_replicas              = 1
  api_count                   = 2
  api_max_count               = 6
  enable_demo_environment     = true
  log_retention_days          = 365
}

output "stack" { value = module.stack }
