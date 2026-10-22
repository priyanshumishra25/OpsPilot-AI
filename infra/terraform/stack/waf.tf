# WAF on the public ALB: AWS managed baseline rule groups plus a per-IP rate limit.

variable "waf_rate_limit_per_5min" {
  type    = number
  default = 2000
}

resource "aws_wafv2_web_acl" "this" {
  #checkov:skip=CKV_AWS_192:AWSManagedRulesKnownBadInputsRuleSet (which contains Log4JRCE) is included via a dynamic block
  name  = local.prefix
  scope = "REGIONAL"
  default_action {
    allow {}
  }

  dynamic "rule" {
    for_each = { AWSManagedRulesCommonRuleSet = 1, AWSManagedRulesKnownBadInputsRuleSet = 2, AWSManagedRulesAmazonIpReputationList = 3 }
    content {
      name     = rule.key
      priority = rule.value
      override_action {
        none {}
      }
      statement {
        managed_rule_group_statement {
          name        = rule.key
          vendor_name = "AWS"
          dynamic "rule_action_override" {
            # Document uploads exceed the common rule set's 8 KB body limit; size is enforced by the API instead.
            for_each = rule.key == "AWSManagedRulesCommonRuleSet" ? ["SizeRestrictions_BODY"] : []
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }
      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = rule.key
        sampled_requests_enabled   = true
      }
    }
  }

  rule {
    name     = "rate-limit-per-ip"
    priority = 10
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-limit-per-ip"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = local.prefix
    sampled_requests_enabled   = true
  }
  tags = local.tags
}

# WAF log group names must start with "aws-waf-logs-".
resource "aws_cloudwatch_log_group" "waf" {
  #checkov:skip=CKV_AWS_338:Retention is a variable: 365 days in prod, shorter in dev
  name              = "aws-waf-logs-${local.prefix}"
  retention_in_days = var.log_retention_days
  kms_key_id        = module.kms.key_arn
  tags              = local.tags
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  redacted_fields {
    single_header { name = "authorization" }
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = module.alb.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}
