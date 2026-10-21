"""Ingestion worker: claims jobs, extracts, chunks, embeds and atomically publishes a document version."""

from __future__ import annotations

import asyncio
import random
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta

from langchain_core.embeddings import Embeddings
from opentelemetry import trace
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.db.base import Database
from opspilot.db.models import (
    Chunk,
    Document,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    JobStatus,
    VersionStatus,
)
from opspilot.errors import ExtractionError, NotFoundError, ValidationFailedError
from opspilot.logging import get_logger
from opspilot.observability.metrics import INGESTION_JOBS, INGESTION_STAGE_SECONDS
from opspilot.queue.base import JobQueue, QueueMessage
from opspilot.rag.chunking import ChunkDraft, Chunker
from opspilot.rag.extraction import extract
from opspilot.storage.objects import ObjectStore

log = get_logger(__name__)
tracer = trace.get_tracer(__name__)

PERMANENT_ERRORS = (ExtractionError, ValidationFailedError, NotFoundError)


def backoff_seconds(attempt: int, base: float, cap: float, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter: uniform(0, min(cap, base * 2**(attempt-1)))."""
    r = rng or random
    return r.uniform(0, min(cap, base * (2 ** max(0, attempt - 1))))


class IngestionWorker:
    def __init__(
        self,
        db: Database,
        store: ObjectStore,
        queue: JobQueue,
        embeddings: Embeddings,
        chunker: Chunker,
        *,
        max_pdf_pages: int,
        backoff_base: float,
        backoff_cap: float,
        stale_after_seconds: int,
        concurrency: int = 2,
    ) -> None:
        self._db = db
        self._store = store
        self._queue = queue
        self._embeddings = embeddings
        self._chunker = chunker
        self._max_pdf_pages = max_pdf_pages
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._stale = stale_after_seconds
        self._sem = asyncio.Semaphore(concurrency)
        self._stopping = asyncio.Event()

    # ---- job processing -------------------------------------------------------------------------

    async def claim(self, job_id: uuid.UUID) -> IngestionJob | None:
        """Atomically move a due job to RUNNING. Returns None if another worker owns it or it's not due."""
        async with self._db.session() as s:
            job = await s.scalar(
                update(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY_SCHEDULED]),
                    IngestionJob.available_at <= datetime.now(UTC),
                )
                .values(
                    status=JobStatus.RUNNING,
                    attempts=IngestionJob.attempts + 1,
                    started_at=datetime.now(UTC),
                    stage="extracting",
                    error=None,
                )
                .returning(IngestionJob)
            )
            await s.commit()
            return job

    async def process(self, job_id: uuid.UUID) -> JobStatus | None:
        job = await self.claim(job_id)
        if job is None:
            return None
        timings: dict[str, float] = {}
        with tracer.start_as_current_span("ingestion.process_job") as span:
            span.set_attribute("ingestion.job_id", str(job.id))
            span.set_attribute("ingestion.attempt", job.attempts)
            try:
                status = await self._run(job, timings)
            except PERMANENT_ERRORS as exc:
                status = await self._fail(job, exc, permanent=True, timings=timings)
            except Exception as exc:
                log.exception("ingestion_job_error", job_id=str(job.id))
                status = await self._fail(job, exc, permanent=False, timings=timings)
            span.set_attribute("ingestion.status", status.value)
        INGESTION_JOBS.labels(status=status.value).inc()
        return status

    async def _stage(self, job_id: uuid.UUID, stage: str) -> None:
        async with self._db.session() as s:
            await s.execute(update(IngestionJob).where(IngestionJob.id == job_id).values(stage=stage))
            await s.commit()

    async def _run(self, job: IngestionJob, timings: dict[str, float]) -> JobStatus:
        async with self._db.session() as s:
            version = await s.get(DocumentVersion, job.version_id)
            doc = await s.get(Document, job.document_id)
            if version is None or doc is None or version.workspace_id != job.workspace_id:
                raise NotFoundError("Document or version no longer exists")
            version.status = VersionStatus.PROCESSING
            await s.commit()
            title, object_key, mime, filename = (
                doc.title,
                version.object_key,
                version.mime_type,
                version.original_filename,
            )

        t = time.perf_counter()
        data = await self._store.get(object_key)
        extracted = await asyncio.to_thread(extract, filename, data, mime, max_pdf_pages=self._max_pdf_pages)
        drafts = self._chunker.chunk(extracted)
        if _is_filename_title(title) and extracted.title:
            title = extracted.title[:500]
        if not drafts:
            raise ExtractionError("No indexable content after chunking")
        timings["extract_chunk_ms"] = _elapsed(t)
        INGESTION_STAGE_SECONDS.labels(stage="extract_chunk").observe(timings["extract_chunk_ms"] / 1000)

        await self._stage(job.id, "embedding")
        t = time.perf_counter()
        vectors, reused = await self._embed(job.workspace_id, drafts, title)
        timings["embed_ms"] = _elapsed(t)
        INGESTION_STAGE_SECONDS.labels(stage="embed").observe(timings["embed_ms"] / 1000)

        await self._stage(job.id, "indexing")
        t = time.perf_counter()
        async with self._db.session() as s:
            await self._publish_version(s, job, drafts, vectors, title)
            await s.execute(
                update(IngestionJob)
                .where(IngestionJob.id == job.id)
                .values(
                    status=JobStatus.SUCCEEDED,
                    stage="done",
                    finished_at=datetime.now(UTC),
                    timings_ms={**timings, "index_ms": _elapsed(t), "chunks": len(drafts), "embeddings_reused": reused},
                )
            )
            await s.commit()
        log.info("ingestion_job_succeeded", job_id=str(job.id), chunks=len(drafts), embeddings_reused=reused)
        return JobStatus.SUCCEEDED

    async def _embed(
        self, workspace_id: uuid.UUID, drafts: list[ChunkDraft], title: str
    ) -> tuple[list[list[float]], int]:
        """Embed chunks, reusing vectors for identical passages already indexed in this workspace."""
        hashes = list({d.content_hash for d in drafts})
        async with self._db.session() as s:
            rows = await s.execute(
                select(Chunk.content_hash, Chunk.embedding, Chunk.section_path, Document.title)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.workspace_id == workspace_id, Chunk.content_hash.in_(hashes))
            )
            # Reuse only when the embedding input (title + section path + content) is identical.
            cache = {(h, tuple(p or []), t): list(e) for h, e, p, t in rows}
        out: list[list[float] | None] = []
        missing: list[int] = []
        for i, d in enumerate(drafts):
            hit = cache.get((d.content_hash, tuple(d.section_path), title))
            out.append(hit)
            if hit is None:
                missing.append(i)
        if missing:
            fresh = await self._embeddings.aembed_documents([drafts[i].embedding_text(title) for i in missing])
            for i, v in zip(missing, fresh, strict=True):
                out[i] = v
        return [v for v in out if v is not None], len(drafts) - len(missing)

    async def _publish_version(
        self,
        s: AsyncSession,
        job: IngestionJob,
        drafts: list[ChunkDraft],
        vectors: list[list[float]],
        extracted_title: str,
    ) -> None:
        doc = await s.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        version = await s.get(DocumentVersion, job.version_id)
        if doc is None or version is None:
            raise NotFoundError("Document was deleted during ingestion")
        # Idempotent: a retried job replaces any chunks written for this version.
        await s.execute(delete(Chunk).where(Chunk.version_id == version.id))
        s.add_all(
            Chunk(
                workspace_id=job.workspace_id,
                document_id=doc.id,
                version_id=version.id,
                ordinal=d.ordinal,
                section_heading=d.section_heading,
                section_path=d.section_path,
                content=d.content,
                content_hash=d.content_hash,
                token_estimate=d.token_estimate,
                page_number=d.page_number,
                char_start=d.char_start,
                char_end=d.char_end,
                access_scope=doc.access_scope,
                embedding=v,
            )
            for d, v in zip(drafts, vectors, strict=True)
        )
        version.status = VersionStatus.INDEXED
        version.chunk_count = len(drafts)
        version.indexed_at = datetime.now(UTC)
        version.error = None

        current = await s.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
        if current is None or current.id == version.id or current.version_number < version.version_number:
            if current is not None and current.id != version.id:
                current.status = VersionStatus.SUPERSEDED
                await s.execute(delete(Chunk).where(Chunk.version_id == current.id))
            doc.current_version_id = version.id
            if _is_filename_title(doc.title):
                doc.title = extracted_title
        else:
            # An older version finished after a newer one; keep the newer one live.
            version.status = VersionStatus.SUPERSEDED
            await s.execute(delete(Chunk).where(Chunk.version_id == version.id))
        doc.status = DocumentStatus.INDEXED
        # Scope may have changed on re-upload; keep denormalised chunk scope consistent.
        await s.execute(update(Chunk).where(Chunk.document_id == doc.id).values(access_scope=doc.access_scope))

    async def _fail(
        self, job: IngestionJob, exc: Exception, *, permanent: bool, timings: dict[str, float]
    ) -> JobStatus:
        message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        exhausted = job.attempts >= job.max_attempts
        if permanent:
            status = JobStatus.FAILED
        elif exhausted:
            status = JobStatus.DEAD
        else:
            status = JobStatus.RETRY_SCHEDULED
        delay = (
            0.0
            if status != JobStatus.RETRY_SCHEDULED
            else backoff_seconds(job.attempts, self._backoff_base, self._backoff_cap)
        )
        async with self._db.session() as s:
            await s.execute(
                update(IngestionJob)
                .where(IngestionJob.id == job.id)
                .values(
                    status=status,
                    stage="failed" if status != JobStatus.RETRY_SCHEDULED else "retry_wait",
                    error=message[:4000],
                    error_class=exc.__class__.__name__,
                    available_at=datetime.now(UTC) + timedelta(seconds=delay),
                    finished_at=datetime.now(UTC) if status != JobStatus.RETRY_SCHEDULED else None,
                    timings_ms=timings,
                )
            )
            if status in (JobStatus.FAILED, JobStatus.DEAD):
                version = await s.get(DocumentVersion, job.version_id)
                if version is not None:
                    version.status = VersionStatus.FAILED
                    version.error = message[:4000]
                doc = await s.get(Document, job.document_id)
                if doc is not None and doc.current_version_id is None:
                    doc.status = DocumentStatus.FAILED
            await s.commit()
        if status == JobStatus.DEAD:
            await self._queue.publish_dead_letter(str(job.id), message)
        log.warning(
            "ingestion_job_failed", job_id=str(job.id), status=status.value, error=message, retry_in_s=round(delay, 2)
        )
        return status

    # ---- loop -----------------------------------------------------------------------------------

    async def sweep(self) -> int:
        """Republish due jobs (covers lost messages and scheduled retries) and recover stale RUNNING jobs."""
        now = datetime.now(UTC)
        async with self._db.session() as s:
            await s.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.status == JobStatus.RUNNING,
                    IngestionJob.started_at < now - timedelta(seconds=self._stale),
                )
                .values(status=JobStatus.RETRY_SCHEDULED, stage="retry_wait", error="Worker lease expired")
            )
            due = list(
                await s.scalars(
                    select(IngestionJob.id)
                    .where(
                        IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RETRY_SCHEDULED]),
                        IngestionJob.available_at <= now,
                        IngestionJob.created_at < now - timedelta(seconds=5),
                    )
                    .limit(100)
                )
            )
            await s.commit()
        for job_id in due:
            await self._queue.publish(str(job_id))
        return len(due)

    async def _handle(self, msg: QueueMessage) -> None:
        async with self._sem:
            try:
                await self.process(uuid.UUID(msg.job_id))
            except ValueError:
                log.warning("invalid_job_message", job_id=msg.job_id)
            finally:
                await self._queue.ack(msg)

    async def run_forever(self, *, sweep_interval: float = 15.0) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stopping.set)
        in_flight: set[asyncio.Task[None]] = set()
        last_sweep = 0.0
        log.info("ingestion_worker_started")
        while not self._stopping.is_set():
            if time.monotonic() - last_sweep > sweep_interval:
                try:
                    await self.sweep()
                except Exception:
                    log.exception("ingestion_sweep_failed")
                last_sweep = time.monotonic()
            try:
                messages = await self._queue.receive(max_messages=4, wait_seconds=2)
            except Exception:
                log.exception("queue_receive_failed")
                await asyncio.sleep(2)
                continue
            for msg in messages:
                task = asyncio.create_task(self._handle(msg))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        log.info("ingestion_worker_draining", in_flight=len(in_flight))
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        log.info("ingestion_worker_stopped")


def _is_filename_title(title: str) -> bool:
    return title.lower().endswith((".md", ".markdown", ".txt", ".pdf"))


def _elapsed(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 2)
