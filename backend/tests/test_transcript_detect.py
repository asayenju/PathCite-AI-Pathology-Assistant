import asyncio
import io
import json
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from app.errors import ApiError
from app.images import read_upload
from app.transcripts.detect import detect_format, load_transcript

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def code_for(data: bytes) -> str:
    with pytest.raises(ApiError) as exc:
        load_transcript(data)
    return exc.value.code


@pytest.mark.parametrize(
    "name, source",
    [
        ("deepgram_utterances.json", "deepgram"),
        ("deepgram_words_only.json", "deepgram"),
        ("deepgram_multichannel.json", "deepgram"),
        ("deepgram_spanish.json", "deepgram"),
        ("deepgram_single_speaker.json", "deepgram"),
        ("aws_audio_segments.json", "aws_transcribe"),
        ("aws_items_speaker_label.json", "aws_transcribe"),
        ("aws_legacy_speaker_segments.json", "aws_transcribe"),
        ("aws_channel_identification.json", "aws_transcribe"),
        ("aws_medical.json", "aws_transcribe"),
        ("whisperx.json", "whisperx"),
        ("whisperx_unknown_speaker.json", "whisperx"),
        ("openai_diarized.json", "openai_diarized"),
    ],
)
def test_supported_formats_detected(name, source):
    assert load_transcript(raw(name))[0] == source


def test_detection_ignores_whitespace_and_key_order():
    doc = json.loads(raw("aws_audio_segments.json"))
    reordered = json.dumps(dict(reversed(list(doc.items())))).encode()
    assert load_transcript(reordered)[0] == "aws_transcribe"


@pytest.mark.parametrize(
    "name, code",
    [
        ("whisper_plain.json", "undiarized_transcript"),
        ("aws_call_analytics.json", "unsupported_transcript_format"),
        ("aws_healthscribe.json", "unsupported_transcript_format"),
        ("deepgram_empty.json", "empty_transcript"),
        ("truncated.json", "invalid_json"),
    ],
)
def test_rejected_fixtures(name, code):
    assert code_for(raw(name)) == code


@pytest.mark.parametrize(
    "data, code",
    [
        (b"", "empty_file"),
        ("{\"text\": \"café\"}".encode("latin-1"), "invalid_json"),  # not UTF-8
        ('{"text": "hi"}'.encode("utf-16"), "invalid_json"),
        (b"not json at all", "invalid_json"),
        (b"[" * 100_000 + b"]" * 100_000, "invalid_json"),  # recursion bomb
        (b"[1, 2, 3]", "unsupported_transcript_format"),
        (b'"just a string"', "unsupported_transcript_format"),
        (b"{}", "unsupported_transcript_format"),
        (b'{"foo": {"bar": 1}}', "unsupported_transcript_format"),
        (b'{"text": "Hello doctor."}', "undiarized_transcript"),  # OpenAI response_format=json
        (b'{"segments": [], "word_segments": []}', "undiarized_transcript"),  # WhisperX, no --diarize
        (b'{"results": {"channels": [{"alternatives": [{"transcript": "", "words": []}]}]}}', "empty_transcript"),
        (b'{"jobName": "x", "results": {"transcripts": [], "items": []}}', "empty_transcript"),
        (b'{"segments": [{"type": "transcript.text.segment", "text": " ", "speaker": "A"}]}', "empty_transcript"),
    ],
)
def test_bad_inputs(data, code):
    assert code_for(data) == code


def test_utf8_bom_accepted():
    assert load_transcript(b"\xef\xbb\xbf" + raw("openai_diarized.json"))[0] == "openai_diarized"


def test_error_messages_never_quote_content():
    secret = b'{"text": "SECRET-PATIENT-WORDS"}'
    for data in (secret, secret[:-3], b'{"segments": [{"text": "SECRET-PATIENT-WORDS"}]}'):
        with pytest.raises(ApiError) as exc:
            load_transcript(data)
        assert "SECRET" not in exc.value.detail


def test_detect_format_rejects_non_objects():
    with pytest.raises(ApiError):
        detect_format(None)


def test_oversize_upload_is_413_without_full_buffering():
    limit = 1024
    stream = io.BytesIO(b"{" + b" " * (limit * 50) + b"}")
    with pytest.raises(ApiError) as exc:
        asyncio.run(read_upload(UploadFile(stream), limit))
    assert exc.value.status_code == 413 and exc.value.code == "file_too_large"
    assert stream.tell() == limit + 1  # stopped reading right after the cap
