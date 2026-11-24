import copy
import json
from pathlib import Path

import pytest

from app.errors import ApiError
from app.transcripts.normalize import build_turns
from app.transcripts.parsers import deepgram
from tests.fixtures.transcripts.generate import DIALOGUE

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def turns_of(doc):
    return build_turns(deepgram.parse(doc).segments)


def expected_turn_texts():
    """The scripted dialogue with back-to-back lines by the same speaker merged."""
    out: list[tuple[int, str]] = []
    for spk, text in DIALOGUE:
        if out and out[-1][0] == spk:
            out[-1] = (spk, out[-1][1] + " " + text)
        else:
            out.append((spk, text))
    return [t for _, t in out]


def test_utterances_fixture():
    result = deepgram.parse(load("deepgram_utterances.json"))
    speakers, turns = build_turns(result.segments)
    assert [t.text for t in turns] == expected_turn_texts()
    assert [s.source_label for s in speakers] == ["speaker_0", "speaker_1"]
    assert result.language == "en" and result.warnings == []
    assert result.duration_s == pytest.approx(load("deepgram_utterances.json")["metadata"]["duration"])


def test_words_only_gives_same_turns_as_utterances():
    assert turns_of(load("deepgram_words_only.json")) == turns_of(load("deepgram_utterances.json"))


def test_empty_utterances_list_falls_back_to_words():
    doc = load("deepgram_utterances.json")
    doc["results"]["utterances"] = []
    assert turns_of(doc) == turns_of(load("deepgram_words_only.json"))


def test_punctuated_word_preferred_with_fallback_to_word():
    doc = load("deepgram_words_only.json")
    words = doc["results"]["channels"][0]["alternatives"][0]["words"]
    assert words[0]["punctuated_word"] == "Hi," and words[0]["word"] == "hi"
    del words[1]["punctuated_word"]
    _, turns = turns_of(doc)
    assert [w.text for w in turns[0].words[:2]] == ["Hi,", "thanks"]


def test_multichannel_without_diarization_uses_channels():
    speakers, turns = turns_of(load("deepgram_multichannel.json"))
    assert [s.source_label for s in speakers] == ["ch0", "ch1"]
    assert [t.text for t in turns] == expected_turn_texts()


def test_multichannel_words_only_interleaves_channels_by_time():
    doc = load("deepgram_multichannel.json")
    del doc["results"]["utterances"]
    speakers, turns = turns_of(doc)
    assert [s.source_label for s in speakers] == ["ch0", "ch1"]
    assert [t.text for t in turns] == expected_turn_texts()


def test_multichannel_with_diarization_combines_labels():
    doc = load("deepgram_multichannel.json")
    del doc["results"]["utterances"]
    for c, ch in enumerate(doc["results"]["channels"]):
        for w in ch["alternatives"][0]["words"]:
            w["speaker"] = 1 if c == 1 else 0
    speakers, _ = turns_of(doc)
    assert [s.source_label for s in speakers] == ["ch0:speaker_0", "ch1:speaker_1"]


def test_non_english_adds_warning():
    result = deepgram.parse(load("deepgram_spanish.json"))
    assert result.language == "es"
    assert len(result.warnings) == 1 and "'es'" in result.warnings[0]


def test_single_channel_without_speakers_is_undiarized():
    doc = load("deepgram_words_only.json")
    for w in doc["results"]["channels"][0]["alternatives"][0]["words"]:
        del w["speaker"]
    with pytest.raises(ApiError) as exc:
        deepgram.parse(doc)
    assert exc.value.code == "undiarized_transcript"


def test_confidence_and_timing_preserved():
    _, turns = turns_of(load("deepgram_utterances.json"))
    tumor_turn = next(t for t in turns if "adenocarcinoma" in t.text)
    assert tumor_turn.min_confidence == 0.41
    assert all(w.start is not None and w.end is not None and w.start < w.end for w in tumor_turn.words)


def test_wrong_types_do_not_crash():
    doc = copy.deepcopy(load("deepgram_words_only.json"))
    words = doc["results"]["channels"][0]["alternatives"][0]["words"]
    words[0]["start"] = "abc"
    words[1]["confidence"] = None
    words.append("not a dict")
    doc["metadata"] = "nope"
    result = deepgram.parse(doc)
    assert result.duration_s is None
    _, turns = build_turns(result.segments)
    assert turns[0].words[0].text == "Hi," and turns[0].words[0].start is None  # untimed word keeps its place


def test_request_id_not_carried():
    result = deepgram.parse(load("deepgram_utterances.json"))
    assert "SYNTHETIC0001" not in repr(result)
