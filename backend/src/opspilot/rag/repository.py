"""Retrieval SQL. Tenant and access-scope predicates are applied inside each candidate query,
before ranking and LIMIT, so unauthorised chunks can never occupy a candidate slot."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, and_, func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.db.models import Chunk, Document, DocumentStatus
from opspilot.rag.types import AccessContext, RetrievedChunk, SearchFilters


def effective_scopes(access: AccessContext, filters: SearchFilters) -> list[str]:
    if filters.access_scopes:
        return sorted(access.access_scopes.intersection(filters.access_scopes))
    return sorted(access.access_scopes)


def _visible[S: Select[*tuple[Any, ...]]](stmt: S, access: AccessContext, filters: SearchFilters) -> S:
    stmt = stmt.join(
        Document,
        and_(Document.id == Chunk.document_id, Document.current_version_id == Chunk.version_id),
    ).where(
        Chunk.workspace_id == access.workspace_id,
        Document.workspace_id == access.workspace_id,
        Document.status == DocumentStatus.INDEXED,
        Chunk.access_scope.in_(effective_scopes(access, filters)),
    )
    if filters.document_ids:
        stmt = stmt.where(Chunk.document_id.in_(filters.document_ids))
    if filters.source_types:
        stmt = stmt.where(Document.source_type.in_(filters.source_types))
    return stmt


async def dense_search(
    session: AsyncSession, access: AccessContext, filters: SearchFilters, query_vec: list[float], k: int
) -> list[tuple[uuid.UUID, float]]:
    if not effective_scopes(access, filters):
        return []
    # Iterative scans keep filtered HNSW queries from returning fewer than k rows (pgvector >= 0.8).
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = Chunk.embedding.cosine_distance(query_vec).label("distance")
    inner = (
        _visible(select(Chunk.id, Chunk.content_hash, distance), access, filters)
        .order_by(distance, Chunk.content_hash)
        .limit(k)
        .subquery()
    )
    # relaxed_order may return slightly out-of-order rows; re-sort exactly.
    rows = await session.execute(select(inner.c.id, inner.c.distance).order_by(inner.c.distance, inner.c.content_hash))
    return [(r.id, float(r.distance)) for r in rows]


async def lexical_search(
    session: AsyncSession, access: AccessContext, filters: SearchFilters, lexical_query: str, k: int
) -> list[tuple[uuid.UUID, float]]:
    if not lexical_query or not effective_scopes(access, filters):
        return []
    tsq = func.websearch_to_tsquery(literal_column("'english'::regconfig"), lexical_query)
    rank = func.ts_rank_cd(Chunk.tsv, tsq, 32).label("rank")
    stmt = _visible(select(Chunk.id, rank), access, filters).where(Chunk.tsv.op("@@")(tsq))
    # Ties are common in ts_rank_cd; break them on content so results are reproducible across re-ingestion.
    rows = await session.execute(stmt.order_by(rank.desc(), Chunk.content_hash, Chunk.ordinal).limit(k))
    return [(r.id, float(r.rank)) for r in rows]


async def hydrate(
    session: AsyncSession, access: AccessContext, filters: SearchFilters, chunk_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RetrievedChunk]:
    """Load full chunk records, re-applying the visibility predicate (defence in depth)."""
    if not chunk_ids:
        return {}
    stmt = _visible(select(Chunk, Document.title, Document.source_type, Document.source_uri), access, filters).where(
        Chunk.id.in_(chunk_ids)
    )
    out: dict[uuid.UUID, RetrievedChunk] = {}
    for chunk, title, source_type, source_uri in (await session.execute(stmt)).all():
        out[chunk.id] = RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            version_id=chunk.version_id,
            document_title=title,
            source_type=str(source_type),
            source_uri=source_uri,
            section_path=list(chunk.section_path or []),
            content=chunk.content,
            content_hash=chunk.content_hash,
            page_number=chunk.page_number,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            token_estimate=chunk.token_estimate,
            access_scope=chunk.access_scope,
        )
    return out
