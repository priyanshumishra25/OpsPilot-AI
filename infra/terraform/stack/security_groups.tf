# Network policy: the ALB reaches only the API and web tasks; MCP servers accept traffic only from the API and the
# agent worker; demo services only from the observability/operations MCP servers and each other; Postgres and Redis
# only from tasks that carry the matching client group. Tasks need egress (ECR, model API, GitHub) via NAT.

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb"
  description = "Public HTTPS entry point"
  vpc_id      = module.network.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS from the internet"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  #checkov:skip=CKV_AWS_260:Port 80 only serves a 301 redirect to HTTPS
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP, redirected to HTTPS"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_vpc" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = module.network.vpc_cidr
  ip_protocol       = "-1"
  description       = "To targets inside the VPC"
}

locals {
  task_groups = ["api", "web", "worker", "agent", "mcp", "demo", "migrate"]
}

resource "aws_security_group" "task" {
  for_each    = toset(local.task_groups)
  name        = "${local.prefix}-${each.key}"
  description = "OpsPilot ${each.key} tasks"
  vpc_id      = module.network.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_egress_rule" "task_all" {
  for_each          = aws_security_group.task
  security_group_id = each.value.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Outbound (ECR, model API, GitHub, AWS APIs) via NAT"
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.task["api"].id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "API from the load balancer"
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.task["web"].id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
  description                  = "Web from the load balancer"
}

resource "aws_vpc_security_group_ingress_rule" "mcp_from_callers" {
  for_each                     = toset(["api", "agent"])
  security_group_id            = aws_security_group.task["mcp"].id
  referenced_security_group_id = aws_security_group.task[each.key].id
  from_port                    = 8101
  to_port                      = 8104
  ip_protocol                  = "tcp"
  description                  = "MCP from ${each.key}"
}

locals {
  # Exact demo flows: source group => ports it may reach on demo tasks.
  demo_flows = {
    "mcp-8090"  = { from = "mcp", port = 8090 }  # observability/operations -> control plane
    "mcp-9090"  = { from = "mcp", port = 9090 }  # observability -> Prometheus
    "mcp-3100"  = { from = "mcp", port = 3100 }  # observability -> Loki
    "api-8090"  = { from = "api", port = 8090 }  # admin scenario controls -> control plane
    "demo-8090" = { from = "demo", port = 8090 } # demo services -> control plane
    "demo-3100" = { from = "demo", port = 3100 } # demo services -> Loki (log push)
    "demo-8000" = { from = "demo", port = 8000 } # traffic/Prometheus -> orders-api
    "demo-8001" = { from = "demo", port = 8001 } # traffic/Prometheus -> checkout-api
  }
}

resource "aws_vpc_security_group_ingress_rule" "demo_from" {
  for_each                     = local.demo_flows
  security_group_id            = aws_security_group.task["demo"].id
  referenced_security_group_id = aws_security_group.task[each.value.from].id
  from_port                    = each.value.port
  to_port                      = each.value.port
  ip_protocol                  = "tcp"
  description                  = "Demo port ${each.value.port} from ${each.value.from}"
}

resource "aws_security_group" "db" {
  name        = "${local.prefix}-db"
  description = "PostgreSQL"
  vpc_id      = module.network.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from" {
  for_each                     = toset(["api", "worker", "agent", "mcp", "migrate"])
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.task[each.key].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "Postgres from ${each.key}"
}

resource "aws_security_group" "cache" {
  name        = "${local.prefix}-cache"
  description = "Redis"
  vpc_id      = module.network.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "cache_from" {
  for_each                     = toset(["api", "worker", "agent", "mcp"])
  security_group_id            = aws_security_group.cache.id
  referenced_security_group_id = aws_security_group.task[each.key].id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis from ${each.key}"
}
