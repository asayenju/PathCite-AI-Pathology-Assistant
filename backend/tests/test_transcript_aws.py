import copy
import json
from pathlib import Path

import pytest

from app.errors import ApiError
from app.transcripts.normalize import build_turns
from app.transcripts.parsers import aws
from tests.fixtures.transcripts.generate import DIALOGUE

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
DIARIZED = ["aws_audio_segments.json", "aws_items_speaker_label.json", "aws_legacy_speaker_segments.json"]


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def turns_of(doc):
    return build_turns(aws.parse(doc).segments)


def expected_turn_texts():
    """The scripted dialogue with back-to-back lines by the same speaker merged."""
    out: list[tuple[int, str]] = []
    for spk, text in DIALOGUE:
        if out and out[-1][0] == spk:
            out[-1] = (spk, out[-1][1] + " " + text)
        else:
            out.append((spk, text))
    return [t for _, t in out]


@pytest.mark.parametrize("name", [*DIARIZED, "aws_medical.json"])
def test_diarized_layouts_give_the_dialogue(name):
    speakers, turns = turns_of(load(name))
    assert [t.text for t in turns] == expected_turn_texts()
    assert [s.source_label for s in speakers] == ["spk_0", "spk_1"]


def test_all_diarized_layouts_give_identical_turns():
    reference = turns_of(load("aws_audio_segments.json"))
    for name in [*DIARIZED[1:], "aws_medical.json"]:
        assert turns_of(load(name)) == reference, name


def test_channel_identification_maps_to_channels():
    speakers, turns = turns_of(load("aws_channel_identification.json"))
    assert [s.source_label for s in speakers] == ["ch_0", "ch_1"]
    assert [t.text for t in turns] == expected_turn_texts()


def test_item_level_channel_label_fallback():
    doc = load("aws_channel_identification.json")
    channels = doc["results"].pop("channel_labels")["channels"]
    starts = {it["start_time"]: ch["channel_label"] for ch in channels for it in ch["items"] if "start_time" in it}
    for it in doc["results"]["items"]:
        if "start_time" in it:
            it["channel_label"] = starts[it["start_time"]]
    speakers, turns = turns_of(doc)
    assert [s.source_label for s in speakers] == ["ch_0", "ch_1"]
    assert [t.text for t in turns] == expected_turn_texts()


def test_punctuation_attaches_to_previous_word():
    _, turns = turns_of(load("aws_audio_segments.json"))
    assert [w.text for w in turns[0].words[:2]] == ["Hi,", "thanks"]
    assert turns[0].words[4].text == "in."
    assert all(" ." not in t.text and " ," not in t.text and " ?" not in t.text for t in turns)


def test_string_numbers_parsed():
    result = aws.parse(load("aws_audio_segments.json"))
    word = result.segments[0].words[0]
    assert isinstance(word.start, float) and isinstance(word.end, float) and isinstance(word.confidence, float)
    assert isinstance(result.segments[0].start, float)
    last_end = load("aws_audio_segments.json")["results"]["items"][-2]["end_time"]
    assert result.duration_s == pytest.approx(float(last_end))


def test_confidence_and_timing_preserved():
    _, turns = turns_of(load("aws_audio_segments.json"))
    tumor_turn = next(t for t in turns if "adenocarcinoma" in t.text)
    assert tumor_turn.min_confidence == 0.41
    assert all(w.start is not None and w.end is not None and w.start < w.end for w in tumor_turn.words)


def test_language_and_warning():
    doc = load("aws_audio_segments.json")
    assert aws.parse(doc).language is None
    doc["results"]["language_code"] = "es-US"
    result = aws.parse(doc)
    assert result.language == "es-US" and len(result.warnings) == 1
    doc["languageCode"] = "en-US"
    result = aws.parse(doc)
    assert result.language == "en-US" and result.warnings == []


def test_no_speakers_is_undiarized():
    doc = load("aws_legacy_speaker_segments.json")
    del doc["results"]["speaker_labels"]
    with pytest.raises(ApiError) as exc:
        aws.parse(doc)
    assert exc.value.code == "undiarized_transcript"


@pytest.mark.parametrize("name", [*DIARIZED, "aws_channel_identification.json", "aws_medical.json"])
def test_metadata_not_carried(name):
    doc = load(name)
    doc["results"]["transcripts"][0]["transcript_uri"] = "s3://SYNTHETIC-bucket/123456789012/out.json"
    result = aws.parse(doc)
    speakers, turns = build_turns(result.segments)
    blob = repr(result) + repr(speakers) + repr(turns)
    for leaked in ("SYNTHETIC-PATIENT-NAME", "123456789012", "s3://"):
        assert leaked not in blob


def test_wrong_types_do_not_crash():
    doc = copy.deepcopy(load("aws_audio_segments.json"))
    items = doc["results"]["items"]
    items[0]["start_time"] = "abc"
    items[2]["alternatives"][0]["confidence"] = None
    items[3]["alternatives"] = "nope"
    items.append("not a dict")
    items.append({"type": "pronunciation"})
    doc["results"]["audio_segments"].append({"speaker_label": 5, "items": "x"})
    doc["results"]["audio_segments"].append("nope")
    doc["results"]["speaker_labels"] = []
    doc["languageCode"] = 7
    result = aws.parse(doc)
    assert result.language is None
    _, turns = build_turns(result.segments)
    assert turns[0].words[0].text == "Hi," and turns[0].words[0].start is None  # untimed word keeps its place


@pytest.mark.parametrize("results", [None, "x", {}, {"items": "x"}, {"items": []}])
def test_garbage_results_give_no_segments(results):
    assert aws.parse({"results": results}).segments == []


def test_legacy_matches_by_segment_span_when_start_differs():
    doc = load("aws_legacy_speaker_segments.json")
    word = next(it for it in doc["results"]["items"] if it["alternatives"][0]["content"] == "Okay")
    word["start_time"] = f"{float(word['start_time']) + 0.001:.3f}"  # no exact item match, still in span
    _, turns = turns_of(doc)
    assert [t.text for t in turns] == expected_turn_texts()
