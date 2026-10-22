# Deployment

## Local (verified)

```bash
cp .env.example .env
docker compose --profile demo up --build      # omit --profile demo to skip the demo seed
```

Services: `postgres` (pgvector), `redis`, `s3` (SeaweedFS S3 gateway), `migrate` (one-shot: Alembic + bucket +
model warmup), `seed` (demo profile only), `api` (:8000), `worker`, `agent-worker`, four MCP servers
(:8101–8104), the demo environment (`control-plane` :8090, `orders-api`, `checkout-api`, `traffic`,
`prometheus` :9090, `loki` :3100) and `frontend` (:8080). All ports bind to `127.0.0.1`. Optional trace viewer:
`docker compose --profile observability up -d jaeger` with `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`.
Give Docker about 6 GB of memory. Tear down with `docker compose down` (keep data) or `docker compose down -v`.

Migrations are explicit: the `migrate` service runs `alembic upgrade head`; the API never migrates on start-up.

## AWS (Terraform written and statically validated; **not deployed**)

No AWS account or budget was available while building, so nothing below has been applied. What *has* been
verified: `terraform fmt -check`, `terraform validate` for `envs/dev` and `envs/prod` (AWS provider 6.70,
Terraform 1.17), Checkov (709 checks passed, 0 failed, 57 skipped, each skip with an inline reason), and
actionlint on the workflows. Treat the first real `apply` as untested.

### Layout

```
infra/terraform/
  modules/  network, kms, database (RDS PG17), cache (ElastiCache Redis), storage (S3 + SQS/DLQ), registry (ECR),
            secrets, alb, ecs_service (generic Fargate service), monitoring (alarms + SNS), github_oidc
  stack/    composition: security groups, IAM task policies, ECS cluster + Service Connect, all services, WAF
  envs/dev  single NAT, single-AZ db.t4g.small, no Redis replica, deletion protection off, 14-day logs
  envs/prod NAT per AZ, Multi-AZ db.m7g.large, Redis replica, deletion protection, 365-day logs, 2–6 API tasks
```

### Design points

- **Secrets never in state** (with one exception): Terraform creates empty Secrets Manager containers, and
  `infra/scripts/put-secrets.sh` writes values. The RDS master password is managed by RDS. The exception is the
  ElastiCache AUTH token, which Terraform generates, so the state bucket must be encrypted and access-controlled.
- **Least privilege:** each ECS service has its own execution role (it can read only the secrets that service
  references) and its own task role (for example, the API can write `s3://…/workspaces/*` and send to the ingestion
  queue; the worker can read objects and consume the queue; the agent and MCP servers have no AWS API access).
  The control-plane deploy token is given only to `mcp-operations`.
- **Network:** tasks run in private subnets. The ALB reaches only the API and web tasks; MCP servers accept
  traffic only from the API and the agent worker; demo services accept exact ports from exact peers; Postgres and
  Redis accept only their client groups. TLS terminates at the ALB (TLS 1.3 policy) behind WAF with AWS managed
  rules and a per-IP rate limit.
- **Encryption:** one customer-managed KMS key (rotation on) for S3, SQS, Secrets Manager, CloudWatch Logs, ECR,
  ElastiCache and RDS. RDS enforces TLS (`rds.force_ssl`), and the app connects with `ssl=require`.
- **Deploys vs infrastructure:** Terraform owns everything except the running task-definition revision and the
  desired count (`ignore_changes`). The GitHub deploy role (OIDC, scoped to `repo:<owner/name>:environment:<env>`)
  can push to this stack's ECR repositories, register task definitions, run the migration task, update services
  in this cluster, and pass only this stack's roles. It cannot change infrastructure.
- **Why ECS Fargate rather than Kubernetes:** about ten small services with no custom scheduling needs. Fargate
  plus Service Connect gives service discovery, rolling deploys with circuit-breaker rollback, and per-service IAM
  without operating a control plane or node groups. Kubernetes would be worth it for multi-tenant isolation at
  the pod level, GPU scheduling for self-hosted models, or a team that already operates EKS.

### First-time setup (order matters)

1. Create a state bucket (versioned, encrypted, public access blocked) and an ACM certificate for your domain.
2. `cd infra/terraform/envs/dev && cp terraform.tfvars.example terraform.tfvars` and fill it in.
   `terraform init -backend-config="bucket=<state-bucket>" -backend-config="region=<region>"`.
3. Create the pieces that must exist before tasks can start:
   `terraform apply -target=module.stack.module.kms -target=module.stack.module.registry -target=module.stack.module.secrets`.
4. Write secret values: `ENV=dev ANTHROPIC_API_KEY=… GITHUB_TOKEN=… ./infra/scripts/put-secrets.sh`
   (optional credentials default to the sentinel `unset`, which the app treats as not configured).
5. Build and push images tagged with a commit SHA (the deploy workflow's build step, or the same `docker buildx`
   commands locally).
6. `terraform apply -var image_tag=<sha>` for everything else, then run the `migrate` task once
   (`aws ecs run-task … --task-definition opspilot-dev-migrate`) and create the first admin user with
   `python -m opspilot.cli create-user` through the same task definition (command override).
7. Point DNS at the ALB, then set the GitHub environment variables `AWS_DEPLOY_ROLE_ARN` (Terraform output
   `github_deploy_role_arn`), `AWS_REGION` and `APP_DOMAIN`.

### Deploying

`Actions → Deploy → environment`. The workflow:

1. Builds arm64 images tagged with the commit SHA.
2. Records the current task definitions.
3. Registers new revisions with only the image tag changed.
4. Runs the migration task and requires exit code 0.
5. Rolls all services and waits for stability.
6. Smoke-tests `https://<domain>/readyz`.

On any failure it rolls every service back to the recorded revisions. ECS's deployment circuit breaker also rolls
back services that fail health checks. Migrations are forward-only, so schema changes must stay
backward-compatible with the previous release; this is not enforced by tooling yet.

### Cost (estimate, not measured)

The fixed monthly cost drivers for **dev** are the NAT gateway, the ALB, WAF (web ACL and rule groups),
RDS db.t4g.small, ElastiCache cache.t4g.micro, roughly 17 Fargate tasks (about 6 vCPU in total with the demo
environment), one KMS key, Secrets Manager secrets and CloudWatch Logs. Prod adds NAT per AZ, Multi-AZ RDS, a
Redis replica and more API tasks. Model API usage is billed separately by Anthropic and recorded per query or
turn in the app. Use the AWS Pricing Calculator for your region before applying; no bill has been observed.
Setting `enable_demo_environment = false` removes 6 tasks, but the operations tools would then have nothing to
act on.

### Rotating secrets

Re-run `put-secrets.sh` for the secret, then force a new deployment of the services that read it. Rotating the
MCP signing key invalidates in-flight tool tokens (TTL 120 s), and rotating the JWT secret signs everyone out.
Automatic rotation Lambdas are not implemented.

### Teardown

1. In prod, set `deletion_protection = false` and apply.
2. `terraform destroy -var image_tag=<any>`.
3. Secrets enter a 7-day recovery window. Delete them immediately with
   `aws secretsmanager delete-secret --force-delete-without-recovery` if needed.
4. The KMS key is scheduled for deletion after 30 days.
5. Delete the state bucket last.
