"""Guards the sample transcripts and their answer keys (docs/PRD-transcript-samples.md)."""

import json
import re

import pytest

from app.config import Settings
from app.transcripts.service import import_transcript
from tests.fixtures.transcripts import generate
from tests.fixtures.transcripts.scenarios import DOCTOR, FOLDERS, SCENARIOS

SAMPLES = generate.build_samples()
IDS = [sc.id for sc in SCENARIOS]
BY_ID = {sc.id: sc for sc in SCENARIOS}

DOCTOR_LABEL = generate.DOCTOR_LABEL  # vendor label of speaker 0 (the doctor) after parsing
ALLOWED_TEXT = re.compile(r"^[A-Za-z0-9 '+\-.,?!]+$")


def _imported(sc):
    data = (generate.REPO / "frontend/public" / generate.sample_path(sc)).read_bytes()
    return import_transcript(data, Settings())


@pytest.mark.parametrize("rel", sorted(SAMPLES))
def test_output_is_up_to_date(rel):
    path = generate.REPO / rel
    assert path.is_file(), f"{rel} missing: run python tests/fixtures/transcripts/generate.py"
    assert path.read_text(encoding="utf-8") == generate.dump(SAMPLES[rel]), (
        f"{rel} is stale: run python tests/fixtures/transcripts/generate.py"
    )


def test_generation_is_deterministic():
    assert generate.build_samples() == SAMPLES


def test_manifest_lists_nine_samples_in_four_folders():
    index = json.loads((generate.REPO / generate.SAMPLES_DIR / "index.json").read_text(encoding="utf-8"))
    assert "Not medical advice" in index["notice"]
    assert [f["name"] for f in index["folders"]] == [name for name, _ in FOLDERS]
    items = [it for f in index["folders"] for it in f["items"]]
    assert len(items) == 9
    assert sorted(it["id"] for it in items) == sorted(IDS)
    for it in items:
        assert (generate.REPO / "frontend/public" / it["file"]).is_file()
        assert it["vendor"] == BY_ID[it["id"]].vendor


def test_keys_are_kept_out_of_public():
    public = [rel for rel in SAMPLES if rel.startswith("frontend/")]
    for rel in public:
        assert '"verdict"' not in generate.dump(SAMPLES[rel])


@pytest.mark.parametrize("sid", IDS)
def test_key_file_carries_review_notice(sid):
    key = json.loads((generate.REPO / generate.KEYS_DIR / f"{sid}.json").read_text(encoding="utf-8"))
    assert "pathologist" in key["notice"]
    assert all(k["speaker"] == "doctor" for k in key["keys"])


@pytest.mark.parametrize("sid", IDS)
def test_scenario_script_is_well_formed(sid):
    sc = BY_ID[sid]
    assert 12 <= len(sc.lines) <= 20
    assert sc.lines[0][0] == DOCTOR
    assert {spk for spk, _, _ in sc.lines} == {0, 1}
    for spk, text, keys in sc.lines:
        assert ALLOWED_TEXT.match(text), text
        assert spk == DOCTOR or not keys, "only doctor lines carry keys"
        for k in keys:
            assert k.span in text
    assert {k.verdict for k in sc.keys} == {"accurate", "incomplete", "inaccurate"}
    for k in sc.keys:
        assert ALLOWED_TEXT.match(k.span), k.span
        assert k.correct and k.source_hint
        assert (k.issue is None) == (k.verdict == "accurate")
    assert len(sc.low_confidence) == 1
    ((word, score),) = sc.low_confidence.items()
    assert 0.4 <= score <= 0.58
    assert any(word in text.lower() for _, text, _ in sc.lines)


@pytest.mark.parametrize("sid", IDS)
def test_sample_imports_with_doctor_suggested(sid):
    sc = BY_ID[sid]
    result = _imported(sc)
    assert len(result.speakers) == 2
    roles = {s.source_label: s.suggested_role for s in result.speakers}
    assert roles.pop(DOCTOR_LABEL[sc.vendor]) == "doctor"
    assert list(roles.values()) == ["patient"]
    assert result.warnings == []


@pytest.mark.parametrize("sid", IDS)
def test_every_key_span_is_in_exactly_one_doctor_turn(sid):
    sc = BY_ID[sid]
    result = _imported(sc)
    doctor = next(s.id for s in result.speakers if s.source_label == DOCTOR_LABEL[sc.vendor])
    for k in sc.keys:
        assert sum(t.text.count(k.span) for t in result.turns) == 1, k.span
        (turn,) = [t for t in result.turns if k.span in t.text]
        assert turn.speaker == doctor


@pytest.mark.parametrize("sid", [sc.id for sc in SCENARIOS if sc.vendor != "openai_diarized"])
def test_low_confidence_word_is_flagged(sid):
    result = _imported(BY_ID[sid])
    assert any(t.min_confidence is not None and t.min_confidence < 0.6 for t in result.turns)
