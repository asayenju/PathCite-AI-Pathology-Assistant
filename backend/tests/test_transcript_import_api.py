"""End-to-end tests for POST /transcripts/import: fixtures, errors, warnings and privacy."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.transcripts import router as transcripts_router
from app.transcripts import service
from app.transcripts.parsers import deepgram
from app.transcripts.roles import ONE_SPEAKER_WARNING

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
URL = "/transcripts/import"

SUPPORTED = {
    "deepgram_utterances.json": "deepgram",
    "deepgram_words_only.json": "deepgram",
    "deepgram_multichannel.json": "deepgram",
    "deepgram_spanish.json": "deepgram",
    "deepgram_single_speaker.json": "deepgram",
    "aws_audio_segments.json": "aws_transcribe",
    "aws_items_speaker_label.json": "aws_transcribe",
    "aws_legacy_speaker_segments.json": "aws_transcribe",
    "aws_channel_identification.json": "aws_transcribe",
    "aws_medical.json": "aws_transcribe",
    "whisperx.json": "whisperx",
    "whisperx_unknown_speaker.json": "whisperx",
    "openai_diarized.json": "openai_diarized",
}
TWO_SPEAKER = [
    name for name in SUPPORTED if name not in {"deepgram_single_speaker.json", "deepgram_spanish.json", "whisperx_unknown_speaker.json"}
]
REJECTED = {
    "whisper_plain.json": "undiarized_transcript",
    "aws_call_analytics.json": "unsupported_transcript_format",
    "aws_healthscribe.json": "unsupported_transcript_format",
    "deepgram_empty.json": "empty_transcript",
    "truncated.json": "invalid_json",
}
SECRETS = ["SYNTHETIC-PATIENT-NAME", "123456789012", "SYNTHETIC0001", "s3://"]


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def post(client, data: bytes, name="t.json", ctype="application/json"):
    return client.post(URL, files={"file": (name, data, ctype)})


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def has_low_conf_warning(warnings: list[str]) -> bool:
    return any("low transcription confidence" in w for w in warnings)


# ---- supported fixtures ----


@pytest.mark.parametrize("name,source", SUPPORTED.items())
def test_supported_fixture_imports(client, name, source):
    # Detection is from content: a misleading file name and content type don't matter.
    r = post(client, fixture(name), name="notes.txt", ctype="text/plain")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == source
    assert body["turns"] and all(t["text"] for t in body["turns"])
    assert body["notice"].startswith("Research use only")
    assert not has_low_conf_warning(body["warnings"])
    assert service.OVERLAP_WARNING not in body["warnings"]


@pytest.mark.parametrize("name", TWO_SPEAKER)
def test_two_speaker_fixtures_suggest_doctor(client, name):
    body = post(client, fixture(name)).json()
    assert len(body["speakers"]) == 2
    assert sorted(s["suggested_role"] for s in body["speakers"]) == ["doctor", "patient"]
    assert body["warnings"] == []


@pytest.mark.parametrize(
    "name,needle",
    [
        ("deepgram_spanish.json", "Detected language is 'es'"),
        ("deepgram_single_speaker.json", ONE_SPEAKER_WARNING),
        ("whisperx_unknown_speaker.json", "unknown"),
    ],
)
def test_fixture_warnings(client, name, needle):
    body = post(client, fixture(name)).json()
    assert any(needle in w for w in body["warnings"]), body["warnings"]


def test_low_confidence_words_survive(client):
    body = post(client, fixture("deepgram_utterances.json")).json()
    low = {w["text"].strip(",.").lower(): w["confidence"] for t in body["turns"] for w in t["words"] if w["confidence"] is not None and w["confidence"] < 0.6}
    assert low == {"adenocarcinoma": 0.41, "sigmoid": 0.55, "mismatch": 0.58}


# ---- rejected inputs ----


@pytest.mark.parametrize("name,code", REJECTED.items())
def test_rejected_fixture(client, name, code):
    r = post(client, fixture(name))
    assert r.status_code == 422
    assert r.json()["code"] == code
    assert set(r.json()) == {"detail", "code"}


@pytest.mark.parametrize(
    "data,code",
    [(b"", "empty_file"), (b"\xff\xfe\x00{\x80\x81", "invalid_json"), (b"[1, 2]", "unsupported_transcript_format")],
    ids=["empty", "non_utf8", "not_object"],
)
def test_bad_bytes(client, data, code):
    r = post(client, data)
    assert r.status_code == 422 and r.json()["code"] == code


def test_oversize_is_413(monkeypatch):
    monkeypatch.setenv("MAX_TRANSCRIPT_MB", "1")
    data = fixture("deepgram_utterances.json") + b" " * (1024 * 1024)
    with TestClient(app) as c:
        r = post(c, data)
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"


def test_missing_field_is_422(client):
    r = client.post(URL, files={"transcript": ("t.json", fixture("whisperx.json"), "application/json")})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"


def test_no_turns_after_parsing_is_empty_transcript(client, monkeypatch):
    real = deepgram.parse

    def no_segments(doc):
        result = real(doc)
        result.segments = []
        return result

    monkeypatch.setattr(deepgram, "parse", no_segments)
    r = post(client, fixture("deepgram_utterances.json"))
    assert r.status_code == 422 and r.json()["code"] == "empty_transcript"


# ---- works without the image model ----


def test_import_works_without_model(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.pt"))
    with TestClient(app) as c:
        assert c.get("/health").json()["model_loaded"] is False
        r = post(c, fixture("aws_medical.json"))
    assert r.status_code == 200 and r.json()["source"] == "aws_transcribe"


# ---- privacy ----


def test_large_transcript_never_spooled_to_disk(client, monkeypatch):
    seen = {}
    real = transcripts_router.read_upload

    async def spy(file, max_bytes):
        seen["rolled"] = file.file._rolled  # SpooledTemporaryFile: True once written to disk
        return await real(file, max_bytes)

    monkeypatch.setattr(transcripts_router, "read_upload", spy)
    data = fixture("deepgram_utterances.json") + b" " * (1536 * 1024)  # whitespace padding, still valid JSON
    assert len(data) > 1024 * 1024  # larger than Starlette's default 1 MB spool limit
    assert post(client, data).status_code == 200
    assert seen["rolled"] is False


def test_unexpected_parser_error_leaks_nothing(monkeypatch, caplog):
    def boom(doc):
        raise RuntimeError("SECRET-PATIENT-WORDS")

    monkeypatch.setattr(deepgram, "parse", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = post(c, fixture("deepgram_utterances.json"))
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "code": "internal_error"}
    assert "SECRET-PATIENT-WORDS" not in r.text
    assert "SECRET-PATIENT-WORDS" not in caplog.text
    # No transcript text from the upload ends up in the logs either.
    assert "biopsy" not in caplog.text.lower()


def test_successful_import_logs_no_content(client, caplog):
    caplog.set_level("DEBUG")
    assert post(client, fixture("deepgram_utterances.json")).status_code == 200
    assert "biopsy" not in caplog.text.lower()


@pytest.mark.parametrize("name", [n for n in SUPPORTED if n.startswith(("aws", "deepgram"))])
def test_vendor_metadata_not_echoed(client, name):
    r = post(client, fixture(name))
    assert r.status_code == 200
    for secret in SECRETS:
        assert secret not in r.text


def test_fixtures_actually_carry_metadata():
    # Guard: the metadata test above is only meaningful if the fixtures contain it.
    assert b"SYNTHETIC-PATIENT-NAME" in fixture("aws_medical.json")
    assert b"123456789012" in fixture("aws_audio_segments.json")
    assert b"SYNTHETIC0001" in fixture("deepgram_utterances.json")


# ---- computed warnings ----


def _deepgram_doc() -> dict:
    return json.loads(fixture("deepgram_utterances.json"))


def _all_deepgram_words(doc: dict):
    for u in doc["results"]["utterances"]:
        yield from u["words"]
    for ch in doc["results"]["channels"]:
        for alt in ch["alternatives"]:
            yield from alt["words"]


def test_low_confidence_warning(client):
    doc = _deepgram_doc()
    words = list(_all_deepgram_words(doc))
    for i, w in enumerate(words):
        if i % 3 == 0:
            w["confidence"] = 0.1
    body = post(client, json.dumps(doc).encode()).json()
    lows = [w for w in body["warnings"] if has_low_conf_warning([w])]
    assert len(lows) == 1
    pct = int(lows[0].split("%")[0])
    assert 30 <= pct <= 40
    assert lows[0] == service.low_confidence_warning(pct)


def test_low_confidence_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("ASR_LOW_CONFIDENCE", "0.95")  # most synthetic words are below 0.95
    with TestClient(app) as c:
        body = post(c, fixture("deepgram_utterances.json")).json()
    assert has_low_conf_warning(body["warnings"])


def test_overlap_warning(client):
    doc = _deepgram_doc()
    second = doc["results"]["utterances"][1]
    first_end = doc["results"]["utterances"][0]["end"]
    shift = second["start"] - (first_end - 1.5)  # starts 1.5 s before the first one ends
    second["start"] -= shift
    second["end"] -= shift
    for w in second["words"]:
        w["start"] -= shift
        w["end"] -= shift
    body = post(client, json.dumps(doc).encode()).json()
    assert body["warnings"].count(service.OVERLAP_WARNING) == 1


def test_small_overlap_is_tolerated():
    from app.transcripts.schemas import Turn

    def turn(start, end):
        return Turn(id="t", speaker="S1", start=start, end=end, text="x", is_final=True, words=[], min_confidence=None)

    assert not service.has_overlap([turn(0, 4.0), turn(3.6, 6), turn(None, None), turn(6.0, 7)])
    assert service.has_overlap([turn(0, 4.0), turn(None, None), turn(3.4, 6)])


def test_warnings_are_deduplicated(client, monkeypatch):
    real = deepgram.parse

    def doubled(doc):
        result = real(doc)
        result.warnings = result.warnings * 2 + [service.OVERLAP_WARNING]
        return result

    monkeypatch.setattr(deepgram, "parse", doubled)
    body = post(client, fixture("deepgram_spanish.json")).json()
    assert len(body["warnings"]) == len(set(body["warnings"]))
    assert body["warnings"][0].startswith("Detected language")


# ---- docs ----


def test_endpoint_in_openapi():
    op = app.openapi()["paths"][URL]["post"]
    assert "Research use only" in op["description"]
