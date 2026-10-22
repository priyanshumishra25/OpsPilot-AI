# ElastiCache (Redis OSS) with TLS and an AUTH token. Used for rate limits, SSE wake-ups and the agent-run stream.
# The AUTH token is generated here and stored in Secrets Manager; it is also in Terraform state, which must be an
# encrypted, access-controlled backend (see DEPLOYMENT.md).

variable "name" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "node_type" { type = string }
variable "kms_key_arn" { type = string }

variable "replicas" {
  type    = number
  default = 0
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "random_password" "auth" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "auth" {
  name                    = "${var.name}/redis-auth-token"
  recovery_window_in_days = 7
  kms_key_id              = var.kms_key_arn
  tags                    = var.tags
  #checkov:skip=CKV2_AWS_57:Rotating the AUTH token requires coordinated client restarts; documented manual procedure
}

resource "aws_secretsmanager_secret_version" "auth" {
  secret_id     = aws_secretsmanager_secret.auth.id
  secret_string = random_password.auth.result
}

resource "aws_elasticache_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = var.name
  description                = "OpsPilot ${var.name}"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.node_type
  num_cache_clusters         = 1 + var.replicas
  automatic_failover_enabled = var.replicas > 0
  multi_az_enabled           = var.replicas > 0
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = var.security_group_ids
  at_rest_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn
  transit_encryption_enabled = true
  auth_token                 = random_password.auth.result
  snapshot_retention_limit   = 1
  apply_immediately          = true
  tags                       = var.tags
  #checkov:skip=CKV2_AWS_50:Multi-AZ failover is enabled when replicas > 0 (prod); dev runs a single node for cost
}

output "host" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
output "auth_secret_arn" { value = aws_secretsmanager_secret.auth.arn }
