"""split_spans: deterministic sentence spans (character offsets) for Jev triage. Jev can't split text itself."""

from app.triage.segment import MAX_SPAN_WORDS, Span, split_spans


def texts(text: str) -> list[str]:
    return [text[s.start : s.end] for s in split_spans(text)]


def test_splits_sentences_on_terminal_punctuation():
    assert texts("Good morning. Is it bad? It isn't!") == ["Good morning.", "Is it bad?", "It isn't!"]


def test_offsets_index_into_the_original_text_and_are_trimmed():
    text = "  Hello there.   Staging tells us how deep it goes.  "
    spans = split_spans(text)
    assert [text[s.start : s.end] for s in spans] == ["Hello there.", "Staging tells us how deep it goes."]
    assert all(text[s.start] != " " and text[s.end - 1] != " " for s in spans)


def test_spans_are_ordered_and_do_not_overlap():
    spans = split_spans("One thing. Two things. Three things.")
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))


def test_does_not_split_decimals():
    assert texts("The Breslow depth is 1.2 mm. That is thin.") == ["The Breslow depth is 1.2 mm.", "That is thin."]


def test_does_not_split_after_abbreviations():
    assert texts("Dr. Patel and Mrs. Jones agree, e.g. on staging. Next sentence.") == [
        "Dr. Patel and Mrs. Jones agree, e.g. on staging.",
        "Next sentence.",
    ]


def test_does_not_split_after_an_initial():
    assert texts("Ask J. Smith about it. Thanks.") == ["Ask J. Smith about it.", "Thanks."]


def test_ellipsis_before_lowercase_continues_the_sentence():
    assert texts("It's, um... it's a cancer of the gland cells. Okay?") == [
        "It's, um... it's a cancer of the gland cells.",
        "Okay?",
    ]


def test_ellipsis_before_a_capital_ends_the_sentence():
    assert texts("Well… That is the result.") == ["Well…", "That is the result."]


def test_closing_quote_stays_with_its_sentence():
    assert texts('She said "it\'s benign." Then we talked.') == ['She said "it\'s benign."', "Then we talked."]


def test_text_without_final_punctuation_is_one_span():
    assert texts("so the tumor is moderately differentiated") == ["so the tumor is moderately differentiated"]


def test_empty_and_whitespace_text_has_no_spans():
    assert split_spans("") == []
    assert split_spans("   \n ") == []


def test_long_unpunctuated_run_is_cut_at_a_comma_or_conjunction():
    first = " ".join(["word"] * 25) + ","
    second = " ".join(["more"] * 25)
    out = texts(f"{first} {second}")
    assert out == [first, second]


def test_long_run_without_a_break_point_is_cut_at_max_words():
    words = [f"w{i}" for i in range(MAX_SPAN_WORDS * 2 + 5)]
    out = texts(" ".join(words))
    assert [len(t.split()) for t in out] == [MAX_SPAN_WORDS, MAX_SPAN_WORDS, 5]
    assert " ".join(out) == " ".join(words)


def test_filler_only_spans_are_marked_for_local_skip():
    spans = split_spans("Um. Okay, okay. Mm-hmm. The tumor is benign. ... ")
    text = "Um. Okay, okay. Mm-hmm. The tumor is benign. ... "
    assert [(text[s.start : s.end], s.filler) for s in spans] == [
        ("Um.", True),
        ("Okay, okay.", True),
        ("Mm-hmm.", True),
        ("The tumor is benign.", False),
        ("...", True),
    ]


def test_span_is_a_value_object():
    assert Span(0, 3, False) == Span(0, 3, False)


def test_a_spoken_no_ends_its_sentence():
    assert texts("No. It hasn't spread.") == ["No.", "It hasn't spread."]
