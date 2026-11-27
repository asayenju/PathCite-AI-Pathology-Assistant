"""Verdict rules for the claim checker (docs/PRD-rag-checker.md, section 4). Pure code: no model, no majority vote.

Per chunk (in Luna's order, which is search order):
- Jev unavailable (`jev is None`): Luna's stance after the groundedness guard (unclear stays unclear).
- Luna and Jev both say supports, or both contradicts, and Jev's p >= jev_min: that stance.
- Both say irrelevant: irrelevant (whatever Jev's p).
- Anything else: unclear. That covers the two models disagreeing, Jev's p being too low, Luna's guard having turned a
  made-up quote into unclear, and Jev not answering for the chunk.

Verdict over the chunks that support or contradict: none -> unverified; all support -> accurate (incomplete when
Luna reports missing_qualifier); all contradict -> inaccurate; a mix -> disputed.

needs_review = disputed, or any unclear chunk, or Jev unavailable. review_reasons, in this order and each at most once
(the frontend contract):
- "sources_disagree": the verdict is disputed.
- "models_disagree": with Jev available, any chunk ended up unclear. We don't split out Luna's groundedness guard: a
  quote Luna couldn't ground is Luna failing to back up its stance, which the reviewer handles the same way (look at
  the passage). Both models' stances are in the response for anyone who needs the difference.
- "jev_unavailable": Jev failed or isn't configured, so the verdict rests on Luna alone. With Jev unavailable an
  unclear chunk (Luna's guard) adds no extra reason: needs_review is already set.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.check.jev_check import JevStance
from app.check.verify import SourceStance, Stance

Verdict = Literal["accurate", "incomplete", "inaccurate", "disputed", "unverified"]

SOURCES_DISAGREE = "sources_disagree"
MODELS_DISAGREE = "models_disagree"
JEV_UNAVAILABLE = "jev_unavailable"

_RELEVANT = ("supports", "contradicts")


@dataclass(frozen=True)
class ChunkResult:
    chunk_id: int
    stance: Stance  # the combined stance
    luna_stance: Stance  # Luna's, after the groundedness guard
    jev_stance: str | None  # None when Jev was unavailable or didn't answer for this chunk
    jev_p: float | None


@dataclass(frozen=True)
class Aggregate:
    verdict: Verdict
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)
    chunks: list[ChunkResult] = field(default_factory=list)


def _combine(luna: SourceStance, jev: JevStance | None, jev_min: float) -> Stance:
    if jev is None:
        return "unclear"
    if luna.stance == jev.stance == "irrelevant":
        return "irrelevant"
    if luna.stance == jev.stance and luna.stance in _RELEVANT and jev.p >= jev_min:
        return luna.stance
    return "unclear"


def _verdict(stances: Sequence[str], missing_qualifier: bool) -> Verdict:
    relevant = {s for s in stances if s in _RELEVANT}
    if not relevant:
        return "unverified"
    if relevant == {"supports"}:
        return "incomplete" if missing_qualifier else "accurate"
    if relevant == {"contradicts"}:
        return "inaccurate"
    return "disputed"


def aggregate(
    luna: Sequence[SourceStance],
    jev: Sequence[JevStance] | None,
    missing_qualifier: bool,
    jev_min: float = 0.6,
) -> Aggregate:
    if not 0.0 <= jev_min <= 1.0:
        raise ValueError("jev_min must be between 0 and 1")

    chunks: list[ChunkResult] = []
    if jev is None:
        for s in luna:
            chunks.append(ChunkResult(s.chunk_id, s.stance, s.stance, None, None))
    else:
        by_id: dict[int, JevStance] = {}
        for j in jev:
            by_id.setdefault(j.chunk_id, j)
        for s in luna:
            j = by_id.get(s.chunk_id)
            chunks.append(
                ChunkResult(
                    s.chunk_id,
                    _combine(s, j, jev_min),
                    s.stance,
                    j.stance if j else None,
                    j.p if j else None,
                )
            )

    verdict = _verdict([c.stance for c in chunks], missing_qualifier)
    any_unclear = any(c.stance == "unclear" for c in chunks)

    reasons: list[str] = []
    if verdict == "disputed":
        reasons.append(SOURCES_DISAGREE)
    if jev is not None and any_unclear:
        reasons.append(MODELS_DISAGREE)
    if jev is None:
        reasons.append(JEV_UNAVAILABLE)

    needs_review = verdict == "disputed" or any_unclear or jev is None
    return Aggregate(verdict, needs_review, reasons, chunks)
