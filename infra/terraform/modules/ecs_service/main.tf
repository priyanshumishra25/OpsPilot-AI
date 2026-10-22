# One ECS Fargate service (or a run-task-only task definition when desired_count = null) with its own task role,
# log group, optional ALB target, Service Connect endpoint and CPU-based autoscaling.

variable "name" { type = string }
variable "cluster_arn" { type = string }
variable "cluster_name" { type = string }
variable "image" { type = string }
variable "command" {
  type    = list(string)
  default = null
}
variable "cpu" { type = number }
variable "memory" { type = number }
variable "desired_count" {
  description = "null = task definition only (one-off tasks such as migrations)."
  type        = number
  default     = 1
}
variable "port" {
  type    = number
  default = null
}
variable "environment" {
  type    = map(string)
  default = {}
}
variable "secrets" {
  description = "Env var name => Secrets Manager secret ARN and optional JSON key."
  type        = map(object({ arn = string, key = optional(string) }))
  default     = {}
}
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "task_policy_json" {
  description = "IAM policy document for the task role (least privilege, per service)."
  type        = string
  default     = null
}
variable "target_group_arn" {
  type    = string
  default = null
}
variable "service_connect_namespace" {
  type    = string
  default = null
}
variable "discovery_name" {
  description = "Service Connect DNS name other services use (e.g. mcp-knowledge). Requires a port."
  type        = string
  default     = null
}
variable "stop_timeout" {
  type    = number
  default = 30
}
variable "autoscaling" {
  type    = object({ min = number, max = number, cpu_target = number })
  default = null
}
variable "kms_key_arn" { type = string }
variable "log_retention_days" {
  type    = number
  default = 30
}
variable "tags" {
  type    = map(string)
  default = {}
}

data "aws_region" "current" {}

resource "aws_cloudwatch_log_group" "this" {
  #checkov:skip=CKV_AWS_338:Retention is a variable: 365 days in prod, shorter in dev
  name              = "/opspilot/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Per-service execution role: pull images, write its own logs, and read only the secrets this service references.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  count = length(var.secrets) == 0 ? 0 : 1
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = distinct([for s in values(var.secrets) : s.arn])
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count  = length(var.secrets) == 0 ? 0 : 1
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets[0].json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "task" {
  count  = var.task_policy_json == null ? 0 : 1
  role   = aws_iam_role.task.id
  policy = var.task_policy_json
}

locals {
  container = merge({
    name                   = "app"
    image                  = var.image
    essential              = true
    stopTimeout            = var.stop_timeout
    environment            = [for k, v in var.environment : { name = k, value = v }]
    secrets                = [for k, v in var.secrets : { name = k, valueFrom = v.key == null ? v.arn : "${v.arn}:${v.key}::" }]
    readonlyRootFilesystem = false
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = "app"
      }
    }
    portMappings = var.port == null ? [] : [{ name = "http", containerPort = var.port, protocol = "tcp", appProtocol = "http" }]
  }, var.command == null ? {} : { command = var.command })
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([local.container])
  tags                  = var.tags
}

resource "aws_ecs_service" "this" {
  count                              = var.desired_count == null ? 0 : 1
  name                               = var.name
  cluster                            = var.cluster_arn
  task_definition                    = aws_ecs_task_definition.this.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  enable_execute_command             = false
  propagate_tags                     = "SERVICE"
  wait_for_steady_state              = false

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = var.target_group_arn == null ? [] : [1]
    content {
      target_group_arn = var.target_group_arn
      container_name   = "app"
      container_port   = var.port
    }
  }

  dynamic "service_connect_configuration" {
    for_each = var.service_connect_namespace == null ? [] : [1]
    content {
      enabled   = true
      namespace = var.service_connect_namespace
      dynamic "service" {
        for_each = var.port == null || var.discovery_name == null ? [] : [1]
        content {
          port_name      = "http"
          discovery_name = var.discovery_name
          client_alias {
            port     = var.port
            dns_name = var.discovery_name
          }
        }
      }
    }
  }

  # Deploys (GitHub Actions) register new task-definition revisions with new image tags and autoscaling owns the
  # count, so Terraform does not revert either. Terraform owns everything else about the service.
  lifecycle { ignore_changes = [desired_count, task_definition] }
  tags = var.tags
}

resource "aws_appautoscaling_target" "this" {
  count              = var.autoscaling == null || var.desired_count == null ? 0 : 1
  service_namespace  = "ecs"
  resource_id        = "service/${var.cluster_name}/${aws_ecs_service.this[0].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.autoscaling.min
  max_capacity       = var.autoscaling.max
}

resource "aws_appautoscaling_policy" "cpu" {
  count              = length(aws_appautoscaling_target.this)
  name               = "${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension
  target_tracking_scaling_policy_configuration {
    target_value = var.autoscaling.cpu_target
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}

output "task_definition_arn" { value = aws_ecs_task_definition.this.arn }
output "task_role_arn" { value = aws_iam_role.task.arn }
output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "service_name" { value = var.desired_count == null ? null : aws_ecs_service.this[0].name }
output "log_group" { value = aws_cloudwatch_log_group.this.name }
