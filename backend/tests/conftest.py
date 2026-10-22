"""Test fixtures. Postgres (pgvector) and Redis are real containers via testcontainers, unless
OPSPILOT_TEST_DATABASE_URL / OPSPILOT_TEST_REDIS_URL point at existing instances."""

from __future__ import annotations

import os

# Cheap Argon2 parameters for tests only; must be set before opspilot.auth.passwords is imported.
os.environ.setdefault("OPSPILOT_ARGON2_PROFILE", "test")
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text

from opspilot.api.app import create_app
from opspilot.approvals.service import ApprovalService
from opspilot.auth.passwords import ahash_password
from opspilot.auth.principal import Principal
from opspilot.auth.tokens import TokenService
from opspilot.config import Settings
from opspilot.container import Container
from opspilot.db.base import Database
from opspilot.db.models import Membership, Role, User, Workspace
from opspilot.ingestion.service import IngestionService
from opspilot.ingestion.worker import IngestionWorker
from opspilot.rag.chunking import Chunker
from opspilot.rag.cost import ModelPricing
from opspilot.rag.generation import GroundedAnswerer
from opspilot.rag.retriever import HybridRetriever, RetrievalConfig
from tests.fakes import HashingEmbeddings, InMemoryObjectStore, InMemoryQueue

BACKEND = Path(__file__).resolve().parents[1]
TEST_JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    if url := os.environ.get("OPSPILOT_TEST_DATABASE_URL"):
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:0.8.1-pg17", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    if url := os.environ.get("OPSPILOT_TEST_REDIS_URL"):
        yield url
        return
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:7.4-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def migrated(pg_url: str) -> str:
    env = {**os.environ, "OPSPILOT_DATABASE_URL": pg_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True
    )
    return pg_url


@pytest.fixture
def settings(migrated: str, redis_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=migrated,
        redis_url=redis_url,
        jwt_secret=SecretStr(TEST_JWT_SECRET),
        reranker="none",
        rate_limit_login_per_minute=1000,
        rate_limit_query_per_minute=1000,
        max_upload_bytes=256 * 1024,
        ingestion_backoff_base_seconds=0.0,
        ingestion_backoff_max_seconds=0.0,
    )


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.database_url, pool_size=5)
    yield database
    async with database.session() as s:
        await s.execute(
            text(
                "TRUNCATE chunks, ingestion_jobs, document_versions, documents, rag_queries, audit_log, "
                "memberships, users, workspaces RESTART IDENTITY CASCADE"
            )
        )
        await s.commit()
    await database.dispose()


@dataclass
class Env:
    container: Container
    store: InMemoryObjectStore
    queue: InMemoryQueue
    embeddings: HashingEmbeddings
    worker: IngestionWorker

    async def drain(self) -> None:
        """Process every published job synchronously (stands in for the worker loop)."""
        while self.queue.published:
            job_id = self.queue.published.pop(0)
            await self.worker.process(uuid.UUID(job_id))


@pytest.fixture
async def env(settings: Settings, db: Database) -> AsyncIterator[Env]:
    store, queue, embeddings = InMemoryObjectStore(), InMemoryQueue(), HashingEmbeddings()
    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    chunker = Chunker(chunk_size=settings.chunk_size_chars, chunk_overlap=settings.chunk_overlap_chars)
    retriever = HybridRetriever(db, embeddings, None, RetrievalConfig(abstain_max_dense_distance=2.0))
    answerer = GroundedAnswerer(
        None,
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=1000,
        pricing=ModelPricing("claude-opus-5-5", 4.0, 20.0, 0.2, 5.0),
    )
    container = Container(
        settings=settings,
        db=db,
        redis=redis,
        store=store,
        queue=queue,
        embeddings=embeddings,  # type: ignore[arg-type]
        reranker=None,
        retriever=retriever,
        answerer=answerer,
        ingestion=IngestionService(db, store, queue, max_bytes=settings.max_upload_bytes, max_attempts=3),
        tokens=TokenService(
            TEST_JWT_SECRET, issuer=settings.jwt_issuer, audience=settings.jwt_audience, ttl_seconds=600
        ),
        chunker=chunker,
        approvals=ApprovalService(),
    )
    worker = IngestionWorker(
        db,
        store,
        queue,
        embeddings,
        chunker,
        max_pdf_pages=50,
        backoff_base=0.0,
        backoff_cap=0.0,
        stale_after_seconds=600,
    )
    yield Env(container, store, queue, embeddings, worker)
    await redis.aclose()


async def make_member(
    db: Database, *, email: str, workspace_slug: str, role: Role = Role.ENGINEER, scopes: list[str] | None = None
) -> Principal:
    async with db.session() as s:
        ws = (await s.execute(text("SELECT id FROM workspaces WHERE slug = :s"), {"s": workspace_slug})).scalar()
        if ws is None:
            w = Workspace(slug=workspace_slug, name=workspace_slug, settings={})
            s.add(w)
            await s.flush()
            ws = w.id
        user = User(email=email, display_name=email.split("@")[0], password_hash=await ahash_password(TEST_PASSWORD))
        s.add(user)
        await s.flush()
        m = Membership(user_id=user.id, workspace_id=ws, role=role, access_scopes=scopes or ["general"])
        s.add(m)
        await s.commit()
        return Principal(user.id, email, ws, role, frozenset(m.access_scopes))


@pytest.fixture
async def client(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(env.container.settings, env.container)
    app.state.container = env.container
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
