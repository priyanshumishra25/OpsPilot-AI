"""Document lifecycle operations invoked by the API: upload, re-index, delete."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.audit import record_audit
from opspilot.auth.principal import Permission, Principal
from opspilot.db.base import Database
from opspilot.db.models import (
    Document,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    JobStatus,
    SourceType,
    VersionStatus,
)
from opspilot.errors import NotFoundError, ValidationFailedError
from opspilot.logging import get_logger
from opspilot.queue.base import JobQueue
from opspilot.rag.extraction import detect_mime_type, sanitize_filename
from opspilot.storage.objects import ObjectStore

log = get_logger(__name__)
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class SubmitResult:
    document: Document
    version: DocumentVersion
    job: IngestionJob | None
    deduplicated: bool


class IngestionService:
    def __init__(self, db: Database, store: ObjectStore, queue: JobQueue, *, max_bytes: int, max_attempts: int) -> None:
        self._db = db
        self._store = store
        self._queue = queue
        self._max_bytes = max_bytes
        self._max_attempts = max_attempts

    async def submit(
        self,
        principal: Principal,
        *,
        filename: str,
        data: bytes,
        access_scope: str,
        source_type: SourceType = SourceType.UPLOAD,
        source_uri: str | None = None,
        title: str | None = None,
        is_demo_seed: bool = False,
    ) -> SubmitResult:
        principal.require(Permission.DOCUMENTS_WRITE)
        if not _SCOPE_RE.match(access_scope):
            raise ValidationFailedError("Invalid access scope name")
        # A user may only classify documents into scopes they can themselves read.
        principal.require_scope(access_scope)
        if not data:
            raise ValidationFailedError("File is empty")
        if len(data) > self._max_bytes:
            raise ValidationFailedError(f"File exceeds the {self._max_bytes // (1024 * 1024)} MB limit")
        safe_name = sanitize_filename(filename)
        mime = detect_mime_type(safe_name, data)
        digest = hashlib.sha256(data).hexdigest()
        uri = source_uri or f"upload://{safe_name}"

        async with self._db.session() as s:
            doc = await s.scalar(
                select(Document)
                .where(
                    Document.workspace_id == principal.workspace_id,
                    Document.source_type == source_type,
                    Document.source_uri == uri,
                )
                .with_for_update()
            )
            if doc is not None:
                existing = await s.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == doc.id, DocumentVersion.content_hash == digest
                    )
                )
                if existing is not None and existing.status != VersionStatus.FAILED:
                    record_audit(
                        s,
                        action="document.upload.deduplicated",
                        resource_type="document",
                        resource_id=str(doc.id),
                        workspace_id=principal.workspace_id,
                        actor_user_id=principal.user_id,
                        details={"version_id": str(existing.id), "content_hash": digest},
                    )
                    await s.commit()
                    return SubmitResult(doc, existing, None, True)
                if doc.access_scope != access_scope:
                    principal.require_scope(doc.access_scope)
                    doc.access_scope = access_scope
                if title:
                    doc.title = title[:500]
            else:
                doc = Document(
                    workspace_id=principal.workspace_id,
                    title=(title or safe_name)[:500],
                    source_type=source_type,
                    source_uri=uri,
                    access_scope=access_scope,
                    status=DocumentStatus.PENDING,
                    created_by=principal.user_id,
                    is_demo_seed=is_demo_seed,
                )
                s.add(doc)
                await s.flush()

            next_version = (
                await s.scalar(
                    select(func.coalesce(func.max(DocumentVersion.version_number), 0)).where(
                        DocumentVersion.document_id == doc.id
                    )
                )
                or 0
            ) + 1
            key = f"workspaces/{principal.workspace_id}/documents/{doc.id}/{digest}/{safe_name}"
            await self._store.put(key, data, mime)
            version = DocumentVersion(
                document_id=doc.id,
                workspace_id=principal.workspace_id,
                version_number=next_version,
                content_hash=digest,
                object_key=key,
                mime_type=mime,
                original_filename=safe_name,
                size_bytes=len(data),
                status=VersionStatus.PENDING,
            )
            # A previously failed version with the same bytes is replaced so the user can retry.
            failed = await s.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == doc.id, DocumentVersion.content_hash == digest
                )
            )
            if failed is not None:
                await s.delete(failed)
                await s.flush()
            s.add(version)
            await s.flush()
            job = self._new_job(principal.workspace_id, doc.id, version.id, f"version:{version.id}")
            s.add(job)
            record_audit(
                s,
                action="document.upload",
                resource_type="document",
                resource_id=str(doc.id),
                workspace_id=principal.workspace_id,
                actor_user_id=principal.user_id,
                details={"version": next_version, "filename": safe_name, "bytes": len(data), "scope": access_scope},
            )
            await s.commit()
        await self._publish(job.id)
        return SubmitResult(doc, version, job, False)

    async def reindex(self, principal: Principal, document_id: uuid.UUID) -> IngestionJob:
        principal.require(Permission.DOCUMENTS_WRITE)
        async with self._db.session() as s:
            doc = await self._get_visible(s, principal, document_id)
            version = await s.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )
            if version is None:
                raise NotFoundError("Document has no versions")
            n = await s.scalar(
                select(func.count()).select_from(IngestionJob).where(IngestionJob.version_id == version.id)
            )
            job = self._new_job(principal.workspace_id, doc.id, version.id, f"version:{version.id}:reindex:{n}")
            version.status = VersionStatus.PENDING
            s.add(job)
            record_audit(
                s,
                action="document.reindex",
                resource_type="document",
                resource_id=str(doc.id),
                workspace_id=principal.workspace_id,
                actor_user_id=principal.user_id,
                details={"version_id": str(version.id)},
            )
            await s.commit()
        await self._publish(job.id)
        return job

    async def delete(self, principal: Principal, document_id: uuid.UUID) -> None:
        principal.require(Permission.DOCUMENTS_DELETE)
        async with self._db.session() as s:
            doc = await self._get_visible(s, principal, document_id)
            keys = list(
                await s.scalars(select(DocumentVersion.object_key).where(DocumentVersion.document_id == doc.id))
            )
            record_audit(
                s,
                action="document.delete",
                resource_type="document",
                resource_id=str(doc.id),
                workspace_id=principal.workspace_id,
                actor_user_id=principal.user_id,
                details={"title": doc.title, "versions": len(keys)},
            )
            # Chunks, versions and jobs cascade; the index no longer returns this document once committed.
            await s.delete(doc)
            await s.commit()
        for key in keys:
            try:
                await self._store.delete(key)
            except Exception:
                log.warning("artifact_delete_failed", object_key=key, exc_info=True)

    async def _get_visible(self, s: AsyncSession, principal: Principal, document_id: uuid.UUID) -> Document:
        doc = await s.scalar(
            select(Document).where(Document.id == document_id, Document.workspace_id == principal.workspace_id)
        )
        # Indistinguishable "not found" for other tenants' and out-of-scope documents.
        if doc is None or not principal.can_read_scope(doc.access_scope):
            raise NotFoundError("Document not found")
        return doc

    def _new_job(self, workspace_id: uuid.UUID, doc_id: uuid.UUID, version_id: uuid.UUID, key: str) -> IngestionJob:
        return IngestionJob(
            workspace_id=workspace_id,
            document_id=doc_id,
            version_id=version_id,
            idempotency_key=key,
            status=JobStatus.QUEUED,
            stage="queued",
            attempts=0,
            max_attempts=self._max_attempts,
        )

    async def _publish(self, job_id: uuid.UUID) -> None:
        try:
            await self._queue.publish(str(job_id))
        except Exception:
            log.warning("queue_publish_failed", job_id=str(job_id), exc_info=True)
