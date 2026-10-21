# Deployment

## Local (verified)

```bash
cp .env.example .env
docker compose --profile demo up --build      # omit --profile demo to skip the demo seed
```

Services: `postgres` (pgvector), `redis`, `s3` (SeaweedFS S3 gateway), `migrate` (one-shot: Alembic +
bucket + model warmup), `seed` (demo profile only), `api` (:8000), `worker`, `frontend` (:8080). All
ports bind to `127.0.0.1`. Models are cached in the `models` volume. Tear down with
`docker compose down` (keep data) or `docker compose down -v` (delete data).

Database migrations are explicit: the `migrate` service runs `alembic upgrade head`. The API never
migrates on start-up.

## AWS (not yet implemented)

Planned for Phase 7: Terraform modules for VPC, security groups, IAM (least-privilege task roles),
ECR, ECS Fargate (API, worker, MCP services), ALB, RDS PostgreSQL with pgvector, S3 (default
encryption and block-public-access), SQS with DLQ, Secrets Manager and CloudWatch, plus a GitHub
Actions deploy workflow using OIDC federation. No cloud resources have been created, and nothing in
this repository claims a live deployment.
