"""Jev's confidence in the claim (docs/PRD-rag-checker.md, "v2 (issue #51)"): one independent second opinion.

`jev_confidence(jev, claim, context, chunks)` sends ONE request through `JevClient.ask` (same transport, retries and
errors as triage) with one Choice question, `verdict`: do these passages show the general medical claim is
supported / contradicted / insufficient (not settled)? `state` holds the claim, up to 2 previous doctor turns and
every passage, numbered, with title, section, trust and year. Jev is NOT shown Luna's stances or verdict, so its
answer is an independent check; aggregate.py compares the two.

v1 asked one Choice per passage; Jev's per-passage probabilities were often 0.5-0.65 and it split with Luna on minor
passages, so nearly every card went to review. One question over all passages is what Jev answers well.

Returns `JevVerdict(probs, label, p)`: all three probabilities (validated like triage's), the argmax label (ties go
to the first label in LABELS order) and its probability. A missing or malformed answer is a `JevUpstreamError`.

Privacy: claim, context and passages go to TypeSafe only; nothing is logged and errors never contain them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.check.verify import MAX_CONTEXT_TURNS, ChunkLike
from app.triage.jev import JevClient, JevUpstreamError, prob

JevLabel = Literal["supported", "contradicted", "insufficient"]
LABELS: tuple[JevLabel, ...] = ("supported", "contradicted", "insufficient")
QUESTION = "verdict"

VERDICT_CRITERIA: dict[str, str] = {
    "supported": (
        "Judging ONLY from the passages (not from general medical knowledge), the passages show that the general "
        "medical fact the claim relies on is correct."
    ),
    "contradicted": (
        "Judging ONLY from the passages, the passages show that the general medical fact the claim relies on is "
        "incorrect for the same disease and the same kind of patient. A patient-specific detail (this patient's "
        "result, size or plan) is not something a passage can contradict."
    ),
    "insufficient": (
        "The passages do not settle whether the general medical claim is correct: they are off-topic, about a "
        "different subtype, cancer, stage or population, only partly address it, or point both ways."
    ),
}

INSTRUCTIONS = (
    "Do these passages show that the doctor's general medical claim is correct, incorrect, or not settled? The "
    "context turns only explain what the claim refers to; do not judge them."
)


@dataclass(frozen=True)
class JevVerdict:
    probs: dict[str, float]  # every label in LABELS
    label: JevLabel  # argmax
    p: float  # its probability


def _state(claim: str, context: Sequence[str], chunks: Sequence[ChunkLike]) -> dict:
    return {
        "claim": claim,
        "context": list(context)[-MAX_CONTEXT_TURNS:],
        "passages": [
            {
                "passage": i,
                "title": c.title,
                "section": c.section,
                "trust": getattr(c, "trust", None),
                "year": getattr(c, "year", None),
                "text": c.content,
            }
            for i, c in enumerate(chunks, 1)
        ],
    }


def _parse(answer: object) -> JevVerdict:
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JevUpstreamError("Jev returned a malformed answer")
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or any(label not in probs for label in LABELS):
        raise JevUpstreamError("Jev returned a malformed answer")
    values = {label: prob(probs[label]) for label in LABELS}
    best = max(LABELS, key=lambda label: values[label])
    return JevVerdict(values, best, values[best])


async def jev_confidence(
    jev: JevClient, claim: str, context: Sequence[str], chunks: Sequence[ChunkLike]
) -> JevVerdict:
    """Jev's answer over all passages. ValueError (no request) for no chunks; a JevError subclass on any failure."""
    if not chunks:
        raise ValueError("no passages to judge")
    question = {"type": "choice", "instructions": INSTRUCTIONS, "criteria": VERDICT_CRITERIA}
    answers = await jev.ask(_state(claim, context, chunks), {QUESTION: question})
    return _parse(answers.get(QUESTION))
