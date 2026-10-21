"""Exercises the real fastembed text models (downloads ~150 MB on first run). Guards the pillow override."""

import os

import numpy as np
import pytest

from opspilot.rag.embeddings import FastEmbedEmbeddings
from opspilot.rag.rerank import CrossEncoderReranker

pytestmark = pytest.mark.models
CACHE = os.environ.get("OPSPILOT_EMBEDDING_CACHE_DIR")


def test_embedding_and_reranker_models_run() -> None:
    e = FastEmbedEmbeddings("BAAI/bge-small-en-v1.5", cache_dir=CACHE)
    docs = e.embed_documents(["Restart checkout pods one at a time.", "Rotations hand over on Mondays."])
    q = e.embed_query("how to restart checkout")
    assert len(q) == 384 and abs(float(np.linalg.norm(q)) - 1.0) < 1e-4
    assert float(np.dot(q, docs[0])) > float(np.dot(q, docs[1]))
    r = CrossEncoderReranker("Xenova/ms-marco-MiniLM-L-6-v2", cache_dir=CACHE)
    s = r.score_sync(
        "how to restart checkout", ["Restart checkout pods one at a time.", "Rotations hand over on Mondays."]
    )
    assert s[0] > s[1]
