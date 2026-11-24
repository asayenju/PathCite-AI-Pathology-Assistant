"""The transcript import pipeline as one pure function: bytes in, TranscriptImport out.

Runs in a threadpool from the router. Never logs or echoes transcript text or vendor metadata:
only the fields of TranscriptImport leave this module.
"""

from app.config import Settings
from app.errors import ApiError
from app.transcripts.detect import load_transcript
from app.transcripts.normalize import build_turns
from app.transcripts.parsers import aws, deepgram, openai_diarized, whisperx
from app.transcripts.roles import summarize_speakers
from app.transcripts.schemas import Turn, TranscriptImport

# Modules, not functions, so `parse` is looked up at call time.
PARSERS = {
    "deepgram": deepgram,
    "aws_transcribe": aws,
    "whisperx": whisperx,
    "openai_diarized": openai_diarized,
}

OVERLAP_TOLERANCE_S = 0.5
OVERLAP_WARNING = "Some speech overlaps; turns may be out of order."
LOW_CONFIDENCE_SHARE = 0.2


def low_confidence_warning(pct: int) -> str:
    return f"{pct}% of words have low transcription confidence; check flagged words before relying on them."


def has_overlap(turns: list[Turn], tolerance: float = OVERLAP_TOLERANCE_S) -> bool:
    prev_end = None
    for turn in turns:
        if prev_end is not None and turn.start is not None and turn.start < prev_end - tolerance:
            return True
        if turn.end is not None:
            prev_end = turn.end
    return False


def low_confidence_pct(turns: list[Turn], threshold: float) -> float | None:
    """Share (0-100) of words with a confidence that fall below threshold; None if no confidences."""
    scores = [w.confidence for t in turns for w in t.words if w.confidence is not None]
    if not scores:
        return None
    return 100 * sum(c < threshold for c in scores) / len(scores)


def _dedupe(warnings: list[str]) -> list[str]:
    return list(dict.fromkeys(warnings))


def import_transcript(data: bytes, settings: Settings) -> TranscriptImport:
    source, doc = load_transcript(data)
    parsed = PARSERS[source].parse(doc)
    speakers, turns = build_turns(parsed.segments)
    if not turns:
        raise ApiError(422, "empty_transcript", "The transcript contains no speech")
    speakers, role_warnings = summarize_speakers(speakers, turns)

    extra: list[str] = []
    if has_overlap(turns):
        extra.append(OVERLAP_WARNING)
    pct = low_confidence_pct(turns, settings.asr_low_confidence)
    if pct is not None and pct > LOW_CONFIDENCE_SHARE * 100:
        extra.append(low_confidence_warning(round(pct)))

    return TranscriptImport(
        source=source,
        language=parsed.language,
        duration_s=parsed.duration_s,
        speakers=speakers,
        turns=turns,
        warnings=_dedupe([*parsed.warnings, *role_warnings, *extra]),
    )
