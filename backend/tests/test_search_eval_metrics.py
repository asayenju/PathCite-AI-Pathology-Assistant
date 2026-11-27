"""Offline tests for the search eval metrics (backend/evals/search/metrics.py), on hand-computed examples."""

from math import isclose, log2

import pytest

from evals.search.metrics import (
    aggregate,
    hit_at,
    mrr,
    ndcg_at,
    noise,
    precision_at,
    recall_at,
    score_claim,
)

# Grades for one claim: 1, 4 and 5 settle it (2), 3 is on topic (1), 2 is judged unrelated (0).
GRADES = {1: 2, 2: 0, 3: 1, 4: 2, 5: 2}
# Ranked result: 6 was never judged (counts as 0); 5 was not retrieved.
RANKED = [2, 1, 3, 6, 4]


def approx(a, b):
    return a is not None and isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


# --- hit rate ------------------------------------------------------------------


def test_hit_at():
    assert hit_at(RANKED, GRADES, 1) == 0.0
    assert hit_at(RANKED, GRADES, 2) == 1.0
    assert hit_at(RANKED, GRADES, 8) == 1.0


def test_hit_ignores_grade_1():
    assert hit_at([3], GRADES, 1) == 0.0


def test_hit_with_empty_result():
    assert hit_at([], GRADES, 8) == 0.0


# --- recall --------------------------------------------------------------------


def test_recall_at():
    # three grade-2 chunks known (1, 4, 5)
    assert recall_at(RANKED, GRADES, 1) == 0.0
    assert approx(recall_at(RANKED, GRADES, 3), 1 / 3)
    assert approx(recall_at(RANKED, GRADES, 5), 2 / 3)
    assert approx(recall_at(RANKED, GRADES, 8), 2 / 3)


def test_recall_undefined_without_known_grade_2():
    assert recall_at([3, 2], {3: 1, 2: 0}, 8) is None
    assert recall_at([], {}, 8) is None


# --- precision -----------------------------------------------------------------


def test_precision_strict_and_lenient():
    assert approx(precision_at(RANKED, GRADES, 3), 1 / 3)
    assert approx(precision_at(RANKED, GRADES, 5), 2 / 5)
    assert approx(precision_at(RANKED, GRADES, 3, min_grade=1), 2 / 3)
    # 6 is unjudged and counts as 0
    assert approx(precision_at(RANKED, GRADES, 5, min_grade=1), 3 / 5)


def test_precision_with_fewer_than_k_uses_the_number_returned():
    assert precision_at([1, 2], GRADES, 8) == 0.5
    assert precision_at([1], GRADES, 3) == 1.0


def test_precision_of_nothing_is_undefined():
    assert precision_at([], GRADES, 8) is None


# --- MRR -----------------------------------------------------------------------


def test_mrr():
    assert mrr(RANKED, GRADES) == 0.5
    assert mrr([1], GRADES) == 1.0
    assert mrr([3, 2], GRADES) == 0.0


def test_mrr_only_looks_at_the_top_8():
    ranked = [100 + i for i in range(8)] + [1]
    assert mrr(ranked, GRADES) == 0.0
    assert mrr(ranked, GRADES, cutoff=9) == 1 / 9


# --- NDCG ----------------------------------------------------------------------


def test_ndcg_at_3():
    dcg = 0 / log2(2) + 2 / log2(3) + 1 / log2(4)
    # ideal: every judged grade, best first -> 2, 2, 2 (then 1, 0)
    idcg = 2 / log2(2) + 2 / log2(3) + 2 / log2(4)
    assert approx(ndcg_at(RANKED, GRADES, 3), dcg / idcg)


def test_ndcg_at_5_counts_unjudged_as_0():
    dcg = 2 / log2(3) + 1 / log2(4) + 0 / log2(5) + 2 / log2(6)
    idcg = 2 / log2(2) + 2 / log2(3) + 2 / log2(4) + 1 / log2(5) + 0 / log2(6)
    assert approx(ndcg_at(RANKED, GRADES, 5), dcg / idcg)


def test_ndcg_at_1():
    assert ndcg_at(RANKED, GRADES, 1) == 0.0
    assert ndcg_at([4], GRADES, 1) == 1.0


def test_ndcg_ties_any_order_of_equal_grades_is_ideal():
    grades = {1: 2, 2: 2, 3: 1}
    assert approx(ndcg_at([1, 2, 3], grades, 3), 1.0)
    assert approx(ndcg_at([2, 1, 3], grades, 3), 1.0)
    assert ndcg_at([3, 1, 2], grades, 3) < 1.0


def test_ndcg_without_any_relevant_judgement_is_undefined():
    assert ndcg_at([1, 2], {1: 0, 2: 0}, 8) is None


def test_ndcg_with_only_grade_1_is_defined():
    assert approx(ndcg_at([2, 1], {1: 1, 2: 0}, 2), (1 / log2(3)) / 1.0)


def test_ndcg_with_empty_result():
    assert ndcg_at([], GRADES, 8) == 0.0


# --- duplicates ----------------------------------------------------------------


def test_duplicate_ids_count_once():
    assert precision_at([1, 1, 2], GRADES, 3) == 0.5
    assert recall_at([1, 1, 1], GRADES, 3) == 1 / 3


# --- per claim -----------------------------------------------------------------


def test_score_claim_keys_and_short_flag():
    s = score_claim([1, 2], GRADES, ks=(1, 3))
    assert s["hit@1"] == 1.0
    assert s["recall@3"] == 1 / 3
    assert s["p_strict@3"] == 0.5
    assert s["p_lenient@3"] == 0.5
    assert s["short@1"] is False
    assert s["short@3"] is True
    assert s["mrr"] == 1.0
    assert "ndcg@3" in s


# --- aggregate -----------------------------------------------------------------


def claim(type_, ranked, grades):
    return {"type": type_, "ranked": ranked, "grades": grades}


def test_aggregate_means_over_covered_claims_and_skips_undefined():
    claims = [
        claim("supported", [1], {1: 2}),  # hit, recall 1, mrr 1
        claim("contradicted", [2, 1], {1: 2, 2: 0}),  # hit@1 0, hit@2 1, mrr 0.5
        claim("supported", [3], {3: 1}),  # nothing grade 2 known: recall undefined
        claim("not_covered", [9], {9: 2}),  # excluded from the main metrics
    ]
    agg = aggregate(claims, ks=(1, 2))
    assert agg["n"] == 3
    assert approx(agg["hit@1"], 1 / 3)
    assert approx(agg["hit@2"], 2 / 3)
    assert approx(agg["recall@2"], 1.0)  # (1 + 1) / 2, the undefined one excluded
    assert approx(agg["mrr"], (1 + 0.5 + 0) / 3)
    assert approx(agg["p_strict@1"], (1 + 0 + 0) / 3)
    assert agg["short@2"] == 2  # two claims returned fewer than 2


def test_aggregate_of_nothing():
    agg = aggregate([claim("not_covered", [1], {1: 0})], ks=(1,))
    assert agg["n"] == 0
    assert agg["hit@1"] is None
    assert agg["mrr"] is None


# --- noise (not covered) ---------------------------------------------------------


def test_noise_on_not_covered_claims_only():
    claims = [
        claim("not_covered", [1, 2, 3, 4], {1: 1, 2: 0}),  # lenient P@8 = 1/4, no grade 2
        claim("not_covered", [5, 6], {5: 2, 6: 1}),  # lenient 1.0, has grade 2
        claim("supported", [7], {7: 2}),  # ignored
    ]
    n = noise(claims, k=8)
    assert n["n"] == 2
    assert approx(n["p_lenient"], (0.25 + 1.0) / 2)
    assert n["any_relevant_share"] == 0.5


def test_noise_without_not_covered_claims():
    n = noise([claim("supported", [1], {1: 2})], k=8)
    assert n == {"n": 0, "p_lenient": None, "any_relevant_share": None}


@pytest.mark.parametrize("k", [0, -1])
def test_k_must_be_positive(k):
    with pytest.raises(ValueError):
        hit_at([1], GRADES, k)
