"""Sentence spans used as the semantic split's units: never cut at abbreviations, decimals or initials."""

import pytest

from app.corpus.sentences import split_sentences


def texts(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]


def test_splits_on_terminal_punctuation():
    assert texts("Offer surgery. Is it safe? Yes!") == ["Offer surgery.", "Is it safe?", "Yes!"]


@pytest.mark.parametrize(
    "sentence",
    [
        "Use a systemic agent, e.g. Nivolumab, as first line.",
        "Use a systemic agent, i.e. Nivolumab, as first line.",
        "Compare surgery vs. Radiotherapy in this group.",
        "Dr. Smith reviewed the slides with the team.",
        "Margins are shown in Fig. 3 of the guideline.",
        "The Breslow depth was 1.2 mm in this patient.",
        "The trial was led by J. R. Tolkien in Oxford.",
        "Approved by the U.S. Food and Drug Administration.",
        "As reported by Smith et al. In their cohort, rates fell.",
    ],
)
def test_never_cuts_inside(sentence):
    assert texts(sentence + " Next sentence here.") == [sentence, "Next sentence here."]


def test_newlines_are_boundaries_and_spans_are_trimmed():
    text = "  A heading-less line without a full stop\nSecond line.  "
    assert texts(text) == ["A heading-less line without a full stop", "Second line."]


def test_list_numbering_at_line_start_is_not_a_sentence():
    assert texts("1. Offer surgery.\n2. Offer chemotherapy.") == ["1. Offer surgery.", "2. Offer chemotherapy."]


def test_lowercase_continuation_is_not_a_boundary():
    assert texts("Dose is 5 mg. daily for a week.") == ["Dose is 5 mg. daily for a week."]


def test_non_ascii_hyphen_is_not_a_boundary():
    assert texts("BRAF‐mutant disease. Next.") == ["BRAF‐mutant disease.", "Next."]


def test_spans_are_ordered_and_non_overlapping():
    spans = split_sentences("One. Two. Three.\nFour.")
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
