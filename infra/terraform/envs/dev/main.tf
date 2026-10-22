provider "aws" {
  region = var.region
  default_tags { tags = { Project = "opspilot", Environment = "dev" } }
}

variable "region" { type = string }
variable "domain_name" { type = string }
variable "certificate_arn" { type = string }
variable "image_tag" { type = string }
variable "github_repository" { type = string }
variable "create_github_oidc_provider" {
  type    = bool
  default = true
}
variable "alarm_email" {
  type    = string
  default = null
}

# Cost-conscious: single NAT gateway, single-AZ small database, no Redis replica, deletion protection off.
module "stack" {
  source                      = "../../stack"
  environment                 = "dev"
  domain_name                 = var.domain_name
  certificate_arn             = var.certificate_arn
  image_tag                   = var.image_tag
  github_repository           = var.github_repository
  create_github_oidc_provider = var.create_github_oidc_provider
  alarm_email                 = var.alarm_email
  single_nat_gateway          = true
  db_instance_class           = "db.t4g.small"
  db_multi_az                 = false
  db_backup_retention_days    = 3
  deletion_protection         = false
  redis_node_type             = "cache.t4g.micro"
  api_count                   = 1
  api_max_count               = 2
  enable_demo_environment     = true
  log_retention_days          = 14
}

output "stack" { value = module.stack }
