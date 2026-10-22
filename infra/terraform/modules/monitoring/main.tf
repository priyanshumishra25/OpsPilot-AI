# Alarms routed to an SNS topic: ALB 5xx, unhealthy API targets, messages in the ingestion DLQ, RDS CPU and storage.

variable "name" { type = string }
variable "alarm_email" {
  type    = string
  default = null
}
variable "alb_arn_suffix" { type = string }
variable "api_target_group_arn_suffix" { type = string }
variable "dlq_name" { type = string }
variable "db_instance_id" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_sns_topic" "alarms" {
  name              = "${var.name}-alarms"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarms = {
    alb-5xx = {
      namespace = "AWS/ApplicationELB", metric = "HTTPCode_Target_5XX_Count", stat = "Sum", threshold = 10,
      dims      = { LoadBalancer = var.alb_arn_suffix }, description = "API returned more than 10 5xx responses in 5 minutes"
    }
    api-unhealthy = {
      namespace = "AWS/ApplicationELB", metric = "UnHealthyHostCount", stat = "Maximum", threshold = 0,
      dims      = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.api_target_group_arn_suffix }, description = "An API task failed health checks"
    }
    ingestion-dlq = {
      namespace = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", stat = "Maximum", threshold = 0,
      dims      = { QueueName = var.dlq_name }, description = "Ingestion jobs were dead-lettered"
    }
    rds-cpu = {
      namespace = "AWS/RDS", metric = "CPUUtilization", stat = "Average", threshold = 80,
      dims      = { DBInstanceIdentifier = var.db_instance_id }, description = "RDS CPU above 80%"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = each.value.dims
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

output "alarm_topic_arn" { value = aws_sns_topic.alarms.arn }
