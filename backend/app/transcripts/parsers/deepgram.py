"""Deepgram pre-recorded JSON (diarize and/or multichannel, with or without utterances)."""

from app.errors import ApiError
from app.transcripts._json import as_dict, dicts, num, text
from app.transcripts.parsers import ParseResult, language_warnings
from app.transcripts.schemas import Segment, Word


def _label(channel: int, speaker, multichannel: bool) -> str | None:
    spk = f"speaker_{speaker}" if isinstance(speaker, int) and not isinstance(speaker, bool) else None
    if multichannel:
        return f"ch{channel}:{spk}" if spk else f"ch{channel}"
    return spk


def _word(w: dict) -> Word:
    return Word(
        text=text(w.get("punctuated_word")) or text(w.get("word")),
        start=num(w.get("start")),
        end=num(w.get("end")),
        confidence=num(w.get("confidence")),
    )


def parse(doc: dict) -> ParseResult:
    results = as_dict(doc.get("results"))
    channels = dicts(results.get("channels"))
    multichannel = len(channels) > 1
    language = (text(channels[0].get("detected_language")) or None) if channels else None

    utterances = dicts(results.get("utterances"))
    if utterances:
        segments = [
            Segment(
                speaker_label=_label(int(num(u.get("channel")) or 0), u.get("speaker"), multichannel) or "",
                start=num(u.get("start")),
                end=num(u.get("end")),
                text=text(u.get("transcript")),
                words=[_word(w) for w in dicts(u.get("words"))],
            )
            for u in utterances
        ]
    else:
        segments = _segments_from_words(channels, multichannel)

    if any(not s.speaker_label for s in segments):
        raise ApiError(
            422,
            "undiarized_transcript",
            "This Deepgram transcript has no speaker labels. Re-run with diarize=true "
            "(or multichannel=true when each person is on their own channel).",
        )

    return ParseResult(
        segments=segments,
        language=language,
        duration_s=num(as_dict(doc.get("metadata")).get("duration")),
        warnings=language_warnings(language),
    )


def _segments_from_words(channels: list[dict], multichannel: bool) -> list[Segment]:
    """No utterances: interleave every channel's words by time, then cut on speaker change."""
    tagged = []  # (sort time, label, word); an untimed word stays right after its predecessor
    for c, ch in enumerate(channels):
        alts = dicts(ch.get("alternatives"))
        last = 0.0
        for w in dicts(alts[0].get("words")) if alts else []:
            word = _word(w)
            last = word.start if word.start is not None else last
            tagged.append((last, _label(c, w.get("speaker"), multichannel) or "", word))
    tagged.sort(key=lambda t: t[0])  # stable

    segments: list[Segment] = []
    for _, label, word in tagged:
        if segments and segments[-1].speaker_label == label:
            segments[-1].words.append(word)
            segments[-1].end = word.end if word.end is not None else segments[-1].end
        else:
            segments.append(Segment(speaker_label=label, start=word.start, end=word.end, text="", words=[word]))
    for s in segments:
        s.text = " ".join(w.text for w in s.words)
    return segments
