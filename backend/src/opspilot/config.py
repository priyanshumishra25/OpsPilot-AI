"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Published, local-only Ed25519 key pair used by .env.example. Refused outside environment=local/test.
LOCAL_DEV_MCP_PRIVATE_KEY = "bG9jYWwtZGV2LW9ubHktb3BzcGlsb3QtbWNwLWtleSE="
LOCAL_DEV_MCP_PUBLIC_KEY = "w09lOp4oszP5+QrYUQbCU0ndyTwCe+anvTf4zrLDVKQ="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPSPILOT_", env_file=".env", extra="ignore")

    environment: Literal["local", "test", "dev", "prod"] = "local"
    log_level: str = "INFO"
    service_name: str = "opspilot-api"

    database_url: str = "postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot"
    db_pool_size: int = 10
    db_statement_timeout_ms: int = 15_000

    redis_url: str = "redis://localhost:6379/0"

    # Queue backend: Redis Streams locally, SQS in AWS.
    queue_backend: Literal["redis", "sqs"] = "redis"
    sqs_queue_url: str | None = None
    sqs_dlq_url: str | None = None

    # Object storage (S3; MinIO locally).
    s3_bucket: str = "opspilot-artifacts"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    # Sent on every PutObject in AWS. Empty for local gateways without SSE support.
    s3_server_side_encryption: Literal["AES256", "aws:kms", ""] = "AES256"

    jwt_secret: SecretStr = Field(default=SecretStr(""))
    jwt_issuer: str = "opspilot"
    jwt_audience: str = "opspilot-api"
    access_token_ttl_seconds: int = 1800
    cors_origins: list[str] = ["http://localhost:5173"]

    # Uploads
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 500

    # Embeddings / retrieval
    embedding_provider: Literal["fastembed"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_cache_dir: str | None = None
    reranker: Literal["none", "cross-encoder"] = "cross-encoder"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_candidate_k: int = 30
    retrieval_final_k: int = 8
    # Only the top-N fused candidates are cross-encoder scored (latency is linear in N).
    rerank_top_n: int = 20
    rrf_k: int = 60
    context_token_budget: int = 6000
    max_chunks_per_document: int = 4
    # Evidence-sufficiency thresholds (tuned on the golden set; see EVALUATION.md).
    abstain_min_rerank_score: float = -4.47
    abstain_max_dense_distance: float = 0.367
    chunk_size_chars: int = 1500
    chunk_overlap_chars: int = 200

    # Generation
    anthropic_api_key: SecretStr | None = None
    generation_model: str = "claude-opus-5-5"
    generation_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    generation_max_tokens: int = 8000
    # USD per million tokens, explicitly configured (see docs/EVALUATION.md for sources).
    model_input_price_per_mtok: float = 4.0
    model_output_price_per_mtok: float = 20.0
    model_cache_read_price_per_mtok: float = 0.20
    model_cache_write_price_per_mtok: float = 5.00
    generation_fallbacks_enabled: bool = True

    # Ingestion worker
    ingestion_max_attempts: int = 4
    ingestion_backoff_base_seconds: float = 2.0
    ingestion_backoff_max_seconds: float = 120.0
    ingestion_job_stale_seconds: int = 600

    # ---- MCP -------------------------------------------------------------------------------------
    # Ed25519 keys (base64 raw 32 bytes). The API holds the private key and mints short-lived per-server tool
    # tokens; MCP servers hold only the public key.
    mcp_signing_private_key: SecretStr | None = None
    mcp_verify_public_key: str | None = None
    mcp_token_ttl_seconds: int = 120
    mcp_knowledge_url: str = "http://mcp-knowledge:8101/mcp"
    mcp_github_url: str = "http://mcp-github:8102/mcp"
    mcp_observability_url: str = "http://mcp-observability:8103/mcp"
    mcp_operations_url: str = "http://mcp-operations:8104/mcp"
    mcp_tool_timeout_seconds: float = 20.0

    # GitHub MCP server. Repositories allow-list as JSON: {"owner/name": {"provider": "github"} |
    # {"provider": "local", "path": "/repos/orders-api"}}
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_repositories: dict[str, dict[str, str]] = {}

    # Observability MCP server
    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    demo_services: list[str] = ["orders-api", "checkout-api"]

    # Demo control plane (isolated demo environment only)
    control_plane_url: str = "http://control-plane:8090"
    control_plane_read_token: SecretStr | None = None
    control_plane_deploy_token: SecretStr | None = None
    control_plane_admin_token: SecretStr | None = None

    approval_ttl_seconds: int = 1800
    approval_require_distinct_approver: bool = True

    rate_limit_login_per_minute: int = 10
    rate_limit_query_per_minute: int = 60

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if v.get_secret_value() and len(v.get_secret_value()) < 32:
            raise ValueError("OPSPILOT_JWT_SECRET must be at least 32 characters")
        return v

    def model_post_init(self, __context: object) -> None:
        if self.environment not in ("local", "test") and self.jwt_secret.get_secret_value().startswith(
            "local-dev-only"
        ):
            raise ValueError("The local development JWT secret must not be used outside environment=local")

    def require_jwt_secret(self) -> str:
        secret = self.jwt_secret.get_secret_value()
        if not secret:
            raise RuntimeError("OPSPILOT_JWT_SECRET is not configured")
        return secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
