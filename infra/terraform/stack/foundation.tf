locals {
  prefix = "${var.name}-${var.environment}"
  tags   = { Project = "opspilot", Environment = var.environment, ManagedBy = "terraform" }
}

module "kms" {
  source = "../modules/kms"
  name   = local.prefix
  tags   = local.tags
}

module "network" {
  source             = "../modules/network"
  name               = local.prefix
  cidr               = var.vpc_cidr
  single_nat_gateway = var.single_nat_gateway
  kms_key_arn        = module.kms.key_arn
  log_retention_days = var.log_retention_days
  tags               = local.tags
}

module "registry" {
  source       = "../modules/registry"
  kms_key_arn  = module.kms.key_arn
  name         = local.prefix
  repositories = ["backend", "frontend", "demo", "prometheus"]
  tags         = local.tags
}

module "storage" {
  source        = "../modules/storage"
  kms_key_arn   = module.kms.key_arn
  name          = local.prefix
  force_destroy = var.environment == "dev"
  tags          = local.tags
}

module "secrets" {
  source      = "../modules/secrets"
  kms_key_arn = module.kms.key_arn
  name        = local.prefix
  secret_names = [
    "jwt-secret", "mcp-signing-private-key", "mcp-verify-public-key", "anthropic-api-key", "github-token",
    "control-plane-read-token", "control-plane-deploy-token", "control-plane-admin-token",
  ]
  tags = local.tags
}

module "database" {
  source                   = "../modules/database"
  kms_key_arn              = module.kms.key_arn
  name                     = local.prefix
  subnet_ids               = module.network.private_subnet_ids
  security_group_ids       = [aws_security_group.db.id]
  instance_class           = var.db_instance_class
  allocated_storage_gb     = var.db_allocated_storage_gb
  max_allocated_storage_gb = var.db_allocated_storage_gb * 5
  multi_az                 = var.db_multi_az
  backup_retention_days    = var.db_backup_retention_days
  deletion_protection      = var.deletion_protection
  tags                     = local.tags
}

module "cache" {
  source             = "../modules/cache"
  kms_key_arn        = module.kms.key_arn
  name               = local.prefix
  subnet_ids         = module.network.private_subnet_ids
  security_group_ids = [aws_security_group.cache.id]
  node_type          = var.redis_node_type
  replicas           = var.redis_replicas
  tags               = local.tags
}

module "alb" {
  source              = "../modules/alb"
  name                = local.prefix
  vpc_id              = module.network.vpc_id
  subnet_ids          = module.network.public_subnet_ids
  security_group_ids  = [aws_security_group.alb.id]
  certificate_arn     = var.certificate_arn
  deletion_protection = var.deletion_protection
  tags                = local.tags
}

resource "aws_ecs_cluster" "this" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
  service_connect_defaults { namespace = aws_service_discovery_http_namespace.this.arn }
  tags = local.tags
}

# Internal DNS for service-to-service calls (e.g. http://mcp-knowledge:8101) via ECS Service Connect.
resource "aws_service_discovery_http_namespace" "this" {
  name = "${local.prefix}.local"
  tags = local.tags
}

module "monitoring" {
  source                      = "../modules/monitoring"
  name                        = local.prefix
  alarm_email                 = var.alarm_email
  alb_arn_suffix              = module.alb.arn_suffix
  api_target_group_arn_suffix = module.alb.api_target_group_arn_suffix
  dlq_name                    = module.storage.dlq_name
  db_instance_id              = module.database.instance_id
  tags                        = local.tags
}
