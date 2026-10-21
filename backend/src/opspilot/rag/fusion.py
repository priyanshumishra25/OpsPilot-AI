from __future__ import annotations

import uuid
from collections.abc import Sequence


def reciprocal_rank_fusion(rankings: Sequence[Sequence[uuid.UUID]], *, k: int = 60) -> dict[uuid.UUID, float]:
    """RRF (Cormack et al., 2009): score(d) = sum over rankings of 1 / (k + rank(d)), rank 1-based."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
