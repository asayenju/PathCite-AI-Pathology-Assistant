import unicodedata

from app.transcripts.normalize import TurnBuilder, build_turns
from app.transcripts.schemas import Segment, Word


def seg(speaker, start, text, conf=0.9, end=None, is_final=True):
    words = [
        Word(text=t, start=start + i * 0.3, end=start + i * 0.3 + 0.25, confidence=conf)
        for i, t in enumerate(text.split())
    ]
    end = end if end is not None else (words[-1].end if words else start)
    return Segment(speaker_label=speaker, start=start, end=end, text=text, words=words, is_final=is_final)


CONVO = [
    seg("spk_0", 0.0, "Hi, thanks for coming in."),
    seg("spk_1", 2.0, "Thanks."),
    seg("spk_0", 3.0, "We got the biopsy results."),
    seg("spk_0", 5.0, "It showed an adenocarcinoma.", conf=0.41),
    seg("spk_1", 8.0, "Um, has it spread?"),
]


def test_same_speaker_segments_merge_and_speaker_change_splits():
    speakers, turns = build_turns(CONVO)
    assert [t.speaker for t in turns] == ["S1", "S2", "S1", "S2"]
    assert turns[2].text == "We got the biopsy results. It showed an adenocarcinoma."
    assert turns[2].start == 3.0 and turns[2].end == CONVO[3].end
    assert len(turns[2].words) == 9
    assert [(s.id, s.source_label) for s in speakers] == [("S1", "spk_0"), ("S2", "spk_1")]


def test_chunked_appends_equal_single_append():
    single = build_turns(CONVO)
    for cuts in ([1], [2, 4], [1, 2, 3, 4]):
        b = TurnBuilder()
        prev = 0
        for c in [*cuts, len(CONVO)]:
            b.append(CONVO[prev:c])
            prev = c
        assert b.finish() == single


def test_out_of_order_segments_are_sorted():
    shuffled = [CONVO[3], CONVO[0], CONVO[4], CONVO[2], CONVO[1]]
    assert build_turns(shuffled) == build_turns(CONVO)


def test_ids_are_deterministic_and_ordered():
    _, turns = build_turns(CONVO)
    assert [t.id for t in turns] == ["t0001", "t0002", "t0003", "t0004"]
    assert build_turns(CONVO) == build_turns(CONVO)


def test_speaker_ids_follow_first_appearance_not_label():
    _, turns = build_turns([seg("SPEAKER_01", 0.0, "Hello."), seg("SPEAKER_00", 1.0, "Hi.")])
    assert [t.speaker for t in turns] == ["S1", "S2"]


def test_text_kept_verbatim_except_nfc_and_outer_whitespace():
    decomposed = unicodedata.normalize("NFD", "Um, the café visit, uh, yes.")
    _, turns = build_turns([seg("A", 0.0, "  " + decomposed + " ")])
    assert turns[0].text == unicodedata.normalize("NFC", "Um, the café visit, uh, yes.")
    assert all(unicodedata.is_normalized("NFC", w.text) for w in turns[0].words)


def test_is_final_true_for_file_imports_and_false_if_any_interim():
    _, turns = build_turns(CONVO)
    assert all(t.is_final for t in turns)
    _, turns = build_turns([seg("A", 0.0, "one"), seg("A", 1.0, "two", is_final=False)])
    assert turns[0].is_final is False


def test_min_confidence():
    _, turns = build_turns(CONVO)
    assert turns[2].min_confidence == 0.41
    no_conf = Segment(speaker_label="A", start=0.0, end=1.0, text="Hello there.")
    _, turns = build_turns([no_conf])
    assert turns[0].min_confidence is None
    assert turns[0].words == []


def test_missing_start_keeps_arrival_order():
    a = seg("A", 1.0, "first")
    b = Segment(speaker_label="B", start=None, end=None, text="second")
    c = seg("A", 2.0, "third")
    _, turns = build_turns([a, b, c])
    assert [t.text for t in turns] == ["first", "second", "third"]
    assert turns[1].start is None and turns[1].end is None


def test_empty_segments_skipped_and_text_falls_back_to_words():
    empty = Segment(speaker_label="A", start=0.0, end=0.1, text="  ")
    words_only = Segment(
        speaker_label="A", start=1.0, end=2.0, text="", words=[Word(text="Okay.", start=1.0, end=1.4, confidence=0.9)]
    )
    speakers, turns = build_turns([empty, words_only])
    assert [t.text for t in turns] == ["Okay."]
    assert len(speakers) == 1


def test_no_segments():
    assert build_turns([]) == ([], [])
