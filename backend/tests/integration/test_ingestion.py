import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from opspilot.db.models import (
    AuditLog,
    Chunk,
    Document,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    JobStatus,
    Role,
    VersionStatus,
)
from opspilot.errors import PermissionDeniedError, ValidationFailedError
from opspilot.ingestion.worker import IngestionWorker
from tests.conftest import Env, make_member
from tests.fakes import FailingEmbeddings

pytestmark = pytest.mark.integration
DOCS = Path(__file__).resolve().parents[3] / "demo" / "docs"
RUNBOOK = (DOCS / "checkout-db-connection-runbook.md").read_bytes()


async def _count(env: Env, model: type, *where: object) -> int:
    async with env.container.db.session() as s:
        return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_upload_ingest_and_index(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    res = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    assert res.job is not None and not res.deduplicated
    assert env.queue.published == [str(res.job.id)]
    await env.drain()
    async with env.container.db.session() as s:
        doc = await s.get(Document, res.document.id)
        job = await s.get(IngestionJob, res.job.id)
        assert doc is not None and job is not None
        assert doc.status == DocumentStatus.INDEXED
        assert doc.current_version_id == res.version.id
        assert doc.title == "Checkout Service Runbook: Database Connection Failure"
        assert job.status == JobStatus.SUCCEEDED and job.attempts == 1
        assert job.timings_ms["chunks"] == await _count(env, Chunk, Chunk.document_id == doc.id)
        assert (
            await s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "document.upload")) == 1
        )


async def test_duplicate_upload_is_deduplicated(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    first = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    await env.drain()
    again = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    assert again.deduplicated and again.job is None and again.version.id == first.version.id
    assert env.queue.published == []


async def test_new_version_replaces_chunks_and_reuses_embeddings(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    v1 = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    await env.drain()
    embedded_v1 = env.embeddings.texts_embedded
    changed = RUNBOOK.replace(b"default 40", b"default 48")
    v2 = await env.container.ingestion.submit(p, filename="checkout.md", data=changed, access_scope="general")
    assert v2.document.id == v1.document.id and v2.version.version_number == 2
    await env.drain()
    # Only the edited section is re-embedded; unchanged passages reuse stored vectors.
    assert env.embeddings.texts_embedded - embedded_v1 == 1
    async with env.container.db.session() as s:
        old = await s.get(DocumentVersion, v1.version.id)
        doc = await s.get(Document, v1.document.id)
        assert old is not None and old.status == VersionStatus.SUPERSEDED
        assert doc is not None and doc.current_version_id == v2.version.id
    assert await _count(env, Chunk, Chunk.version_id == v1.version.id) == 0
    contents = await _contents(env, v2.version.id)
    assert any("default 48" in c for c in contents) and not any("default 40" in c for c in contents)


async def _contents(env: Env, version_id: uuid.UUID) -> list[str]:
    async with env.container.db.session() as s:
        return list(await s.scalars(select(Chunk.content).where(Chunk.version_id == version_id)))


async def test_job_claim_is_idempotent(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    res = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    assert res.job is not None
    assert await env.worker.process(res.job.id) == JobStatus.SUCCEEDED
    # A duplicate delivery of the same message is a no-op.
    assert await env.worker.process(res.job.id) is None
    chunks = await _count(env, Chunk, Chunk.document_id == res.document.id)
    assert chunks == len(await _contents(env, res.version.id))


async def test_transient_failure_retries_then_dead_letters(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    res = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    assert res.job is not None
    env.queue.published.clear()
    worker = IngestionWorker(
        env.container.db,
        env.store,
        env.queue,
        FailingEmbeddings(failures=10),
        env.container.chunker,
        max_pdf_pages=10,
        backoff_base=0,
        backoff_cap=0,
        stale_after_seconds=600,
    )
    assert await worker.process(res.job.id) == JobStatus.RETRY_SCHEDULED
    assert await worker.process(res.job.id) == JobStatus.RETRY_SCHEDULED
    assert await worker.process(res.job.id) == JobStatus.DEAD  # max_attempts=3
    assert env.queue.dead_letters and env.queue.dead_letters[0][0] == str(res.job.id)
    async with env.container.db.session() as s:
        job = await s.get(IngestionJob, res.job.id)
        doc = await s.get(Document, res.document.id)
        assert job is not None and job.attempts == 3 and job.error_class == "UpstreamUnavailableError"
        assert doc is not None and doc.status == DocumentStatus.FAILED


async def test_transient_failure_then_recovery(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    res = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    assert res.job is not None
    env.queue.published.clear()
    env.store.fail_gets = 1
    assert await env.worker.process(res.job.id) == JobStatus.RETRY_SCHEDULED
    # The sweeper republishes due retries (stands in for a lost or delayed message).
    async with env.container.db.session() as s:
        job = await s.get(IngestionJob, res.job.id)
        assert job is not None
        job.created_at = job.created_at.replace(year=2000)
        await s.commit()
    assert await env.worker.sweep() == 1
    await env.drain()
    async with env.container.db.session() as s:
        job = await s.get(IngestionJob, res.job.id)
        assert job is not None and job.status == JobStatus.SUCCEEDED and job.attempts == 2


async def test_unparseable_file_fails_permanently_without_retry(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    res = await env.container.ingestion.submit(
        p, filename="broken.pdf", data=b"%PDF-1.4\nnot really", access_scope="general"
    )
    await env.drain()
    async with env.container.db.session() as s:
        job = await s.get(IngestionJob, res.job.id)  # type: ignore[union-attr]
        version = await s.get(DocumentVersion, res.version.id)
        assert job is not None and job.status == JobStatus.FAILED and job.attempts == 1
        assert version is not None and version.status == VersionStatus.FAILED and version.error
    assert env.queue.dead_letters == []


async def test_delete_removes_chunks_and_artifacts(env: Env) -> None:
    p = await make_member(env.container.db, email="admin@a.test", workspace_slug="acme", role=Role.ADMIN)
    res = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    await env.drain()
    assert env.store.objects
    await env.container.ingestion.delete(p, res.document.id)
    assert await _count(env, Chunk, Chunk.document_id == res.document.id) == 0
    assert await _count(env, Document, Document.id == res.document.id) == 0
    assert env.store.objects == {}
    assert await _count(env, AuditLog, AuditLog.action == "document.delete") == 1


async def test_reindex_creates_new_job(env: Env) -> None:
    p = await make_member(env.container.db, email="eng@a.test", workspace_slug="acme")
    res = await env.container.ingestion.submit(p, filename="checkout.md", data=RUNBOOK, access_scope="general")
    await env.drain()
    job = await env.container.ingestion.reindex(p, res.document.id)
    await env.drain()
    async with env.container.db.session() as s:
        refreshed = await s.get(IngestionJob, job.id)
        assert refreshed is not None and refreshed.status == JobStatus.SUCCEEDED
    assert await _count(env, Chunk, Chunk.document_id == res.document.id) > 0


async def test_upload_authorisation_and_limits(env: Env) -> None:
    viewer = await make_member(env.container.db, email="v@a.test", workspace_slug="acme", role=Role.VIEWER)
    with pytest.raises(PermissionDeniedError):
        await env.container.ingestion.submit(viewer, filename="a.md", data=b"# a", access_scope="general")
    eng = await make_member(env.container.db, email="e@a.test", workspace_slug="acme")
    with pytest.raises(PermissionDeniedError):  # cannot classify into a scope you cannot read
        await env.container.ingestion.submit(eng, filename="a.md", data=b"# a", access_scope="sre-restricted")
    with pytest.raises(ValidationFailedError):
        await env.container.ingestion.submit(eng, filename="a.md", data=b"x" * (300 * 1024), access_scope="general")
    with pytest.raises(ValidationFailedError):
        await env.container.ingestion.submit(eng, filename="a.md", data=b"# a", access_scope="Bad Scope!")
    with pytest.raises(ValidationFailedError):
        await env.container.ingestion.submit(eng, filename="a.html", data=b"<script>", access_scope="general")
