import math

import pytest

from opspilot.evaluation.metrics import best_threshold, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank


def test_ranking_metrics() -> None:
    ranked = ["x", "a", "a", "b", "y"]
    rel = {"a", "b", "c"}
    assert recall_at_k(ranked, rel, 5) == pytest.approx(2 / 3)
    assert recall_at_k(ranked, rel, 1) == 0.0
    assert precision_at_k(ranked, rel, 5) == pytest.approx(3 / 5)
    assert precision_at_k(ranked[:2], rel, 5) == pytest.approx(1 / 2)
    assert reciprocal_rank(ranked, rel) == 0.5
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(ranked, rel, 5) == pytest.approx(dcg / ideal)
    assert ndcg_at_k(["a"], {"a"}, 5) == 1.0
    assert reciprocal_rank([], rel) == 0.0


def test_best_threshold_separates_classes() -> None:
    t, bacc = best_threshold([2.0, 3.0, 1.5], [-5.0, -3.0])
    assert -3.0 < t <= 1.5 and bacc == 1.0
    _t2, bacc2 = best_threshold([1.0, -4.0], [-3.0])
    assert bacc2 == pytest.approx(0.75)
