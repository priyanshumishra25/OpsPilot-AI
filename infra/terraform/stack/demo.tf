# Isolated demo environment (fictional services). Optional; state is ephemeral (restarting the control plane resets
# the demo to its seeded history).

locals {
  demo_services = var.enable_demo_environment ? {
    control-plane = {
      image   = local.images.demo, port = 8090
      command = ["uvicorn", "demo.control_plane.app:app", "--host", "0.0.0.0", "--port", "8090"]
      env     = { CONTROL_PLANE_DB = "/tmp/control-plane.db", DEMO_REPOS_DIR = "/repos" }
      secrets = {
        CONTROL_PLANE_READ_TOKEN   = { arn = local.sec["control-plane-read-token"] }
        CONTROL_PLANE_DEPLOY_TOKEN = { arn = local.sec["control-plane-deploy-token"] }
        CONTROL_PLANE_ADMIN_TOKEN  = { arn = local.sec["control-plane-admin-token"] }
      }
    }
    orders-api = {
      image   = local.images.demo, port = 8000
      command = ["uvicorn", "demo.services.app:app", "--host", "0.0.0.0", "--port", "8000"]
      env     = { SERVICE_NAME = "orders-api", CONTROL_PLANE_URL = "http://control-plane:8090", LOKI_PUSH_URL = "http://loki:3100/loki/api/v1/push" }
      secrets = {}
    }
    checkout-api = {
      image   = local.images.demo, port = 8001
      command = ["uvicorn", "demo.services.app:app", "--host", "0.0.0.0", "--port", "8001"]
      env     = { SERVICE_NAME = "checkout-api", CONTROL_PLANE_URL = "http://control-plane:8090", LOKI_PUSH_URL = "http://loki:3100/loki/api/v1/push" }
      secrets = {}
    }
    traffic = {
      image   = local.images.demo, port = null
      command = ["python", "-m", "demo.services.traffic"]
      env     = { ORDERS_URL = "http://orders-api:8000", CHECKOUT_URL = "http://checkout-api:8001" }
      secrets = {}
    }
    prometheus = {
      image = local.images.prometheus, port = 9090, command = null, env = {}, secrets = {}
    }
    loki = {
      image = "grafana/loki:3.7.9", port = 3100, command = null, env = {}, secrets = {}
    }
  } : {}
}

module "demo" {
  for_each                  = local.demo_services
  source                    = "../modules/ecs_service"
  kms_key_arn               = module.kms.key_arn
  name                      = "${local.prefix}-demo-${each.key}"
  cluster_arn               = aws_ecs_cluster.this.arn
  cluster_name              = aws_ecs_cluster.this.name
  image                     = each.value.image
  command                   = each.value.command
  cpu                       = 256
  memory                    = 512
  desired_count             = 1
  port                      = each.value.port
  service_connect_namespace = local.sc
  discovery_name            = each.value.port == null ? null : each.key
  environment               = each.value.env
  secrets                   = each.value.secrets
  subnet_ids                = local.private_subnets
  security_group_ids        = [aws_security_group.task["demo"].id]
  log_retention_days        = 7
  tags                      = merge(local.tags, { Demo = "true" })
}

module "github_oidc" {
  source               = "../modules/github_oidc"
  name                 = local.prefix
  github_repository    = var.github_repository
  github_environment   = var.environment
  create_oidc_provider = var.create_github_oidc_provider
  ecr_repository_arns  = module.registry.repository_arns
  ecs_cluster_arn      = aws_ecs_cluster.this.arn
  passable_role_arns = concat(
    [for m in [module.api, module.web, module.worker, module.agent_worker, module.migrate] : m.task_role_arn],
    [for m in [module.api, module.web, module.worker, module.agent_worker, module.migrate] : m.execution_role_arn],
    [for m in module.mcp : m.task_role_arn], [for m in module.mcp : m.execution_role_arn],
    [for m in module.demo : m.task_role_arn], [for m in module.demo : m.execution_role_arn],
  )
  tags = local.tags
}
