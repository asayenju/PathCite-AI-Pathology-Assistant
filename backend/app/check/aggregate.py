"""Verdict and review rules for the claim checker (docs/PRD-rag-checker.md, "v2 (issue #51)"). Pure code.

Verdict, from Luna's per-passage stances after the groundedness guard (no model, no majority vote). Only supports
and contradicts count; irrelevant and guard-unclear passages are not relevant:
- none relevant -> unverified; all support -> accurate (incomplete when Luna reports missing_qualifier);
  all contradict -> inaccurate; any mix -> disputed.

Jev's single answer (`JevVerdict`, see jev_check.py) is a confidence check on that verdict, never a vote. Luna's
verdict maps to Jev's classes: accurate/incomplete -> supported, inaccurate -> contradicted, unverified ->
insufficient, disputed -> none.

needs_review and review_reasons (a frontend contract; ordered, each at most once):
- "sources_disagree": the verdict is disputed (always reviewed, Jev or not).
- "jev_unavailable": Jev failed or isn't configured; the verdict rests on Luna alone.
- otherwise, for a non-disputed verdict with Jev available, at most one of:
  - "jev_disagrees": Jev's top label is not Luna's class;
  - "low_confidence": it is, but Jev's probability for that class is below jev_min (0.7).
Confident agreement (top label == Luna's class and p >= jev_min) needs no review.

`Confidence` is what the response shows: Jev's top label and p, p_verdict = Jev's probability for Luna's class
(None for disputed) and agrees (None for disputed). None when Jev is unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.check.jev_check import JevLabel, JevVerdict
from app.check.verify import SourceStance

Verdict = Literal["accurate", "incomplete", "inaccurate", "disputed", "unverified"]

SOURCES_DISAGREE = "sources_disagree"
JEV_UNAVAILABLE = "jev_unavailable"
JEV_DISAGREES = "jev_disagrees"
LOW_CONFIDENCE = "low_confidence"

DEFAULT_JEV_MIN = 0.7

_CLASS: dict[str, JevLabel | None] = {
    "accurate": "supported",
    "incomplete": "supported",
    "inaccurate": "contradicted",
    "unverified": "insufficient",
    "disputed": None,
}


@dataclass(frozen=True)
class Confidence:
    label: JevLabel  # Jev's top answer
    p: float  # its probability
    p_verdict: float | None  # Jev's probability for Luna's class; None when disputed
    agrees: bool | None  # label == Luna's class; None when disputed


@dataclass(frozen=True)
class Aggregate:
    verdict: Verdict
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)
    confidence: Confidence | None = None


def luna_verdict(stances: Sequence[SourceStance], missing_qualifier: bool) -> Verdict:
    relevant = {s.stance for s in stances if s.stance in ("supports", "contradicts")}
    if not relevant:
        return "unverified"
    if relevant == {"supports"}:
        return "incomplete" if missing_qualifier else "accurate"
    if relevant == {"contradicts"}:
        return "inaccurate"
    return "disputed"


def luna_class(verdict: Verdict) -> JevLabel | None:
    return _CLASS[verdict]


def aggregate(
    luna: Sequence[SourceStance],
    missing_qualifier: bool,
    jev: JevVerdict | None,
    jev_min: float = DEFAULT_JEV_MIN,
) -> Aggregate:
    if not 0.0 <= jev_min <= 1.0:
        raise ValueError("jev_min must be between 0 and 1")

    verdict = luna_verdict(luna, missing_qualifier)
    target = luna_class(verdict)

    reasons: list[str] = []
    if verdict == "disputed":
        reasons.append(SOURCES_DISAGREE)

    confidence: Confidence | None = None
    if jev is None:
        reasons.append(JEV_UNAVAILABLE)
    elif target is None:
        confidence = Confidence(jev.label, jev.p, None, None)
    else:
        p_verdict = jev.probs[target]
        agrees = jev.label == target
        confidence = Confidence(jev.label, jev.p, p_verdict, agrees)
        if not agrees:
            reasons.append(JEV_DISAGREES)
        elif p_verdict < jev_min:
            reasons.append(LOW_CONFIDENCE)

    return Aggregate(verdict, bool(reasons), reasons, confidence)
