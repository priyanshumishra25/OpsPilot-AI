from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder


class Reranker(Protocol):
    name: str

    async def score(self, query: str, passages: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    """Local cross-encoder (fastembed ONNX). Scores are relevance logits; higher is better."""

    def __init__(self, model_name: str, *, cache_dir: str | None = None) -> None:
        self.name = model_name
        self._cache_dir = cache_dir
        self._model: TextCrossEncoder | None = None
        self._lock = threading.Lock()

    def _load(self) -> TextCrossEncoder:
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(self.name, cache_dir=self._cache_dir)
        return self._model

    def score_sync(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._load().rerank(query, passages, batch_size=32)]

    async def score(self, query: str, passages: list[str]) -> list[float]:
        return await asyncio.to_thread(self.score_sync, query, passages)
