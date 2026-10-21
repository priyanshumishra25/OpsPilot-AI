from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Strategy = Literal["dense", "lexical", "hybrid"]


@dataclass(frozen=True)
class SearchFilters:
    """Caller-requested narrowing. These can only restrict results, never widen access."""

    document_ids: tuple[uuid.UUID, ...] = ()
    source_types: tuple[str, ...] = ()
    access_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccessContext:
    """Trusted retrieval scope, built from the authenticated principal only."""

    workspace_id: uuid.UUID
    access_scopes: frozenset[str]


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    document_title: str
    source_type: str
    source_uri: str
    section_path: list[str]
    content: str
    content_hash: str
    page_number: int | None
    char_start: int
    char_end: int
    token_estimate: int
    access_scope: str
    dense_rank: int | None = None
    dense_distance: float | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    fused_score: float = 0.0
    rerank_score: float | None = None

    @property
    def section_heading(self) -> str | None:
        return self.section_path[-1] if self.section_path else None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "document_title": self.document_title,
            "source_type": self.source_type,
            "source_uri": self.source_uri,
            "section_path": self.section_path,
            "page_number": self.page_number,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "fused_score": round(self.fused_score, 6),
            "rerank_score": None if self.rerank_score is None else round(self.rerank_score, 4),
            "preview": self.content[:400],
        }


@dataclass
class RetrievalResult:
    query: str
    query_class: str
    lexical_query: str
    strategy: Strategy
    chunks: list[RetrievedChunk]
    candidates_considered: int
    timings_ms: dict[str, float] = field(default_factory=dict)
    evidence_sufficient: bool = True
    top_rerank_score: float | None = None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "query_class": self.query_class,
            "lexical_query": self.lexical_query,
            "strategy": self.strategy,
            "candidates_considered": self.candidates_considered,
            "returned": len(self.chunks),
            "timings_ms": {k: round(v, 2) for k, v in self.timings_ms.items()},
            "evidence_sufficient": self.evidence_sufficient,
            "top_rerank_score": self.top_rerank_score,
            "results": [c.to_evidence() for c in self.chunks],
        }
