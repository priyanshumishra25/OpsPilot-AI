# RDS PostgreSQL 17 with pgvector. The master password is generated and stored by RDS in Secrets Manager
# (manage_master_user_password), so it never appears in Terraform state.

variable "name" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "instance_class" { type = string }
variable "allocated_storage_gb" { type = number }
variable "max_allocated_storage_gb" { type = number }
variable "multi_az" { type = bool }
variable "backup_retention_days" { type = number }
variable "deletion_protection" { type = bool }
variable "kms_key_arn" { type = string }

variable "engine_version" {
  type    = string
  default = "17"
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-pg17"
  family = "postgres17"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier                            = var.name
  engine                                = "postgres"
  engine_version                        = var.engine_version
  instance_class                        = var.instance_class
  allocated_storage                     = var.allocated_storage_gb
  max_allocated_storage                 = var.max_allocated_storage_gb
  storage_type                          = "gp3"
  storage_encrypted                     = true
  kms_key_id                            = var.kms_key_arn
  db_name                               = "opspilot"
  username                              = "opspilot"
  manage_master_user_password           = true
  db_subnet_group_name                  = aws_db_subnet_group.this.name
  parameter_group_name                  = aws_db_parameter_group.this.name
  vpc_security_group_ids                = var.security_group_ids
  publicly_accessible                   = false
  multi_az                              = var.multi_az
  backup_retention_period               = var.backup_retention_days
  deletion_protection                   = var.deletion_protection
  skip_final_snapshot                   = !var.deletion_protection
  final_snapshot_identifier             = var.deletion_protection ? "${var.name}-final" : null
  copy_tags_to_snapshot                 = true
  auto_minor_version_upgrade            = true
  iam_database_authentication_enabled   = true
  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  performance_insights_kms_key_id       = var.kms_key_arn
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.monitoring.arn
  enabled_cloudwatch_logs_exports       = ["postgresql"]
  tags                                  = var.tags
  #checkov:skip=CKV_AWS_293:Deletion protection is on in prod (variable); off in dev so it can be torn down
  #checkov:skip=CKV_AWS_157:Multi-AZ is on in prod (variable); single-AZ in dev for cost
}

data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitoring" {
  name               = "${var.name}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

output "address" { value = aws_db_instance.this.address }
output "port" { value = aws_db_instance.this.port }
output "master_secret_arn" {
  description = "Secrets Manager secret (JSON with username/password) managed by RDS."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
output "instance_id" { value = aws_db_instance.this.identifier }
