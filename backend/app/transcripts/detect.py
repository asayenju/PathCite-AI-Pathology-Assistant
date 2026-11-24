"""Decode an uploaded transcript and work out which speech-to-text tool produced it.

The format is sniffed from the JSON structure, never from the file name or content type.
Error messages never quote the file's content (it is patient conversation).
"""

import json
from typing import Any

from app.errors import ApiError
from app.transcripts.schemas import Source

SUPPORTED = "Deepgram, AWS Transcribe (Medical) or diarized Whisper (WhisperX, OpenAI diarized_json)"


def _unsupported(what: str) -> ApiError:
    return ApiError(422, "unsupported_transcript_format", f"{what} Supported: {SUPPORTED}.")


def parse_json(data: bytes) -> Any:
    if not data:
        raise ApiError(422, "empty_file", "The file is empty")
    try:
        # utf-8-sig: accept a UTF-8 byte-order mark (some Windows tools add one).
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ApiError(422, "invalid_json", "The file is not valid UTF-8 JSON") from None


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _list(x: Any) -> list:
    return x if isinstance(x, list) else []


def _dicts(x: Any) -> list[dict]:
    return [i for i in _list(x) if isinstance(i, dict)]


def detect_format(doc: Any) -> Source:
    if not isinstance(doc, dict):
        raise _unsupported("The JSON is not a transcript object.")

    results = _dict(doc.get("results"))
    segments = _dicts(doc.get("segments"))

    # Known formats we deliberately don't support (yet).
    if "TranscriptSegments" in _dict(doc.get("Conversation")):
        raise _unsupported("AWS HealthScribe output is not supported yet.")
    if any("ParticipantRole" in s for s in _dicts(doc.get("Transcript"))):
        raise _unsupported("AWS Transcribe Call Analytics output is not supported.")

    if isinstance(results.get("channels"), list):
        return "deepgram"
    if isinstance(results.get("items"), list) and ("jobName" in doc or "transcripts" in results):
        return "aws_transcribe"
    if any(s.get("type") == "transcript.text.segment" for s in segments):
        return "openai_diarized"

    words = [w for s in segments for w in _dicts(s.get("words"))] + _dicts(doc.get("word_segments"))
    if any("speaker" in x for x in [*segments, *words]):
        return "whisperx"
    # Plain Whisper / WhisperX without --diarize / OpenAI json or verbose_json.
    if segments or "word_segments" in doc or "text" in doc:
        raise ApiError(
            422,
            "undiarized_transcript",
            "This transcript has no speaker labels, so doctor and patient turns can't be told "
            "apart. Re-run with diarization (WhisperX with --diarize, or OpenAI diarized_json).",
        )
    raise _unsupported("Unrecognised transcript format.")


def _has_text(*texts: Any) -> bool:
    return any(isinstance(t, str) and t.strip() for t in texts)


def has_content(source: Source, doc: dict) -> bool:
    """Cheap check that the transcript contains any speech at all."""
    results = _dict(doc.get("results"))
    if source == "deepgram":
        alts = [a for ch in _dicts(results.get("channels")) for a in _dicts(ch.get("alternatives"))]
        return any(_dicts(a.get("words")) or _has_text(a.get("transcript")) for a in alts) or any(
            _has_text(u.get("transcript")) for u in _dicts(results.get("utterances"))
        )
    if source == "aws_transcribe":
        return any(i.get("type") == "pronunciation" for i in _dicts(results.get("items")))
    segments = _dicts(doc.get("segments"))
    return any(_has_text(s.get("text")) or _dicts(s.get("words")) for s in segments)


def load_transcript(data: bytes) -> tuple[Source, dict]:
    """Decode, detect and sanity-check an upload. Raises ApiError with a stable code."""
    doc = parse_json(data)
    source = detect_format(doc)
    if not has_content(source, doc):
        raise ApiError(422, "empty_transcript", "The transcript contains no speech")
    return source, doc
