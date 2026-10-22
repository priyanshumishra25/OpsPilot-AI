"""Composition root: builds long-lived dependencies once per process."""

from __future__ import annotations

from dataclasses import dataclass

from anthropic import AsyncAnthropic
from redis.asyncio import Redis

from opspilot.agent.service import AGENT_STREAM, AgentService
from opspilot.approvals.service import ApprovalService
from opspilot.auth.tokens import TokenService
from opspilot.config import Settings
from opspilot.db.base import Database
from opspilot.db.models import EMBEDDING_DIM
from opspilot.ingestion.service import IngestionService
from opspilot.integrations.control_plane import ControlPlaneClient
from opspilot.mcp_client.gateway import McpGateway, ServerName
from opspilot.mcpservers.tokens import ToolTokenIssuer
from opspilot.queue.base import JobQueue
from opspilot.queue.redis_streams import RedisStreamQueue
from opspilot.queue.sqs import SqsQueue
from opspilot.rag.chunking import Chunker
from opspilot.rag.cost import ModelPricing
from opspilot.rag.embeddings import FastEmbedEmbeddings
from opspilot.rag.generation import GroundedAnswerer
from opspilot.rag.rerank import CrossEncoderReranker, Reranker
from opspilot.rag.retriever import HybridRetriever, RetrievalConfig
from opspilot.storage.objects import S3ObjectStore


@dataclass
class Container:
    settings: Settings
    db: Database
    redis: Redis
    store: S3ObjectStore
    queue: JobQueue
    embeddings: FastEmbedEmbeddings
    reranker: Reranker | None
    retriever: HybridRetriever
    answerer: GroundedAnswerer
    ingestion: IngestionService
    tokens: TokenService
    chunker: Chunker
    approvals: ApprovalService
    mcp: McpGateway | None = None
    agent: AgentService | None = None
    control_plane_admin: ControlPlaneClient | None = None

    async def close(self) -> None:
        await self.queue.close()
        await self.redis.aclose()
        await self.db.dispose()


def build_queue(settings: Settings, redis: Redis) -> JobQueue:
    if settings.queue_backend == "sqs":
        if not settings.sqs_queue_url:
            raise RuntimeError("OPSPILOT_SQS_QUEUE_URL is required when queue_backend=sqs")
        return SqsQueue(settings.sqs_queue_url, settings.sqs_dlq_url, region=settings.s3_region)
    return RedisStreamQueue(Redis.from_url(settings.redis_url))


def build_container(settings: Settings) -> Container:
    if settings.embedding_dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"Configured embedding_dim={settings.embedding_dim} does not match the schema ({EMBEDDING_DIM}); "
            "changing embedding models requires a migration and full re-index"
        )
    db = Database(
        settings.database_url, pool_size=settings.db_pool_size, statement_timeout_ms=settings.db_statement_timeout_ms
    )
    redis = Redis.from_url(settings.redis_url)
    store = S3ObjectStore(settings)
    queue = build_queue(settings, redis)
    embeddings = FastEmbedEmbeddings(settings.embedding_model, cache_dir=settings.embedding_cache_dir)
    reranker: Reranker | None = (
        CrossEncoderReranker(settings.reranker_model, cache_dir=settings.embedding_cache_dir)
        if settings.reranker == "cross-encoder"
        else None
    )
    retriever = HybridRetriever(
        db,
        embeddings,
        reranker,
        RetrievalConfig(
            candidate_k=settings.retrieval_candidate_k,
            final_k=settings.retrieval_final_k,
            rerank_top_n=settings.rerank_top_n,
            rrf_k=settings.rrf_k,
            context_token_budget=settings.context_token_budget,
            max_chunks_per_document=settings.max_chunks_per_document,
            abstain_min_rerank_score=settings.abstain_min_rerank_score,
            abstain_max_dense_distance=settings.abstain_max_dense_distance,
        ),
    )
    api_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    client = AsyncAnthropic(api_key=api_key, timeout=120.0, max_retries=2) if api_key else None
    answerer = GroundedAnswerer(
        client,
        model=settings.generation_model,
        effort=settings.generation_effort,
        max_tokens=settings.generation_max_tokens,
        pricing=ModelPricing(
            model=settings.generation_model,
            input_per_mtok=settings.model_input_price_per_mtok,
            output_per_mtok=settings.model_output_price_per_mtok,
            cache_read_per_mtok=settings.model_cache_read_price_per_mtok,
            cache_write_per_mtok=settings.model_cache_write_price_per_mtok,
        ),
        fallbacks_enabled=settings.generation_fallbacks_enabled,
    )
    return Container(
        settings=settings,
        db=db,
        redis=redis,
        store=store,
        queue=queue,
        embeddings=embeddings,
        reranker=reranker,
        retriever=retriever,
        answerer=answerer,
        ingestion=IngestionService(
            db, store, queue, max_bytes=settings.max_upload_bytes, max_attempts=settings.ingestion_max_attempts
        ),
        tokens=TokenService(
            settings.require_jwt_secret(),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            ttl_seconds=settings.access_token_ttl_seconds,
        ),
        chunker=Chunker(chunk_size=settings.chunk_size_chars, chunk_overlap=settings.chunk_overlap_chars),
        approvals=ApprovalService(
            ttl_seconds=settings.approval_ttl_seconds,
            require_distinct_approver=settings.approval_require_distinct_approver,
        ),
        mcp=_build_gateway(settings),
        agent=(
            AgentService(
                db,
                redis,
                RedisStreamQueue(Redis.from_url(settings.redis_url), stream=AGENT_STREAM, group="agent-workers"),
                model=settings.agent_model,
            )
            if settings.mcp_signing_private_key
            else None
        ),
        control_plane_admin=(
            ControlPlaneClient(settings.control_plane_url, settings.control_plane_admin_token.get_secret_value())
            if settings.control_plane_admin_token
            else None
        ),
    )


def _build_gateway(settings: Settings) -> McpGateway | None:
    if settings.mcp_signing_private_key is None:
        return None
    issuer = ToolTokenIssuer(
        settings.mcp_signing_private_key.get_secret_value(), ttl_seconds=settings.mcp_token_ttl_seconds
    )
    urls: dict[ServerName, str] = {
        "knowledge": settings.mcp_knowledge_url,
        "github": settings.mcp_github_url,
        "observability": settings.mcp_observability_url,
        "operations": settings.mcp_operations_url,
    }
    return McpGateway(urls, issuer, timeout=settings.mcp_tool_timeout_seconds)
