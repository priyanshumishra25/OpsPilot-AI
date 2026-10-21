"""Deterministic retrieval / grounding metrics with binary relevance."""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Hashable, Sequence, Set


def first_relevant_rank[T: Hashable](ranked: Sequence[T], relevant: Set[T]) -> int | None:
    return next((i + 1 for i, item in enumerate(ranked) if item in relevant), None)


def recall_at_k[T: Hashable](ranked: Sequence[T], relevant: Set[T], k: int) -> float:
    """Fraction of distinct relevant items appearing in the top k (duplicates count once)."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k[T: Hashable](ranked: Sequence[T], relevant: Set[T], k: int) -> float:
    """Fraction of the top-k *positions* that hold a relevant item. Uses len(top) when fewer than k."""
    top = ranked[:k]
    return sum(1 for r in top if r in relevant) / len(top) if top else 0.0


def reciprocal_rank[T: Hashable](ranked: Sequence[T], relevant: Set[T]) -> float:
    rank = first_relevant_rank(ranked, relevant)
    return 1.0 / rank if rank else 0.0


def ndcg_at_k[T: Hashable](ranked: Sequence[T], relevant: Set[T], k: int) -> float:
    """Binary-gain nDCG; a relevant item only earns gain the first time it appears."""
    seen: set[T] = set()
    dcg = 0.0
    for i, item in enumerate(ranked[:k]):
        if item in relevant and item not in seen:
            dcg += 1.0 / math.log2(i + 2)
            seen.add(item)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1])


def best_threshold(positives: Sequence[float], negatives: Sequence[float]) -> tuple[float, float]:
    """Threshold t maximising balanced accuracy for the rule `score >= t ⇒ sufficient evidence`.

    `positives` are top scores for answerable questions, `negatives` for unanswerable ones.
    Returns (threshold, balanced_accuracy). Candidate thresholds are midpoints between sorted scores.
    """
    scores = sorted(set(positives) | set(negatives))
    if not scores:
        return 0.0, 0.0
    candidates = [scores[0] - 1.0] + [(a + b) / 2 for a, b in itertools.pairwise(scores)] + [scores[-1] + 1.0]
    best = (candidates[0], -1.0)
    for t in candidates:
        tpr = sum(s >= t for s in positives) / len(positives) if positives else 0.0
        tnr = sum(s < t for s in negatives) / len(negatives) if negatives else 0.0
        bacc = (tpr + tnr) / 2
        if bacc > best[1]:
            best = (t, bacc)
    return best
