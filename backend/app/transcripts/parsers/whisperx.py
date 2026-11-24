"""WhisperX JSON with diarization (`assign_word_speakers`): segments carrying words."""

from collections import Counter

from app.transcripts._json import dicts, num, text
from app.transcripts.parsers import ParseResult, language_warnings
from app.transcripts.schemas import Segment, Word

UNKNOWN = "unknown"
UNKNOWN_WARNING = "Some speech has no speaker label (shown as 'unknown'). Assign it by hand."


def _speaker(seg: dict) -> str | None:
    """Segment label, else the majority label of its words (first seen wins a tie)."""
    label = text(seg.get("speaker"))
    if label:
        return label
    counts = Counter(text(w.get("speaker")) for w in dicts(seg.get("words")))
    counts.pop("", None)
    return counts.most_common(1)[0][0] if counts else None


def _words(raw: list[dict], seg_start: float | None, seg_end: float | None) -> list[Word]:
    """Words WhisperX could not align (digits) inherit timing from their neighbours."""
    words = [
        Word(text=text(w.get("word")), start=num(w.get("start")), end=num(w.get("end")), confidence=num(w.get("score")))
        for w in raw
    ]
    for i, w in enumerate(words):
        if w.start is None:
            prev_end = next((p.end for p in reversed(words[:i]) if p.end is not None), None)
            w.start = prev_end if prev_end is not None else seg_start
        if w.end is None:
            next_start = next((n.start for n in words[i + 1 :] if n.start is not None), None)
            w.end = next_start if next_start is not None else seg_end
    return words


def parse(doc: dict) -> ParseResult:
    segments: list[Segment] = []
    unknown = False
    for seg in dicts(doc.get("segments")):
        label = _speaker(seg)
        if label is None:
            label, unknown = UNKNOWN, True
        start, end = num(seg.get("start")), num(seg.get("end"))
        segments.append(
            Segment(
                speaker_label=label,
                start=start,
                end=end,
                text=text(seg.get("text")).strip(),
                words=_words(dicts(seg.get("words")), start, end),
            )
        )

    language = text(doc.get("language")) or None
    ends = [s.end for s in segments if s.end is not None]
    return ParseResult(
        segments=segments,
        language=language,
        duration_s=max(ends) if ends else None,
        warnings=language_warnings(language) + ([UNKNOWN_WARNING] if unknown else []),
    )
