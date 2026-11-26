"""Guards the triage gold labels for the sample transcripts (docs/PRD-jev-triage.md, "Sample transcripts")."""

import json
import re

import pytest

from app.config import Settings
from app.transcripts.service import import_transcript
from app.triage.segment import split_spans
from tests.fixtures.transcripts import generate
from tests.fixtures.transcripts.scenarios import SCENARIOS

IDS = [sc.id for sc in SCENARIOS]
BY_ID = {sc.id: sc for sc in SCENARIOS}
LABELS = {"skip", "claim", "transcription"}


def _imported(sc):
    data = (generate.REPO / "frontend/public" / generate.sample_path(sc)).read_bytes()
    return import_transcript(data, Settings())


def _doctor_turns(sc):
    result = _imported(sc)
    doctor = next(s.id for s in result.speakers if s.source_label == generate.DOCTOR_LABEL[sc.vendor])
    return [t for t in result.turns if t.speaker == doctor]


def _gold(sid):
    path = generate.REPO / generate.TRIAGE_KEYS_DIR / f"{sid}.json"
    assert path.is_file(), f"{path} missing: run python tests/fixtures/transcripts/generate.py"
    return json.loads(path.read_text(encoding="utf-8"))


def test_triage_keys_are_kept_out_of_public():
    assert not generate.TRIAGE_KEYS_DIR.as_posix().startswith("frontend/")
    public = generate.REPO / "frontend/public"
    for path in public.rglob("*.json"):
        doc = path.read_text(encoding="utf-8")
        assert '"intended"' not in doc and '"base_kind"' not in doc, path


@pytest.mark.parametrize("sid", IDS)
def test_gold_file_describes_its_sample(sid):
    gold = _gold(sid)
    assert gold["sample_id"] == sid
    assert gold["file"] == generate.sample_path(BY_ID[sid])
    assert "pathologist" in gold["notice"]


@pytest.mark.parametrize("sid", IDS)
def test_every_doctor_span_has_exactly_one_label(sid):
    turns = _doctor_turns(BY_ID[sid])
    expected = [
        (i, s.start, s.end, t.text[s.start : s.end])
        for i, t in enumerate(turns)
        for s in split_spans(t.text)
        if not s.filler
    ]
    labelled = [(g["doctor_turn_index"], g["start"], g["end"], g["quote"]) for g in _gold(sid)["spans"]]
    assert labelled == expected
    for g in _gold(sid)["spans"]:
        assert g["label"] in LABELS


@pytest.mark.parametrize("sid", IDS)
def test_one_transcription_error_plus_claims_and_skips(sid):
    spans = _gold(sid)["spans"]
    counts = {label: sum(g["label"] == label for g in spans) for label in LABELS}
    assert counts["transcription"] == 1
    assert counts["claim"] >= 1
    assert counts["skip"] >= 1
    (asr,) = [g for g in spans if g["label"] == "transcription"]
    assert asr["base_kind"] in {"claim", "skip"}
    assert asr["wrong"] in asr["quote"] and asr["intended"] and asr["wrong"] != asr["intended"]
    assert asr["intended_quote"] == asr["quote"].replace(asr["wrong"], asr["intended"])
    for g in spans:
        if g["label"] != "transcription":
            assert set(g) == {"doctor_turn_index", "start", "end", "quote", "label"}


@pytest.mark.parametrize("sid", IDS)
def test_every_fact_check_key_is_a_claim(sid):
    spans = _gold(sid)["spans"]
    for k in BY_ID[sid].keys:
        (g,) = [g for g in spans if k.span in g["quote"]]
        assert "claim" in (g["label"], g.get("base_kind")), k.span


PLAN = re.compile(r"^(?:next |so )?(?:we'll|we will|i'll|let's)\b", re.IGNORECASE)


@pytest.mark.parametrize("sid", IDS)
def test_keys_are_not_plans(sid):
    for k in BY_ID[sid].keys:
        assert not PLAN.match(k.span), k.span


@pytest.mark.parametrize("sid", [sc.id for sc in SCENARIOS if sc.vendor != "openai_diarized"])
def test_planted_error_has_low_confidence_only_where_planted(sid):
    sc = BY_ID[sid]
    wrong = sc.asr_error.wrong
    all_turns = _imported(sc).turns
    hits = [t for t in all_turns if wrong in t.text]
    assert len(hits) == 1
    (turn,) = hits
    start = turn.text.index(wrong)
    # Words are space-separated in the turn text, in order: map each to its offset.
    offsets, pos = [], 0
    for w in turn.words:
        pos = turn.text.index(w.text, pos)
        offsets.append(pos)
        pos += len(w.text)
    planted = [w for w, o in zip(turn.words, offsets) if start <= o < start + len(wrong)]
    assert [w.text.strip(".,?!").lower() for w in planted] == wrong.lower().split()
    assert all(w.confidence == sc.asr_error.confidence for w in planted)
    # The same words elsewhere in the conversation keep a normal score.
    others = {w.lower() for w in wrong.split()}
    planted_ids = {id(w) for w in planted}
    for t in all_turns:
        for w in t.words:
            if w.text.strip(".,?!").lower() in others and id(w) not in planted_ids:
                assert w.confidence is None or w.confidence >= 0.6, (t.id, w.text)
