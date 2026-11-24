"""AWS Transcribe / Transcribe Medical batch JSON (speaker diarization or channel identification).

Speakers come from, in priority: `results.audio_segments`, item-level `speaker_label`, the
older `results.speaker_labels.segments` (matched by time), then channel identification
(`results.channel_labels.channels`, or item-level `channel_label`). Job metadata
(`jobName`, `accountId`, S3 URIs) is never read.
"""

from dataclasses import dataclass

from app.errors import ApiError
from app.transcripts._json import as_dict, dicts, num, text
from app.transcripts.parsers import ParseResult, language_warnings
from app.transcripts.schemas import Segment, Word


@dataclass
class _Token:
    """One pronunciation item with any following punctuation attached to its text."""

    word: Word
    item: dict


def _key(x) -> str | None:
    """Item ids are ints in AWS output; accept numeric strings too."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    if isinstance(x, str) and x.strip():
        return x.strip()
    return None


def _tokens(items: list[dict]) -> list[_Token]:
    tokens: list[_Token] = []
    for it in items:
        alts = dicts(it.get("alternatives"))
        alt = alts[0] if alts else {}
        content = text(alt.get("content"))
        kind = it.get("type")
        if kind == "punctuation":
            if tokens and content:
                tokens[-1].word.text += content
            continue
        if kind != "pronunciation" or not content:
            continue
        tokens.append(
            _Token(
                word=Word(
                    text=content,
                    start=num(it.get("start_time")),
                    end=num(it.get("end_time")),
                    confidence=num(alt.get("confidence")),
                ),
                item=it,
            )
        )
    return tokens


def _audio_segment_speakers(results: dict) -> dict[str, str]:
    by_id: dict[str, str] = {}
    for seg in dicts(results.get("audio_segments")):
        label = text(seg.get("speaker_label"))
        if not label or not isinstance(seg.get("items"), list):
            continue
        for i in seg["items"]:
            k = _key(i)
            if k is not None:
                by_id.setdefault(k, label)
    return by_id


class _LegacySpeakers:
    """`speaker_labels.segments`: match a word by its exact start time, else by segment span."""

    def __init__(self, results: dict) -> None:
        self.by_start: dict[float, str] = {}
        self.spans: list[tuple[float, float, str]] = []
        for seg in dicts(as_dict(results.get("speaker_labels")).get("segments")):
            label = text(seg.get("speaker_label"))
            if not label:
                continue
            for it in dicts(seg.get("items")):
                s = num(it.get("start_time"))
                if s is not None:
                    self.by_start.setdefault(s, text(it.get("speaker_label")) or label)
            s, e = num(seg.get("start_time")), num(seg.get("end_time"))
            if s is not None and e is not None:
                self.spans.append((s, e, label))

    def __call__(self, start: float | None) -> str:
        if start is None:
            return ""
        if start in self.by_start:
            return self.by_start[start]
        return next((label for s, e, label in self.spans if s <= start <= e), "")


def _fill_gaps(labels: list[str]) -> list[str]:
    """Words no source labelled inherit the previous word's speaker (leading ones: the next)."""
    out = list(labels)
    last = ""
    for i, label in enumerate(out):
        out[i] = label or last
        last = out[i]
    nxt = ""
    for i in range(len(out) - 1, -1, -1):
        out[i] = out[i] or nxt
        nxt = out[i]
    return out


def _tagged_from_items(results: dict, tokens: list[_Token]) -> list[tuple[str, Word]]:
    """Speakers from diarization (audio_segments > item speaker_label > legacy segments)."""
    seg_by_id = _audio_segment_speakers(results)
    legacy = _LegacySpeakers(results)
    labels = []
    for t in tokens:
        k = _key(t.item.get("id"))
        labels.append(
            (seg_by_id.get(k, "") if k is not None else "")
            or text(t.item.get("speaker_label"))
            or legacy(t.word.start)
        )
    if not any(labels):
        return []
    return list(zip(_fill_gaps(labels), (t.word for t in tokens)))


def _tagged_from_channels(results: dict, tokens: list[_Token]) -> list[tuple[str, Word]]:
    tagged: list[tuple[str, Word]] = []
    for ch in dicts(as_dict(results.get("channel_labels")).get("channels")):
        label = text(ch.get("channel_label"))
        if label:
            tagged.extend((label, t.word) for t in _tokens(dicts(ch.get("items"))))
    if tagged:
        return tagged
    # Some outputs only tag the top-level items with their channel.
    labels = [text(t.item.get("channel_label")) for t in tokens]
    if not any(labels):
        return []
    return list(zip(_fill_gaps(labels), (t.word for t in tokens)))


def _segments(tagged: list[tuple[str, Word]]) -> list[Segment]:
    """Sort by time (an untimed word stays right after its predecessor), cut on speaker change."""
    keyed = []
    last = 0.0
    for label, word in tagged:
        last = word.start if word.start is not None else last
        keyed.append((last, label, word))
    keyed.sort(key=lambda t: t[0])  # stable

    segments: list[Segment] = []
    for _, label, word in keyed:
        if segments and segments[-1].speaker_label == label:
            segments[-1].words.append(word)
            if word.end is not None:
                segments[-1].end = word.end
            if segments[-1].start is None:
                segments[-1].start = word.start
        else:
            segments.append(Segment(speaker_label=label, start=word.start, end=word.end, text="", words=[word]))
    for s in segments:
        s.text = " ".join(w.text for w in s.words)
    return segments


def _language(doc: dict, results: dict) -> str | None:
    for x in (doc.get("languageCode"), doc.get("language_code"), results.get("language_code")):
        if text(x):
            return text(x)
    return None


def parse(doc: dict) -> ParseResult:
    results = as_dict(doc.get("results"))
    tokens = _tokens(dicts(results.get("items")))

    tagged = _tagged_from_items(results, tokens) or _tagged_from_channels(results, tokens)
    if tokens and not tagged:
        raise ApiError(
            422,
            "undiarized_transcript",
            "This AWS Transcribe output has no speaker labels. Re-run the job with speaker "
            "partitioning (ShowSpeakerLabels) or channel identification.",
        )

    ends = [w.end for _, w in tagged if w.end is not None]
    language = _language(doc, results)
    return ParseResult(
        segments=_segments(tagged),
        language=language,
        duration_s=max(ends) if ends else None,
        warnings=language_warnings(language),
    )
