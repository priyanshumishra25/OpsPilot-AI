"""Hybrid retrieval pipeline: analyse → dense + lexical → RRF → rerank → budget."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document as LCDocument
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from opentelemetry import trace
from pydantic import ConfigDict

from opspilot.db.base import Database
from opspilot.rag import repository
from opspilot.rag.fusion import reciprocal_rank_fusion
from opspilot.rag.query import analyse
from opspilot.rag.rerank import Reranker
from opspilot.rag.types import AccessContext, RetrievalResult, RetrievedChunk, SearchFilters, Strategy

tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class RetrievalConfig:
    candidate_k: int = 30
    final_k: int = 8
    rerank_top_n: int = 40
    rrf_k: int = 60
    context_token_budget: int = 6000
    max_chunks_per_document: int = 4
    abstain_min_rerank_score: float = -4.47
    abstain_max_dense_distance: float = 0.367


class HybridRetriever:
    def __init__(
        self,
        db: Database,
        embeddings: Embeddings,
        reranker: Reranker | None,
        config: RetrievalConfig,
    ) -> None:
        self._db = db
        self._embeddings = embeddings
        self._reranker = reranker
        self.config = config

    async def retrieve(
        self,
        query: str,
        access: AccessContext,
        *,
        filters: SearchFilters | None = None,
        strategy: Strategy = "hybrid",
        rerank: bool = True,
        final_k: int | None = None,
    ) -> RetrievalResult:
        filters = filters or SearchFilters()
        cfg = self.config
        timings: dict[str, float] = {}
        with tracer.start_as_current_span("rag.retrieve") as span:
            span.set_attribute("rag.strategy", strategy)
            analysis = analyse(query)
            span.set_attribute("rag.query_class", analysis.query_class)

            dense: list[tuple[uuid.UUID, float]] = []
            lexical: list[tuple[uuid.UUID, float]] = []
            tasks: list[Awaitable[None]] = []

            async def run_dense() -> None:
                nonlocal dense
                qvec = await _timed(timings, "embed_query", self._embeddings.aembed_query(query))
                async with self._db.session() as s:
                    dense = await _timed(
                        timings,
                        "dense_search",
                        repository.dense_search(s, access, filters, qvec, cfg.candidate_k),
                    )

            async def run_lexical() -> None:
                nonlocal lexical
                async with self._db.session() as s:
                    lexical = await _timed(
                        timings,
                        "lexical_search",
                        repository.lexical_search(s, access, filters, analysis.lexical_query, cfg.candidate_k),
                    )

            if strategy in ("dense", "hybrid"):
                tasks.append(run_dense())
            if strategy in ("lexical", "hybrid"):
                tasks.append(run_lexical())
            await asyncio.gather(*tasks)

            dense_ids = [cid for cid, _ in dense]
            lexical_ids = [cid for cid, _ in lexical]
            fused = reciprocal_rank_fusion([r for r in (dense_ids, lexical_ids) if r], k=cfg.rrf_k)
            async with self._db.session() as s:
                records = await repository.hydrate(s, access, filters, list(fused))
            # Deterministic order: fused score, then content hash for exact ties.
            ordered = sorted(
                (cid for cid in fused if cid in records),
                key=lambda cid: (-fused[cid], records[cid].content_hash),
            )
            dense_info = {cid: (i + 1, d) for i, (cid, d) in enumerate(dense)}
            lex_info = {cid: (i + 1, sc) for i, (cid, sc) in enumerate(lexical)}
            candidates: list[RetrievedChunk] = []
            for cid in ordered:
                rec = records.get(cid)
                if rec is None:
                    continue
                rec.fused_score = fused[cid]
                if cid in dense_info:
                    rec.dense_rank, rec.dense_distance = dense_info[cid]
                if cid in lex_info:
                    rec.lexical_rank, rec.lexical_score = lex_info[cid]
                candidates.append(rec)

            if rerank and self._reranker is not None and candidates:
                head, tail = candidates[: cfg.rerank_top_n], candidates[cfg.rerank_top_n :]
                scores = await _timed(timings, "rerank", self._reranker.score(query, [_rerank_text(c) for c in head]))
                for c, sc in zip(head, scores, strict=True):
                    c.rerank_score = sc
                head.sort(key=lambda c: c.rerank_score if c.rerank_score is not None else float("-inf"), reverse=True)
                candidates = head + tail  # unscored tail keeps fused order, after all scored candidates

            selected = self._budget(candidates, final_k or cfg.final_k)
            sufficient, top = self._sufficiency(candidates)
            span.set_attribute("rag.candidates", len(candidates))
            span.set_attribute("rag.returned", len(selected))
            span.set_attribute("rag.evidence_sufficient", sufficient)
            return RetrievalResult(
                query=query,
                query_class=analysis.query_class,
                lexical_query=analysis.lexical_query,
                strategy=strategy,
                chunks=selected,
                candidates_considered=len(candidates),
                timings_ms=timings,
                evidence_sufficient=sufficient,
                top_rerank_score=top,
            )

    def _budget(self, candidates: list[RetrievedChunk], final_k: int) -> list[RetrievedChunk]:
        """Select top passages within the token budget, dropping duplicates and capping per-document share."""
        out: list[RetrievedChunk] = []
        used = 0
        seen_hashes: set[str] = set()
        per_doc: dict[uuid.UUID, int] = {}
        for c in candidates:
            if len(out) >= final_k:
                break
            if c.content_hash in seen_hashes or per_doc.get(c.document_id, 0) >= self.config.max_chunks_per_document:
                continue
            if used + c.token_estimate > self.config.context_token_budget and out:
                continue
            out.append(c)
            used += c.token_estimate
            seen_hashes.add(c.content_hash)
            per_doc[c.document_id] = per_doc.get(c.document_id, 0) + 1
        return out

    def _sufficiency(self, candidates: list[RetrievedChunk]) -> tuple[bool, float | None]:
        if not candidates:
            return False, None
        top = candidates[0]
        if top.rerank_score is not None:
            return top.rerank_score >= self.config.abstain_min_rerank_score, top.rerank_score
        if top.dense_distance is not None:
            return top.dense_distance <= self.config.abstain_max_dense_distance, None
        return True, None  # lexical-only match without a calibrated signal


def _rerank_text(c: RetrievedChunk) -> str:
    heading = " > ".join(c.section_path)
    return f"{c.document_title} {('— ' + heading) if heading else ''}\n{c.content}"


async def _timed[T](timings: dict[str, float], key: str, aw: Awaitable[T]) -> T:
    start = time.perf_counter()
    try:
        return await aw
    finally:
        timings[key] = (time.perf_counter() - start) * 1000


class OpsPilotLangChainRetriever(BaseRetriever):
    """LangChain `BaseRetriever` over the hybrid pipeline, bound to one trusted access context."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    hybrid: HybridRetriever
    access: AccessContext
    filters: SearchFilters = SearchFilters()

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[LCDocument]:
        raise NotImplementedError("Use the async interface (ainvoke)")

    async def _aget_relevant_documents(self, query: str, *, run_manager: object) -> list[LCDocument]:
        result = await self.hybrid.retrieve(query, self.access, filters=self.filters)
        return [LCDocument(page_content=c.content, metadata=c.to_evidence(), id=str(c.chunk_id)) for c in result.chunks]


RetrieverFactory = Callable[[AccessContext], OpsPilotLangChainRetriever]
