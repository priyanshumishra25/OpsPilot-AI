locals {
  images = {
    backend    = "${module.registry.repository_urls["backend"]}:${var.image_tag}"
    frontend   = "${module.registry.repository_urls["frontend"]}:${var.image_tag}"
    demo       = "${module.registry.repository_urls["demo"]}:${var.image_tag}"
    prometheus = "${module.registry.repository_urls["prometheus"]}:${var.image_tag}"
  }
  sc  = aws_service_discovery_http_namespace.this.arn
  sec = module.secrets.arns

  # Configuration shared by every backend process. Connection strings are assembled by the app from parts because
  # ECS can only inject individual secret fields.
  backend_env = {
    OPSPILOT_ENVIRONMENT               = var.environment
    OPSPILOT_DATABASE_HOST             = module.database.address
    OPSPILOT_DATABASE_PORT             = tostring(module.database.port)
    OPSPILOT_DATABASE_SSL              = "require"
    OPSPILOT_REDIS_HOST                = module.cache.host
    OPSPILOT_REDIS_TLS                 = "true"
    OPSPILOT_QUEUE_BACKEND             = "sqs"
    OPSPILOT_SQS_QUEUE_URL             = module.storage.queue_url
    OPSPILOT_SQS_DLQ_URL               = module.storage.dlq_url
    OPSPILOT_S3_BUCKET                 = module.storage.bucket_name
    OPSPILOT_S3_REGION                 = data.aws_region.current.region
    OPSPILOT_S3_SERVER_SIDE_ENCRYPTION = "" # bucket default (SSE-KMS with the stack key) applies
    OPSPILOT_CORS_ORIGINS              = jsonencode(["https://${var.domain_name}"])
    OPSPILOT_EMBEDDING_CACHE_DIR       = "/tmp/models"
    HF_HOME                            = "/tmp/models/hf"
    OPSPILOT_MCP_KNOWLEDGE_URL         = "http://mcp-knowledge:8101/mcp"
    OPSPILOT_MCP_GITHUB_URL            = "http://mcp-github:8102/mcp"
    OPSPILOT_MCP_OBSERVABILITY_URL     = "http://mcp-observability:8103/mcp"
    OPSPILOT_MCP_OPERATIONS_URL        = "http://mcp-operations:8104/mcp"
    OPSPILOT_MCP_BIND_HOST             = "0.0.0.0"
    OPSPILOT_CONTROL_PLANE_URL         = "http://control-plane:8090"
    OPSPILOT_PROMETHEUS_URL            = "http://prometheus:9090"
    OPSPILOT_LOKI_URL                  = "http://loki:3100"
    OPSPILOT_GITHUB_REPOSITORIES = jsonencode({
      "opspilot-demo/orders-api"   = { provider = "local", path = "/repos/orders-api", workspaces = "*" }
      "opspilot-demo/checkout-api" = { provider = "local", path = "/repos/checkout-api", workspaces = "*" }
    })
  }
  db_secrets = {
    OPSPILOT_DATABASE_USER     = { arn = module.database.master_secret_arn, key = "username" }
    OPSPILOT_DATABASE_PASSWORD = { arn = module.database.master_secret_arn, key = "password" }
  }
  redis_secrets   = { OPSPILOT_REDIS_PASSWORD = { arn = module.cache.auth_secret_arn } }
  jwt_secret      = { OPSPILOT_JWT_SECRET = { arn = local.sec["jwt-secret"] } }
  verify_key      = { OPSPILOT_MCP_VERIFY_PUBLIC_KEY = { arn = local.sec["mcp-verify-public-key"] } }
  signing_key     = { OPSPILOT_MCP_SIGNING_PRIVATE_KEY = { arn = local.sec["mcp-signing-private-key"] } }
  model_key       = { OPSPILOT_ANTHROPIC_API_KEY = { arn = local.sec["anthropic-api-key"] } }
  private_subnets = module.network.private_subnet_ids
}

data "aws_region" "current" {}

module "api" {
  source                    = "../modules/ecs_service"
  kms_key_arn               = module.kms.key_arn
  name                      = "${local.prefix}-api"
  cluster_arn               = aws_ecs_cluster.this.arn
  cluster_name              = aws_ecs_cluster.this.name
  image                     = local.images.backend
  command                   = ["uvicorn", "opspilot.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]
  cpu                       = 1024
  memory                    = 3072
  desired_count             = var.api_count
  port                      = 8000
  target_group_arn          = module.alb.api_target_group_arn
  service_connect_namespace = local.sc
  environment               = local.backend_env
  secrets = merge(local.db_secrets, local.redis_secrets, local.jwt_secret, local.verify_key, local.signing_key, local.model_key,
  { OPSPILOT_CONTROL_PLANE_ADMIN_TOKEN = { arn = local.sec["control-plane-admin-token"] } })
  subnet_ids         = local.private_subnets
  security_group_ids = [aws_security_group.task["api"].id]
  task_policy_json   = data.aws_iam_policy_document.api.json
  autoscaling        = { min = var.api_count, max = var.api_max_count, cpu_target = 60 }
  log_retention_days = var.log_retention_days
  tags               = local.tags
}

module "web" {
  source                    = "../modules/ecs_service"
  kms_key_arn               = module.kms.key_arn
  name                      = "${local.prefix}-web"
  cluster_arn               = aws_ecs_cluster.this.arn
  cluster_name              = aws_ecs_cluster.this.name
  image                     = local.images.frontend
  cpu                       = 256
  memory                    = 512
  desired_count             = 1
  port                      = 8080
  target_group_arn          = module.alb.frontend_target_group_arn
  service_connect_namespace = local.sc
  environment               = { API_ORIGIN = "https://${var.domain_name}" }
  subnet_ids                = local.private_subnets
  security_group_ids        = [aws_security_group.task["web"].id]
  log_retention_days        = var.log_retention_days
  tags                      = local.tags
}

module "worker" {
  source                    = "../modules/ecs_service"
  kms_key_arn               = module.kms.key_arn
  name                      = "${local.prefix}-worker"
  cluster_arn               = aws_ecs_cluster.this.arn
  cluster_name              = aws_ecs_cluster.this.name
  image                     = local.images.backend
  command                   = ["python", "-m", "opspilot.cli", "worker"]
  cpu                       = 1024
  memory                    = 3072
  desired_count             = 1
  stop_timeout              = 120
  service_connect_namespace = local.sc
  environment               = local.backend_env
  secrets                   = merge(local.db_secrets, local.redis_secrets, local.jwt_secret)
  subnet_ids                = local.private_subnets
  security_group_ids        = [aws_security_group.task["worker"].id]
  task_policy_json          = data.aws_iam_policy_document.worker.json
  log_retention_days        = var.log_retention_days
  tags                      = local.tags
}

module "agent_worker" {
  source                    = "../modules/ecs_service"
  kms_key_arn               = module.kms.key_arn
  name                      = "${local.prefix}-agent"
  cluster_arn               = aws_ecs_cluster.this.arn
  cluster_name              = aws_ecs_cluster.this.name
  image                     = local.images.backend
  command                   = ["python", "-m", "opspilot.cli", "agent-worker"]
  cpu                       = 1024
  memory                    = 2048
  desired_count             = 1
  stop_timeout              = 120
  service_connect_namespace = local.sc
  environment               = local.backend_env
  secrets                   = merge(local.db_secrets, local.redis_secrets, local.jwt_secret, local.signing_key, local.model_key)
  subnet_ids                = local.private_subnets
  security_group_ids        = [aws_security_group.task["agent"].id]
  log_retention_days        = var.log_retention_days
  tags                      = local.tags
}

locals {
  mcp_servers = {
    knowledge = {
      port    = 8101, cpu = 1024, memory = 3072
      secrets = merge(local.db_secrets, local.redis_secrets, local.jwt_secret, local.verify_key)
    }
    github = {
      port    = 8102, cpu = 256, memory = 512
      secrets = merge(local.jwt_secret, local.verify_key, { OPSPILOT_GITHUB_TOKEN = { arn = local.sec["github-token"] } })
    }
    observability = {
      port    = 8103, cpu = 256, memory = 512
      secrets = merge(local.jwt_secret, local.verify_key, { OPSPILOT_CONTROL_PLANE_READ_TOKEN = { arn = local.sec["control-plane-read-token"] } })
    }
    operations = {
      port = 8104, cpu = 256, memory = 512
      # The only holder of the deploy token.
      secrets = merge(local.db_secrets, local.redis_secrets, local.jwt_secret, local.verify_key,
      { OPSPILOT_CONTROL_PLANE_DEPLOY_TOKEN = { arn = local.sec["control-plane-deploy-token"] } })
    }
  }
}

module "mcp" {
  for_each                  = local.mcp_servers
  source                    = "../modules/ecs_service"
  kms_key_arn               = module.kms.key_arn
  name                      = "${local.prefix}-mcp-${each.key}"
  cluster_arn               = aws_ecs_cluster.this.arn
  cluster_name              = aws_ecs_cluster.this.name
  image                     = local.images.backend
  command                   = ["python", "-m", "opspilot.mcpservers.${each.key}"]
  cpu                       = each.value.cpu
  memory                    = each.value.memory
  desired_count             = 1
  port                      = each.value.port
  service_connect_namespace = local.sc
  discovery_name            = "mcp-${each.key}"
  environment               = local.backend_env
  secrets                   = each.value.secrets
  subnet_ids                = local.private_subnets
  security_group_ids        = [aws_security_group.task["mcp"].id]
  log_retention_days        = var.log_retention_days
  tags                      = local.tags
}

# One-off task definition for schema migrations; the deploy workflow runs it before rolling services.
module "migrate" {
  source             = "../modules/ecs_service"
  kms_key_arn        = module.kms.key_arn
  name               = "${local.prefix}-migrate"
  cluster_arn        = aws_ecs_cluster.this.arn
  cluster_name       = aws_ecs_cluster.this.name
  image              = local.images.backend
  command            = ["sh", "-c", "alembic upgrade head && python -m opspilot.cli bootstrap"]
  cpu                = 512
  memory             = 2048
  desired_count      = null
  environment        = local.backend_env
  secrets            = merge(local.db_secrets, local.redis_secrets, local.jwt_secret)
  subnet_ids         = local.private_subnets
  security_group_ids = [aws_security_group.task["migrate"].id]
  log_retention_days = var.log_retention_days
  tags               = local.tags
}
