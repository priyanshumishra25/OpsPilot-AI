"""Embedding providers exposed through LangChain's `Embeddings` interface."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import numpy as np
from langchain_core.embeddings import Embeddings

if TYPE_CHECKING:
    from fastembed import TextEmbedding

# BGE v1.5 retrieval instruction for short queries (from the model card). Passages take no prefix.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class FastEmbedEmbeddings(Embeddings):
    """Local ONNX embeddings via fastembed. Vectors are L2-normalised, so cosine distance is used."""

    def __init__(self, model_name: str, *, cache_dir: str | None = None, query_prefix: str | None = None) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._query_prefix = (
            query_prefix if query_prefix is not None else (BGE_QUERY_PREFIX if "bge" in model_name.lower() else "")
        )
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    def _load(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(self.model_name, cache_dir=self._cache_dir)
        return self._model

    @property
    def dimension(self) -> int:
        return len(self.embed_query("dimension probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [_normalise(v) for v in self._load().embed(texts, batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        return _normalise(next(iter(self._load().embed([self._query_prefix + text]))))

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)


def _normalise(v: np.ndarray) -> list[float]:
    arr = np.asarray(v, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    return (arr / norm if norm > 0 else arr).tolist()  # type: ignore[no-any-return]
