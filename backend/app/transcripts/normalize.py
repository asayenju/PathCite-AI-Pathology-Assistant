"""Vendor-neutral turn building, incremental so a future streaming endpoint can reuse it."""

import bisect
import unicodedata
from collections.abc import Iterable

from app.transcripts.schemas import Segment, Speaker, Turn


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def _segment_text(seg: Segment) -> str:
    return _nfc(seg.text) or _nfc(" ".join(w.text for w in seg.words))


class TurnBuilder:
    """Collects segments (in any number of `append` calls) and builds speaker turns.

    Segments are kept sorted by start time (stable, so equal or missing starts keep arrival
    order); a segment without a start sorts right after the segment before it. Adjacent
    segments from the same speaker merge into one turn. Output is a pure function of the
    segments seen, so chunked appends give exactly the same result as one append.
    """

    def __init__(self) -> None:
        self._keys: list[tuple[float, int]] = []
        self._segments: list[Segment] = []
        self._seq = 0
        self._last_start = 0.0

    def append(self, segments: Iterable[Segment]) -> None:
        for seg in segments:
            if not _nfc(seg.text) and not seg.words:
                continue
            start = seg.start if seg.start is not None else self._last_start
            self._last_start = start
            key = (start, self._seq)
            self._seq += 1
            i = bisect.bisect(self._keys, key)
            self._keys.insert(i, key)
            self._segments.insert(i, seg)

    def finish(self) -> tuple[list[Speaker], list[Turn]]:
        speaker_ids: dict[str, str] = {}
        merged: list[list[Segment]] = []
        for seg in self._segments:
            if seg.speaker_label not in speaker_ids:
                speaker_ids[seg.speaker_label] = f"S{len(speaker_ids) + 1}"
            if merged and merged[-1][0].speaker_label == seg.speaker_label:
                merged[-1].append(seg)
            else:
                merged.append([seg])

        turns = [self._turn(i, group, speaker_ids) for i, group in enumerate(merged, start=1)]
        speakers = [Speaker(id=sid, source_label=label) for label, sid in speaker_ids.items()]
        return speakers, turns

    @staticmethod
    def _turn(n: int, group: list[Segment], speaker_ids: dict[str, str]) -> Turn:
        words = [w for seg in group for w in seg.words]
        starts = [x for seg in group for x in [seg.start, *(w.start for w in seg.words)] if x is not None]
        ends = [x for seg in group for x in [seg.end, *(w.end for w in seg.words)] if x is not None]
        confidences = [w.confidence for w in words if w.confidence is not None]
        return Turn(
            id=f"t{n:04d}",
            speaker=speaker_ids[group[0].speaker_label],
            start=min(starts) if starts else None,
            end=max(ends) if ends else None,
            text=" ".join(t for t in (_segment_text(seg) for seg in group) if t),
            is_final=all(seg.is_final for seg in group),
            words=[w.model_copy(update={"text": unicodedata.normalize("NFC", w.text)}) for w in words],
            min_confidence=min(confidences) if confidences else None,
        )


def build_turns(segments: Iterable[Segment]) -> tuple[list[Speaker], list[Turn]]:
    builder = TurnBuilder()
    builder.append(segments)
    return builder.finish()
