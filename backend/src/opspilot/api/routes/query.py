from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select, update
from sse_starlette import EventSourceResponse

from opspilot.api.deps import ContainerDep, PrincipalDep, SessionDep, enforce_rate_limit
from opspilot.api.schemas import AuditOut, FiltersIn, QueryRequest, RagQueryOut, SearchRequest
from opspilot.audit import current_trace_id
from opspilot.auth.principal import Permission, Principal
from opspilot.container import Container
from opspilot.db.models import AuditLog, RagQuery
from opspilot.errors import NotConfiguredError, NotFoundError, OpsPilotError
from opspilot.logging import get_logger
from opspilot.observability.metrics import LLM_COST_USD, LLM_TOKENS, RETRIEVAL_SECONDS
from opspilot.rag.generation import AnswerOutcome
from opspilot.rag.types import AccessContext, SearchFilters

router = APIRouter(prefix="/api/v1", tags=["query"])
log = get_logger(__name__)


def access_context(principal: Principal) -> AccessContext:
    return AccessContext(workspace_id=principal.workspace_id, access_scopes=principal.access_scopes)


def to_filters(f: FiltersIn) -> SearchFilters:
    return SearchFilters(
        document_ids=tuple(f.document_ids), source_types=tuple(f.source_types), access_scopes=tuple(f.access_scopes)
    )


@router.post("/search")
async def search(body: SearchRequest, principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    """Retrieval only (no generation): returns ranked passages and full diagnostics."""
    principal.require(Permission.QUERY)
    await enforce_rate_limit(container, "query", str(principal.user_id), container.settings.rate_limit_query_per_minute)
    start = time.perf_counter()
    result = await container.retriever.retrieve(
        body.query,
        access_context(principal),
        filters=to_filters(body.filters),
        strategy=body.strategy,
        rerank=body.rerank,
        final_k=body.k,
    )
    RETRIEVAL_SECONDS.labels(strategy=body.strategy).observe(time.perf_counter() - start)
    return {"query": body.query, "trace_id": current_trace_id(), **result.diagnostics()}


@router.post("/query")
async def query(body: QueryRequest, principal: PrincipalDep, container: ContainerDep) -> EventSourceResponse:
    """Grounded answer, streamed as Server-Sent Events: retrieval → text/citation deltas → done."""
    principal.require(Permission.QUERY)
    await enforce_rate_limit(container, "query", str(principal.user_id), container.settings.rate_limit_query_per_minute)
    async with container.db.session() as s:
        record = RagQuery(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            query=body.query,
            status="running",
            trace_id=current_trace_id(),
        )
        s.add(record)
        await s.commit()
        query_id = record.id
    return EventSourceResponse(_answer_stream(container, principal, body, query_id), ping=15)


async def _answer_stream(
    container: Container, principal: Principal, body: QueryRequest, query_id: uuid.UUID
) -> AsyncIterator[dict[str, str]]:
    start = time.perf_counter()
    diagnostics: dict[str, Any] = {}
    outcome: AnswerOutcome | None = None
    status, error = "failed", None
    try:
        yield _event("started", {"query_id": str(query_id), "trace_id": current_trace_id()})
        retrieval = await container.retriever.retrieve(
            body.query, access_context(principal), filters=to_filters(body.filters)
        )
        RETRIEVAL_SECONDS.labels(strategy="hybrid").observe(sum(retrieval.timings_ms.values()) / 1000)
        diagnostics = retrieval.diagnostics()
        yield _event("retrieval", diagnostics)
        async for ev in container.answerer.stream(body.query, retrieval):
            if ev["type"] == "text":
                yield _event("text", {"text": ev["text"]})
            elif ev["type"] == "citation":
                yield _event("citation", ev["citation"])
            elif ev["type"] == "done":
                outcome = ev["outcome"]
        status = "succeeded"
    except asyncio.CancelledError:
        status, error = "cancelled", "Client disconnected"
        raise
    except NotConfiguredError as exc:
        status, error = "generation_unavailable", exc.message
        yield _event("error", {"code": exc.code, "message": exc.message, "retrieval_available": True})
    except OpsPilotError as exc:
        error = exc.message
        yield _event("error", {"code": exc.code, "message": exc.message})
    except Exception as exc:
        log.exception("query_failed", query_id=str(query_id))
        error = exc.__class__.__name__
        yield _event("error", {"code": "internal_error", "message": "Answer generation failed"})
    finally:
        latency = int((time.perf_counter() - start) * 1000)
        usage = outcome.usage.as_dict() if outcome and outcome.usage else {}
        if outcome and outcome.usage:
            u = outcome.usage
            LLM_TOKENS.labels(model=u.model, kind="input").inc(u.input_tokens)
            LLM_TOKENS.labels(model=u.model, kind="output").inc(u.output_tokens)
            LLM_COST_USD.labels(model=u.model).inc(u.estimated_cost_usd)
        if outcome:
            diagnostics["rejected_citations"] = outcome.rejected_citations
            diagnostics["stop_reason"] = outcome.stop_reason
            diagnostics["citation_spans"] = outcome.citation_spans
        # Shielded so the record is finalised even when the client disconnects mid-stream.
        await asyncio.shield(_finalise(container, query_id, status, outcome, diagnostics, usage, latency, error))
    if outcome is not None:
        yield _event(
            "done",
            {
                "query_id": str(query_id),
                "answer": outcome.answer,
                "abstained": outcome.abstained,
                "citations": [c.as_dict() for c in outcome.citations],
                "usage": usage,
                "latency_ms": latency,
                "stop_reason": outcome.stop_reason,
            },
        )


async def _finalise(
    container: Container,
    query_id: uuid.UUID,
    status: str,
    outcome: AnswerOutcome | None,
    diagnostics: dict[str, Any],
    usage: dict[str, Any],
    latency: int,
    error: str | None,
) -> None:
    async with container.db.session() as s:
        await s.execute(
            update(RagQuery)
            .where(RagQuery.id == query_id)
            .values(
                status=status,
                answer=outcome.answer if outcome else None,
                abstained=outcome.abstained if outcome else False,
                citations=[c.as_dict() for c in outcome.citations] if outcome else [],
                diagnostics=diagnostics,
                usage=usage,
                latency_ms=latency,
                error=error,
            )
        )
        await s.commit()


def _event(name: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data, default=str)}


@router.get("/queries", response_model=list[RagQueryOut])
async def list_queries(
    principal: PrincipalDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> list[RagQuery]:
    principal.require(Permission.QUERY)
    rows = await session.scalars(
        select(RagQuery)
        .where(RagQuery.workspace_id == principal.workspace_id, RagQuery.user_id == principal.user_id)
        .order_by(RagQuery.created_at.desc())
        .limit(limit)
    )
    return list(rows)


@router.get("/queries/{query_id}", response_model=RagQueryOut)
async def get_query(query_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> RagQuery:
    principal.require(Permission.QUERY)
    row = await session.scalar(
        select(RagQuery).where(
            RagQuery.id == query_id,
            RagQuery.workspace_id == principal.workspace_id,
            RagQuery.user_id == principal.user_id,
        )
    )
    if row is None:
        raise NotFoundError("Query not found")
    return row


@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    principal: PrincipalDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AuditLog]:
    principal.require(Permission.AUDIT_READ)
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.workspace_id == principal.workspace_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    return list(rows)
