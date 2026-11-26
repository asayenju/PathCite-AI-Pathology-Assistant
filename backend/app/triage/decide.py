"""Turn Jev's answers for one span into a triage kind.

- p_asr >= asr_flag: "transcription" (the doctor is asked "Is this correct?"); base_kind is what it becomes on "Yes".
- P(small_talk) >= skip_min: "skip".
- Otherwise "claim". Unsure spans fail safe to claim: a missed claim is a falsehood nobody checks, while a needless
  check is cheap. It also bounds prompt injection in transcript text: the worst it can do is change a label.
"""

import math
from dataclasses import dataclass
from typing import Literal

Kind = Literal["skip", "claim", "transcription"]
BaseKind = Literal["skip", "claim"]


@dataclass(frozen=True)
class Decision:
    kind: Kind
    base_kind: BaseKind
    p_claim: float
    p_asr: float | None


def decide(p_small_talk: float, p_asr: float | None, *, asr_flag: float, skip_min: float) -> Decision:
    _check_probability(p_small_talk)
    if p_asr is not None:
        _check_probability(p_asr)
    base_kind: BaseKind = "skip" if p_small_talk >= skip_min else "claim"
    kind: Kind = "transcription" if p_asr is not None and p_asr >= asr_flag else base_kind
    return Decision(kind=kind, base_kind=base_kind, p_claim=1.0 - p_small_talk, p_asr=p_asr)


def _check_probability(p: float) -> None:
    if math.isnan(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p}")
