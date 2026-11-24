import json
from pathlib import Path

from app.transcripts.normalize import build_turns
from app.transcripts.parsers import deepgram
from app.transcripts.roles import (
    LEXICON_SIZE,
    MANY_SPEAKERS_WARNING,
    ONE_SPEAKER_WARNING,
    count_medical_terms,
    summarize_speakers,
    truncate_preview,
)
from app.transcripts.schemas import Speaker, Turn

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"

DOCTOR_LINE = "The biopsy from your colonoscopy showed an adenocarcinoma in the sigmoid colon."
CHAT_LINE = "Okay, thank you. That sounds like a lot to take in."


def from_fixture(name):
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return build_turns(deepgram.parse(doc).segments)


def make(lines, labels=None, timed=False):
    """lines: [(speaker index, text)] -> (speakers, turns) with ids S1.. and labels spk_N."""
    n = 1 + max(i for i, _ in lines)
    labels = labels or [f"spk_{i}" for i in range(n)]
    speakers = [Speaker(id=f"S{i + 1}", source_label=labels[i]) for i in range(n)]
    turns = [
        Turn(
            id=f"t{k:04d}",
            speaker=f"S{i + 1}",
            start=float(k) if timed else None,
            end=float(k) + 0.5 if timed else None,
            text=text,
            is_final=True,
            words=[],
            min_confidence=None,
        )
        for k, (i, text) in enumerate(lines, start=1)
    ]
    return speakers, turns


def roles(out):
    return [s.suggested_role for s in out]


def test_two_speaker_fixture_suggests_doctor_and_patient():
    speakers, turns = from_fixture("deepgram_utterances.json")
    out, warnings = summarize_speakers(speakers, turns)
    by_label = {s.source_label: s for s in out}
    assert by_label["speaker_0"].id == "S1"
    assert by_label["speaker_0"].suggested_role == "doctor"
    assert by_label["speaker_1"].suggested_role == "patient"
    assert warnings == []


def test_fixture_summaries():
    speakers, turns = from_fixture("deepgram_utterances.json")
    out, _ = summarize_speakers(speakers, turns)
    assert sum(s.word_count for s in out) == sum(len(t.words) for t in turns)
    assert all(s.word_count > 0 for s in out)
    assert all(s.talk_time_s > 0 for s in out)
    for s in out:
        assert 1 <= len(s.preview) <= 2
        assert all(len(line) <= 120 for line in s.preview)
    s1 = out[0]
    first_turn = next(t for t in turns if t.speaker == "S1")
    assert s1.preview[0] == first_turn.text
    expected = round(sum(t.end - t.start for t in turns if t.speaker == "S1"), 2)
    assert s1.talk_time_s == expected


def test_word_count_falls_back_to_text_split():
    speakers, turns = make([(0, "one two three"), (1, "four five")])
    out, _ = summarize_speakers(speakers, turns)
    assert [s.word_count for s in out] == [3, 2]


def test_preview_truncation():
    long = "word " * 60 + "end"
    speakers, turns = make([(0, long), (0, "second"), (0, "third"), (1, "hi")])
    # Same speaker's consecutive turns stay separate here (no merging in summarize).
    out, _ = summarize_speakers(speakers, turns)
    preview = out[0].preview
    assert len(preview) == 2
    assert len(preview[0]) <= 120
    assert preview[0].endswith("…")
    assert preview[0][:-1].split() == ["word"] * len(preview[0][:-1].split())  # whole words only
    assert preview[1] == "second"


def test_truncate_preview_rules():
    assert truncate_preview("short") == "short"
    exact = "x" * 120
    assert truncate_preview(exact) == exact
    no_spaces = "y" * 300
    cut = truncate_preview(no_spaces)
    assert len(cut) == 120 and cut.endswith("…")


def test_tie_gives_no_suggestions():
    speakers, turns = make([(0, DOCTOR_LINE), (1, DOCTOR_LINE)])
    out, _ = summarize_speakers(speakers, turns)
    assert roles(out) == [None, None]


def test_lead_must_be_clear():
    # 4 hits vs 3 hits: not 2x, so no suggestion.
    speakers, turns = make([(0, "biopsy colonoscopy adenocarcinoma tumor"), (1, "biopsy scan tumor")])
    out, _ = summarize_speakers(speakers, turns)
    assert roles(out) == [None, None]


def test_too_few_hits_gives_no_suggestion():
    speakers, turns = make([(0, "the biopsy came back"), (1, CHAT_LINE)])
    out, _ = summarize_speakers(speakers, turns)
    assert roles(out) == [None, None]


def test_one_speaker():
    speakers, turns = from_fixture("deepgram_single_speaker.json")
    assert len(speakers) == 1
    out, warnings = summarize_speakers(speakers, turns)
    assert roles(out) == [None]
    assert warnings == [ONE_SPEAKER_WARNING]


def test_more_than_four_speakers_warns():
    speakers, turns = make([(i, CHAT_LINE) for i in range(5)])
    _, warnings = summarize_speakers(speakers, turns)
    assert warnings == [MANY_SPEAKERS_WARNING]
    speakers, turns = make([(i, CHAT_LINE) for i in range(4)])
    assert summarize_speakers(speakers, turns)[1] == []


def test_three_speakers_clear_doctor():
    speakers, turns = make([(0, DOCTOR_LINE), (1, CHAT_LINE), (2, "Can I ask a question too?")])
    out, _ = summarize_speakers(speakers, turns)
    assert roles(out) == ["doctor", None, None]


def test_unknown_speaker_never_gets_a_role():
    speakers, turns = make([(0, DOCTOR_LINE), (1, CHAT_LINE)], labels=["unknown", "spk_1"])
    out, _ = summarize_speakers(speakers, turns)
    assert out[0].suggested_role is None
    assert out[1].suggested_role is None  # the leader is unknown, so the other isn't a patient

    speakers, turns = make([(0, DOCTOR_LINE), (1, CHAT_LINE)], labels=["spk_0", "unknown"])
    out, _ = summarize_speakers(speakers, turns)
    assert roles(out) == ["doctor", None]


def test_no_timings_gives_zero_talk_time():
    speakers, turns = make([(0, DOCTOR_LINE), (1, CHAT_LINE)])
    out, _ = summarize_speakers(speakers, turns)
    assert [s.talk_time_s for s in out] == [0.0, 0.0]
    speakers, turns = make([(0, DOCTOR_LINE), (1, CHAT_LINE)], timed=True)
    out, _ = summarize_speakers(speakers, turns)
    assert [s.talk_time_s for s in out] == [0.5, 0.5]


def test_inputs_not_mutated():
    speakers, turns = from_fixture("deepgram_utterances.json")
    before = [s.model_dump() for s in speakers]
    turns_before = [t.model_dump() for t in turns]
    out, _ = summarize_speakers(speakers, turns)
    assert [s.model_dump() for s in speakers] == before
    assert [t.model_dump() for t in turns] == turns_before
    assert all(a is not b for a, b in zip(out, speakers))


def test_deterministic():
    speakers, turns = from_fixture("deepgram_utterances.json")
    assert summarize_speakers(speakers, turns) == summarize_speakers(speakers, turns)


def test_term_matching():
    assert count_medical_terms("Mismatch repair, MSI, and a CT scan.") == 3
    assert count_medical_terms("TUMOUR; Tumor! lymph-node") == 2  # "lymph-node" is one unknown token
    assert count_medical_terms("I went for a walk with my dog.") == 0
    assert 150 <= LEXICON_SIZE <= 250
