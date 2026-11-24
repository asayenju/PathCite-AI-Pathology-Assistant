import copy
import json
from pathlib import Path

import pytest

from app.transcripts.normalize import build_turns
from app.transcripts.parsers import openai_diarized, whisperx
from tests.fixtures.transcripts.generate import DIALOGUE

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def expected_turn_texts():
    """The scripted dialogue with back-to-back lines by the same speaker merged."""
    out: list[tuple[int, str]] = []
    for spk, text in DIALOGUE:
        if out and out[-1][0] == spk:
            out[-1] = (spk, out[-1][1] + " " + text)
        else:
            out.append((spk, text))
    return [t for _, t in out]


# ---------------------------------------------------------------- WhisperX


def test_whisperx_fixture():
    doc = load("whisperx.json")
    result = whisperx.parse(doc)
    speakers, turns = build_turns(result.segments)
    assert [t.text for t in turns] == expected_turn_texts()
    assert [s.source_label for s in speakers] == ["SPEAKER_00", "SPEAKER_01"]
    assert result.language == "en" and result.warnings == []
    assert result.duration_s == pytest.approx(max(s["end"] for s in doc["segments"]))


def test_whisperx_segment_text_is_stripped_but_original():
    result = whisperx.parse(load("whisperx.json"))
    assert result.segments[0].text == DIALOGUE[0][1]


def test_whisperx_unaligned_digit_inherits_neighbour_timing():
    _, turns = build_turns(whisperx.parse(load("whisperx.json")).segments)
    turn = next(t for t in turns if "about 2 centimeters?" in t.text)
    i = next(i for i, w in enumerate(turn.words) if w.text == "2")
    prev, two, nxt = turn.words[i - 1 : i + 2]
    assert two.start is not None and two.end is not None
    assert prev.end <= two.start <= two.end <= nxt.start
    assert two.start == prev.end and two.end == nxt.start
    assert two.confidence is None


def test_whisperx_untimed_words_at_segment_edges_use_segment_bounds():
    seg = {"start": 1.0, "end": 3.0, "text": " 2 or 3", "speaker": "SPEAKER_00",
           "words": [{"word": "2"}, {"word": "or", "start": 1.5, "end": 1.7, "score": 0.9}, {"word": "3"}]}
    words = whisperx.parse({"segments": [seg]}).segments[0].words
    assert (words[0].start, words[0].end) == (1.0, 1.5)
    assert (words[2].start, words[2].end) == (1.7, 3.0)


def test_whisperx_score_becomes_confidence():
    _, turns = build_turns(whisperx.parse(load("whisperx.json")).segments)
    tumor_turn = next(t for t in turns if "adenocarcinoma" in t.text)
    assert tumor_turn.min_confidence == 0.41


def test_whisperx_unknown_speaker():
    result = whisperx.parse(load("whisperx_unknown_speaker.json"))
    speakers, turns = build_turns(result.segments)
    assert "unknown" in [s.source_label for s in speakers]
    assert len(result.warnings) == 1 and "unknown" in result.warnings[0]
    assert [t.text for t in turns] == expected_turn_texts()
    assert result.segments[3].speaker_label == "unknown"


def test_whisperx_segment_speaker_falls_back_to_word_majority():
    doc = load("whisperx.json")
    seg = doc["segments"][1]
    del seg["speaker"]
    seg["words"][0]["speaker"] = "SPEAKER_00"  # outvoted by the rest
    result = whisperx.parse(doc)
    assert result.segments[1].speaker_label == "SPEAKER_01"
    assert result.warnings == []


def test_whisperx_words_without_speaker_stay_in_segment():
    result = whisperx.parse(load("whisperx.json"))
    seg = next(s for s in result.segments if "about 2 centimeters?" in s.text)
    assert "2" in [w.text for w in seg.words]


def test_whisperx_non_english_warns():
    doc = load("whisperx.json")
    doc["language"] = "es"
    result = whisperx.parse(doc)
    assert result.language == "es"
    assert len(result.warnings) == 1 and "'es'" in result.warnings[0]


def test_whisperx_wrong_types_do_not_crash():
    doc = copy.deepcopy(load("whisperx.json"))
    doc["segments"][0]["words"][0]["start"] = "abc"
    doc["segments"][0]["words"][1]["score"] = None
    doc["segments"][0]["words"].append("not a dict")
    doc["segments"][1]["speaker"] = 7
    doc["segments"][2]["words"] = "nope"
    doc["segments"][2]["text"] = None
    doc["segments"].append(["junk"])
    doc["language"] = 3
    result = whisperx.parse(doc)
    assert result.language is None
    build_turns(result.segments)
    assert whisperx.parse({"segments": "x", "language": None}).segments == []


# ---------------------------------------------------------------- OpenAI diarized_json


def test_openai_fixture():
    doc = load("openai_diarized.json")
    result = openai_diarized.parse(doc)
    speakers, turns = build_turns(result.segments)
    assert [t.text for t in turns] == expected_turn_texts()
    assert [s.source_label for s in speakers] == ["A", "B"]
    assert all(t.min_confidence is None and t.words == [] for t in turns)
    assert result.duration_s == pytest.approx(doc["duration"])
    assert result.language is None and result.warnings == []


def test_openai_skips_other_segment_types():
    doc = load("openai_diarized.json")
    doc["segments"].insert(0, {"type": "transcript.text.delta", "speaker": "C", "text": "partial"})
    speakers, turns = build_turns(openai_diarized.parse(doc).segments)
    assert [s.source_label for s in speakers] == ["A", "B"]
    assert [t.text for t in turns] == expected_turn_texts()


def test_openai_missing_speaker_is_unknown():
    doc = load("openai_diarized.json")
    del doc["segments"][3]["speaker"]
    result = openai_diarized.parse(doc)
    speakers, turns = build_turns(result.segments)
    assert "unknown" in [s.source_label for s in speakers]
    assert len(result.warnings) == 1
    assert [t.text for t in turns] == expected_turn_texts()


def test_openai_non_english_warns():
    doc = load("openai_diarized.json")
    doc["language"] = "fr"
    result = openai_diarized.parse(doc)
    assert len(result.warnings) == 1 and "'fr'" in result.warnings[0]


def test_openai_wrong_types_do_not_crash():
    doc = copy.deepcopy(load("openai_diarized.json"))
    doc["duration"] = "long"
    doc["segments"][0]["start"] = {}
    doc["segments"][1]["text"] = 5
    doc["segments"].append(None)
    result = openai_diarized.parse(doc)
    assert result.duration_s is None
    build_turns(result.segments)
    assert openai_diarized.parse({"segments": {"a": 1}}).segments == []
