"""Unit tests for the v2 verdict and review rules (app/check/aggregate.py, docs/PRD-rag-checker.md). Pure, no I/O."""

import pytest

from app.check.aggregate import (
    JEV_DISAGREES,
    JEV_UNAVAILABLE,
    LOW_CONFIDENCE,
    SOURCES_DISAGREE,
    Confidence,
    aggregate,
    luna_class,
    luna_verdict,
)
from app.check.jev_check import JevVerdict
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


def jev(supported=0.0, contradicted=0.0, insufficient=0.0):
    probs = {"supported": supported, "contradicted": contradicted, "insufficient": insufficient}
    label = max(probs, key=probs.get)
    return JevVerdict(probs=probs, label=label, p=probs[label])


def test_contract_strings():
    assert (SOURCES_DISAGREE, JEV_UNAVAILABLE, JEV_DISAGREES, LOW_CONFIDENCE) == (
        "sources_disagree",
        "jev_unavailable",
        "jev_disagrees",
        "low_confidence",
    )


# --- Luna's verdict --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stances, missing, verdict",
    [
        (("irrelevant", "irrelevant"), False, "unverified"),
        ((), False, "unverified"),
        (("supports", "irrelevant", "supports"), False, "accurate"),
        (("supports",), True, "incomplete"),
        (("contradicts", "irrelevant"), False, "inaccurate"),
        (("contradicts",), True, "inaccurate"),  # a missing qualifier doesn't soften a contradiction
        (("supports", "supports", "supports", "supports", "contradicts"), False, "disputed"),
        (("unclear", "unclear"), False, "unverified"),  # guard-unclear counts as not relevant
        (("supports", "unclear"), False, "accurate"),
        (("contradicts", "unclear"), False, "inaccurate"),
    ],
)
def test_luna_verdict(stances, missing, verdict):
    assert luna_verdict(luna(*stances), missing) == verdict


@pytest.mark.parametrize(
    "verdict, label",
    [
        ("accurate", "supported"),
        ("incomplete", "supported"),
        ("inaccurate", "contradicted"),
        ("unverified", "insufficient"),
        ("disputed", None),
    ],
)
def test_luna_class(verdict, label):
    assert luna_class(verdict) == label


# --- review rules ----------------------------------------------------------------------------------------------------


def test_confident_agreement_needs_no_review():
    r = aggregate(luna("supports", "irrelevant"), False, jev(supported=0.85, insufficient=0.15))
    assert r.verdict == "accurate"
    assert r.needs_review is False
    assert r.review_reasons == []
    assert r.confidence == Confidence(label="supported", p=0.85, p_verdict=0.85, agrees=True)


def test_confident_agreement_on_unverified_needs_no_review():
    r = aggregate(luna("irrelevant"), False, jev(insufficient=0.9, supported=0.1))
    assert r.verdict == "unverified"
    assert not r.needs_review
    assert r.confidence.agrees is True


def test_confident_agreement_on_inaccurate_needs_no_review():
    r = aggregate(luna("contradicts"), False, jev(contradicted=0.75, supported=0.25))
    assert r.verdict == "inaccurate"
    assert not r.needs_review


def test_incomplete_maps_to_supported():
    r = aggregate(luna("supports"), True, jev(supported=0.9, insufficient=0.1))
    assert r.verdict == "incomplete"
    assert r.confidence.p_verdict == 0.9
    assert not r.needs_review


def test_jev_top_label_differs_is_jev_disagrees():
    r = aggregate(luna("supports"), False, jev(supported=0.3, insufficient=0.7))
    assert r.verdict == "accurate"  # Jev never changes the verdict
    assert r.needs_review
    assert r.review_reasons == [JEV_DISAGREES]
    assert r.confidence == Confidence(label="insufficient", p=0.7, p_verdict=0.3, agrees=False)


def test_jev_agrees_but_below_min_is_low_confidence():
    r = aggregate(luna("supports"), False, jev(supported=0.6, insufficient=0.4), jev_min=0.7)
    assert r.needs_review
    assert r.review_reasons == [LOW_CONFIDENCE]
    assert r.confidence.agrees is True and r.confidence.p_verdict == 0.6


def test_jev_at_min_is_confident():
    r = aggregate(luna("supports"), False, jev(supported=0.7, insufficient=0.3), jev_min=0.7)
    assert not r.needs_review


def test_default_jev_min_is_0_7():
    assert aggregate(luna("supports"), False, jev(supported=0.69, insufficient=0.31)).review_reasons == [LOW_CONFIDENCE]


def test_disputed_is_sources_disagree_with_no_p_verdict():
    r = aggregate(luna("supports", "contradicts"), False, jev(supported=0.9, contradicted=0.1))
    assert r.verdict == "disputed"
    assert r.needs_review
    assert r.review_reasons == [SOURCES_DISAGREE]
    assert r.confidence == Confidence(label="supported", p=0.9, p_verdict=None, agrees=None)


def test_jev_unavailable_keeps_the_verdict():
    r = aggregate(luna("supports"), True, None)
    assert r.verdict == "incomplete"
    assert r.needs_review
    assert r.review_reasons == [JEV_UNAVAILABLE]
    assert r.confidence is None


def test_disputed_and_jev_unavailable_are_ordered():
    r = aggregate(luna("contradicts", "supports"), False, None)
    assert r.review_reasons == [SOURCES_DISAGREE, JEV_UNAVAILABLE]
    assert len(set(r.review_reasons)) == len(r.review_reasons)


def test_guard_unclear_is_not_a_review_reason_by_itself():
    r = aggregate(luna("unclear", "irrelevant"), False, jev(insufficient=0.95, supported=0.05))
    assert r.verdict == "unverified"
    assert not r.needs_review


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_bad_jev_min_raises(bad):
    with pytest.raises(ValueError):
        aggregate([], False, None, jev_min=bad)
