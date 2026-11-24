"""OpenAI `diarized_json` (gpt-4o-transcribe-diarize). Field names are our reading of the
docs and unverified against real output, so everything is read defensively."""

from app.transcripts._json import dicts, num, text
from app.transcripts.parsers import ParseResult, language_warnings
from app.transcripts.parsers.whisperx import UNKNOWN, UNKNOWN_WARNING
from app.transcripts.schemas import Segment

SEGMENT_TYPE = "transcript.text.segment"


def parse(doc: dict) -> ParseResult:
    segments: list[Segment] = []
    unknown = False
    for seg in dicts(doc.get("segments")):
        kind = seg.get("type")
        if kind is not None and kind != SEGMENT_TYPE:
            continue
        label = text(seg.get("speaker")).strip()
        if not label:
            label, unknown = UNKNOWN, True
        segments.append(
            Segment(
                speaker_label=label,
                start=num(seg.get("start")),
                end=num(seg.get("end")),
                text=text(seg.get("text")).strip(),
                words=[],  # no word timings or confidences in this format
            )
        )

    language = text(doc.get("language")) or None
    return ParseResult(
        segments=segments,
        language=language,
        duration_s=num(doc.get("duration")),
        warnings=language_warnings(language) + ([UNKNOWN_WARNING] if unknown else []),
    )
