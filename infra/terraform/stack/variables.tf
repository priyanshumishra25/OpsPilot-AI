variable "environment" {
  description = "dev or prod"
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}
variable "name" {
  type    = string
  default = "opspilot"
}
variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}
variable "single_nat_gateway" { type = bool }
variable "domain_name" {
  description = "Public hostname served by the ALB (used for CORS); DNS is managed outside this stack."
  type        = string
}
variable "certificate_arn" { type = string }
variable "image_tag" {
  description = "Immutable image tag (git SHA) deployed to every service."
  type        = string
}
variable "db_instance_class" { type = string }
variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}
variable "db_multi_az" { type = bool }
variable "db_backup_retention_days" { type = number }
variable "deletion_protection" { type = bool }
variable "redis_node_type" { type = string }
variable "redis_replicas" {
  type    = number
  default = 0
}
variable "api_count" {
  type    = number
  default = 1
}
variable "api_max_count" {
  type    = number
  default = 2
}
variable "enable_demo_environment" {
  description = "Deploy the isolated demo environment (control plane, demo services, Prometheus, Loki)."
  type        = bool
  default     = true
}
variable "github_repository" {
  description = "owner/name allowed to deploy via OIDC"
  type        = string
}
variable "create_github_oidc_provider" {
  type    = bool
  default = true
}
variable "alarm_email" {
  type    = string
  default = null
}
variable "log_retention_days" {
  type    = number
  default = 30
}
