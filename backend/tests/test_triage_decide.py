"""decide: turns Jev's probabilities into skip / claim / transcription. Unsure spans fail safe to claim."""

import pytest

from app.triage.decide import Decision, decide

ASR, SKIP = 0.5, 0.8


def d(p_small_talk: float, p_asr: float | None) -> Decision:
    return decide(p_small_talk, p_asr, asr_flag=ASR, skip_min=SKIP)


def test_confident_small_talk_is_skipped():
    assert d(0.95, 0.05) == Decision(kind="skip", base_kind="skip", p_claim=pytest.approx(0.05), p_asr=0.05)


def test_confident_claim_is_a_claim():
    assert d(0.06, 0.29).kind == "claim"


def test_unsure_small_talk_fails_safe_to_claim():
    # 0.62 small talk is below skip_min: a missed claim is worse than a needless check.
    out = d(0.62, 0.1)
    assert (out.kind, out.base_kind) == ("claim", "claim")


def test_skip_threshold_is_inclusive():
    assert d(0.8, 0.0).kind == "skip"


def test_likely_transcription_error_wins_and_keeps_the_base_kind():
    out = d(0.62, 0.91)
    assert (out.kind, out.base_kind) == ("transcription", "claim")
    assert d(0.99, 0.6).base_kind == "skip"


def test_asr_threshold_is_inclusive():
    assert d(0.1, 0.5).kind == "transcription"
    assert d(0.1, 0.49).kind == "claim"


def test_without_an_asr_answer_only_the_kind_decides():
    assert d(0.1, None) == Decision(kind="claim", base_kind="claim", p_claim=pytest.approx(0.9), p_asr=None)


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_out_of_range_probabilities_are_rejected(bad):
    with pytest.raises(ValueError):
        d(bad, 0.1)
    with pytest.raises(ValueError):
        d(0.1, bad)
