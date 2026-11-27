"""Unit tests for the verdict rules (app/check/aggregate.py, docs/PRD-rag-checker.md section 4). Pure, no I/O."""

import pytest

from app.check.aggregate import (
    JEV_UNAVAILABLE,
    MODELS_DISAGREE,
    SOURCES_DISAGREE,
    aggregate,
)
from app.check.jev_check import JevStance
from app.check.verify import SourceStance


def luna(*stances):
    """One SourceStance per argument, chunk ids 1..n. 'unclear' means the groundedness guard fired."""
    out = []
    for i, s in enumerate(stances, 1):
        if s == "unclear":
            out.append(SourceStance(i, "unclear", "made up", "supports", False))
        elif s == "irrelevant":
            out.append(SourceStance(i, "irrelevant", "", "irrelevant", True))
        else:
            out.append(SourceStance(i, s, f"quote {i}", s, True))
    return out


def jev(*stances, p=0.9):
    return [JevStance(i, s, p) for i, s in enumerate(stances, 1)]


def test_contract_strings():
    assert (SOURCES_DISAGREE, MODELS_DISAGREE, JEV_UNAVAILABLE) == (
        "sources_disagree",
        "models_disagree",
        "jev_unavailable",
    )


def test_all_support_and_models_agree_is_accurate():
    r = aggregate(luna("supports", "supports", "irrelevant"), jev("supports", "supports", "irrelevant"), False)
    assert r.verdict == "accurate"
    assert not r.needs_review
    assert r.review_reasons == []
    assert [c.stance for c in r.chunks] == ["supports", "supports", "irrelevant"]


def test_missing_qualifier_makes_support_incomplete():
    r = aggregate(luna("supports"), jev("supports"), True)
    assert r.verdict == "incomplete"
    assert not r.needs_review


def test_all_contradict_is_inaccurate():
    r = aggregate(luna("contradicts", "irrelevant"), jev("contradicts", "irrelevant"), False)
    assert r.verdict == "inaccurate"
    assert not r.needs_review


def test_missing_qualifier_does_not_soften_a_contradiction():
    assert aggregate(luna("contradicts"), jev("contradicts"), True).verdict == "inaccurate"


def test_four_supports_and_one_contradiction_is_disputed():
    stances = ("supports", "supports", "supports", "supports", "contradicts")
    r = aggregate(luna(*stances), jev(*stances), False)
    assert r.verdict == "disputed"
    assert r.needs_review
    assert r.review_reasons == [SOURCES_DISAGREE]


def test_luna_and_jev_split_on_one_chunk_is_unclear():
    r = aggregate(luna("supports", "supports"), jev("supports", "contradicts"), False)
    assert [c.stance for c in r.chunks] == ["supports", "unclear"]
    assert r.chunks[1].luna_stance == "supports"
    assert r.chunks[1].jev_stance == "contradicts"
    assert r.verdict == "accurate"  # the one agreed chunk still supports
    assert r.needs_review
    assert r.review_reasons == [MODELS_DISAGREE]


def test_luna_irrelevant_jev_supports_is_unclear():
    r = aggregate(luna("irrelevant"), jev("supports"), False)
    assert r.chunks[0].stance == "unclear"
    assert r.verdict == "unverified"
    assert r.review_reasons == [MODELS_DISAGREE]


def test_jev_probability_below_min_is_unclear():
    r = aggregate(luna("supports"), jev("supports", p=0.55), False, jev_min=0.6)
    assert r.chunks[0].stance == "unclear"
    assert r.chunks[0].jev_p == 0.55
    assert r.verdict == "unverified"
    assert r.needs_review
    assert r.review_reasons == [MODELS_DISAGREE]


def test_jev_probability_at_min_counts():
    assert aggregate(luna("supports"), jev("supports", p=0.6), False, jev_min=0.6).chunks[0].stance == "supports"


def test_both_irrelevant_is_irrelevant_whatever_jev_p():
    r = aggregate(luna("irrelevant"), jev("irrelevant", p=0.4), False)
    assert r.chunks[0].stance == "irrelevant"
    assert r.verdict == "unverified"
    assert not r.needs_review
    assert r.review_reasons == []


def test_all_irrelevant_is_unverified_without_review():
    r = aggregate(luna("irrelevant", "irrelevant"), jev("irrelevant", "irrelevant"), False)
    assert r.verdict == "unverified"
    assert not r.needs_review


def test_luna_guard_unclear_with_jev_agreeing_is_still_unclear():
    r = aggregate(luna("unclear"), jev("supports"), False)
    assert r.chunks[0].stance == "unclear"
    assert r.chunks[0].luna_stance == "unclear"
    assert r.verdict == "unverified"
    assert r.needs_review
    assert r.review_reasons == [MODELS_DISAGREE]


def test_unclear_only_is_unverified_and_needs_review():
    r = aggregate(luna("unclear", "supports"), jev("supports", "contradicts"), False)
    assert [c.stance for c in r.chunks] == ["unclear", "unclear"]
    assert r.verdict == "unverified"
    assert r.needs_review


def test_jev_unavailable_uses_luna_stances():
    r = aggregate(luna("supports", "irrelevant", "unclear"), None, False)
    assert [c.stance for c in r.chunks] == ["supports", "irrelevant", "unclear"]
    assert all(c.jev_stance is None and c.jev_p is None for c in r.chunks)
    assert r.verdict == "accurate"
    assert r.needs_review
    assert r.review_reasons == [JEV_UNAVAILABLE]


def test_jev_unavailable_with_mixed_luna_stances():
    r = aggregate(luna("supports", "contradicts"), None, False)
    assert r.verdict == "disputed"
    assert r.review_reasons == [SOURCES_DISAGREE, JEV_UNAVAILABLE]


def test_reasons_are_ordered_and_unique():
    r = aggregate(
        luna("supports", "contradicts", "supports", "supports"),
        jev("supports", "contradicts", "contradicts", "irrelevant"),
        False,
    )
    assert r.verdict == "disputed"
    assert r.review_reasons == [SOURCES_DISAGREE, MODELS_DISAGREE]


def test_jev_missing_a_chunk_makes_it_unclear():
    r = aggregate(luna("supports", "supports"), [JevStance(1, "supports", 0.9)], False)
    assert [c.stance for c in r.chunks] == ["supports", "unclear"]
    assert r.chunks[1].jev_stance is None
    assert r.review_reasons == [MODELS_DISAGREE]


def test_chunk_order_follows_luna():
    r = aggregate(luna("supports", "irrelevant"), list(reversed(jev("supports", "irrelevant"))), False)
    assert [c.chunk_id for c in r.chunks] == [1, 2]
    assert [c.stance for c in r.chunks] == ["supports", "irrelevant"]


def test_no_chunks_is_unverified():
    r = aggregate([], [], False)
    assert r.verdict == "unverified"
    assert not r.needs_review
    assert r.chunks == []


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_bad_jev_min_raises(bad):
    with pytest.raises(ValueError):
        aggregate([], [], False, jev_min=bad)
