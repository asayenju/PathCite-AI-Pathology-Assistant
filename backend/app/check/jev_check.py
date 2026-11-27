"""Jev per-source stance (docs/PRD-rag-checker.md, section 3): Jev's independent second opinion on each passage.

`judge_sources(jev, claim, context, chunks)` sends ONE request through `JevClient.ask` (same transport, retries and
errors as triage) with one Choice question per chunk, `stance_<i>`: supports / contradicts / irrelevant, judged by
the same rules as Luna's instructions in app/check/verify.py. `state` holds the claim, up to 2 previous doctor turns
and the numbered passages. Each answer becomes a `JevStance` with the argmax label and its probability; a missing or
malformed answer is a `JevUpstreamError` (probabilities validated like triage's).

Privacy: claim, context and passages go to TypeSafe only; nothing is logged and errors never contain them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.check.verify import MAX_CONTEXT_TURNS, ChunkLike
from app.triage.jev import JevClient, JevUpstreamError, prob

JevLabel = Literal["supports", "contradicts", "irrelevant"]
LABELS: tuple[JevLabel, ...] = ("supports", "contradicts", "irrelevant")
MAX_CHUNKS = 40  # Jev's question budget per request; the checker sends k=8

STANCE_CRITERIA: dict[str, str] = {
    "supports": (
        "Judging ONLY from what this passage says (not from general medical knowledge), the passage states something "
        "that confirms the doctor's claim."
    ),
    "contradicts": (
        "Judging ONLY from what this passage says, the passage states something incompatible with the claim, for the "
        "same disease and the same kind of patient. A patient-specific detail in the claim (this patient's result, "
        "size or plan) is not something a passage can contradict: judge the general fact the claim relies on."
    ),
    "irrelevant": (
        "The passage does not address what the claim asserts, or it is about a different subtype, a different cancer, "
        "a different stage or a different population (for example anaplastic thyroid cancer when the claim is about "
        "papillary thyroid cancer, or children when the claim is about adults). A passage that only partly addresses "
        "the claim without clearly settling it is also irrelevant."
    ),
}


@dataclass(frozen=True)
class JevStance:
    chunk_id: int
    stance: JevLabel  # argmax of Jev's probabilities
    p: float  # its probability


def _state(claim: str, context: Sequence[str], chunks: Sequence[ChunkLike]) -> dict:
    return {
        "claim": claim,
        "context": list(context)[-MAX_CONTEXT_TURNS:],
        "passages": [
            {"passage": i, "chunk_id": c.chunk_id, "title": c.title, "section": c.section, "text": c.content}
            for i, c in enumerate(chunks, 1)
        ],
    }


def _questions(chunks: Sequence[ChunkLike]) -> dict[str, dict]:
    return {
        f"stance_{i}": {
            "type": "choice",
            "instructions": (
                f"Does Passage {i + 1} (chunk_id {c.chunk_id}) support, contradict or not address the doctor's claim? "
                "Judge this passage on its own. The context turns only explain what the claim refers to."
            ),
            "criteria": STANCE_CRITERIA,
        }
        for i, c in enumerate(chunks)
    }


def _parse(answer: object, chunk_id: int) -> JevStance:
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JevUpstreamError("Jev returned a malformed answer")
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or any(label not in probs for label in LABELS):
        raise JevUpstreamError("Jev returned a malformed answer")
    values = {label: prob(probs[label]) for label in LABELS}
    best = max(LABELS, key=lambda label: values[label])  # ties go to the first label in LABELS order
    return JevStance(chunk_id, best, values[best])


async def judge_sources(
    jev: JevClient, claim: str, context: Sequence[str], chunks: Sequence[ChunkLike]
) -> list[JevStance]:
    """One JevStance per chunk, in chunk order. Raises a JevError subclass on any failure."""
    if not chunks:
        return []
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f"at most {MAX_CHUNKS} chunks per Jev request")
    answers = await jev.ask(_state(claim, context, chunks), _questions(chunks))
    return [_parse(answers.get(f"stance_{i}"), c.chunk_id) for i, c in enumerate(chunks)]
