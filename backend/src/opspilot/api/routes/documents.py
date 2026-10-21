from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.api.deps import ContainerDep, PrincipalDep, SessionDep
from opspilot.api.schemas import ChunkOut, DocumentDetailOut, DocumentOut, JobOut, UploadOut, VersionOut
from opspilot.auth.principal import Permission, Principal
from opspilot.db.models import Chunk, Document, DocumentVersion, IngestionJob, JobStatus
from opspilot.errors import NotFoundError, ValidationFailedError

router = APIRouter(prefix="/api/v1", tags=["documents"])


async def _latest_jobs(session: AsyncSession, doc_ids: list[uuid.UUID]) -> dict[uuid.UUID, IngestionJob]:
    if not doc_ids:
        return {}
    rows = await session.scalars(
        select(IngestionJob)
        .where(IngestionJob.document_id.in_(doc_ids))
        .ext(distinct_on(IngestionJob.document_id))
        .order_by(IngestionJob.document_id, IngestionJob.created_at.desc())
    )
    return {j.document_id: j for j in rows}


def _doc_out(doc: Document, version: DocumentVersion | None, job: IngestionJob | None) -> dict:  # type: ignore[type-arg]
    return {
        "id": doc.id,
        "title": doc.title,
        "source_type": str(doc.source_type),
        "source_uri": doc.source_uri,
        "access_scope": doc.access_scope,
        "status": str(doc.status),
        "is_demo_seed": doc.is_demo_seed,
        "current_version": VersionOut.model_validate(version) if version else None,
        "latest_job": JobOut.model_validate(job) if job else None,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


async def _visible_doc(session: AsyncSession, principal: Principal, document_id: uuid.UUID) -> Document:
    doc = await session.scalar(
        select(Document).where(Document.id == document_id, Document.workspace_id == principal.workspace_id)
    )
    if doc is None or not principal.can_read_scope(doc.access_scope):
        raise NotFoundError("Document not found")
    return doc


@router.post("/documents", response_model=UploadOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    principal: PrincipalDep,
    container: ContainerDep,
    file: UploadFile,
    access_scope: Annotated[str, Form()] = "general",
    title: Annotated[str | None, Form(max_length=500)] = None,
) -> UploadOut:
    limit = container.settings.max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailedError(f"File exceeds the {limit // (1024 * 1024)} MB limit")
    result = await container.ingestion.submit(
        principal, filename=file.filename or "upload", data=data, access_scope=access_scope, title=title
    )
    return UploadOut(
        document_id=result.document.id,
        version_id=result.version.id,
        job_id=result.job.id if result.job else None,
        deduplicated=result.deduplicated,
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    principal: PrincipalDep,
    session: SessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[dict]:  # type: ignore[type-arg]
    principal.require(Permission.DOCUMENTS_READ)
    stmt = (
        select(Document, DocumentVersion)
        .outerjoin(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(Document.workspace_id == principal.workspace_id, Document.access_scope.in_(principal.access_scopes))
        .order_by(Document.updated_at.desc())
        .limit(500)
    )
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    rows = (await session.execute(stmt)).all()
    jobs = await _latest_jobs(session, [d.id for d, _ in rows])
    return [_doc_out(d, v, jobs.get(d.id)) for d, v in rows]


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
async def get_document(document_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> dict:  # type: ignore[type-arg]
    principal.require(Permission.DOCUMENTS_READ)
    doc = await _visible_doc(session, principal, document_id)
    versions = list(
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
        )
    )
    jobs = list(
        await session.scalars(
            select(IngestionJob)
            .where(IngestionJob.document_id == doc.id)
            .order_by(IngestionJob.created_at.desc())
            .limit(20)
        )
    )
    current = next((v for v in versions if v.id == doc.current_version_id), None)
    return {
        **_doc_out(doc, current, jobs[0] if jobs else None),
        "versions": [VersionOut.model_validate(v) for v in versions],
        "jobs": [JobOut.model_validate(j) for j in jobs],
    }


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkOut])
async def document_chunks(document_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> list[ChunkOut]:
    """Indexed passages of the live version, in document order (used for previews)."""
    principal.require(Permission.DOCUMENTS_READ)
    doc = await _visible_doc(session, principal, document_id)
    if doc.current_version_id is None:
        return []
    chunks = await session.scalars(
        select(Chunk).where(Chunk.version_id == doc.current_version_id).order_by(Chunk.ordinal)
    )
    return [_chunk_out(c, doc) for c in chunks]


@router.get("/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> ChunkOut:
    """Resolve a citation. Same permission predicate as retrieval; out-of-scope chunks are 404."""
    principal.require(Permission.DOCUMENTS_READ)
    row = (
        await session.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.id == chunk_id,
                Chunk.workspace_id == principal.workspace_id,
                Document.workspace_id == principal.workspace_id,
                Chunk.access_scope.in_(principal.access_scopes),
                Document.access_scope.in_(principal.access_scopes),
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Passage not found")
    return _chunk_out(row[0], row[1])


def _chunk_out(c: Chunk, doc: Document) -> ChunkOut:
    return ChunkOut(
        id=c.id,
        document_id=c.document_id,
        document_title=doc.title,
        version_id=c.version_id,
        ordinal=c.ordinal,
        section_path=list(c.section_path or []),
        content=c.content,
        page_number=c.page_number,
        char_start=c.char_start,
        char_end=c.char_end,
        source_uri=doc.source_uri,
    )


@router.post("/documents/{document_id}/reindex", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(document_id: uuid.UUID, principal: PrincipalDep, container: ContainerDep) -> IngestionJob:
    return await container.ingestion.reindex(principal, document_id)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, principal: PrincipalDep, container: ContainerDep) -> None:
    await container.ingestion.delete(principal, document_id)


@router.get("/ingestion/jobs", response_model=list[JobOut])
async def list_jobs(
    principal: PrincipalDep,
    session: SessionDep,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[IngestionJob]:
    principal.require(Permission.DOCUMENTS_READ)
    stmt = (
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(IngestionJob.workspace_id == principal.workspace_id, Document.access_scope.in_(principal.access_scopes))
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(IngestionJob.status == status_filter)
    return list(await session.scalars(stmt))
