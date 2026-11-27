"""Luna per-source stance: does each retrieved passage support, contradict or not address a claim?

`luna_stances(client, claim, context, chunks)` asks the checker model (gpt-6-luna through
`OpenAIResponsesClient.structured`) for one judgement per passage, validates the answer against
`LUNA_SCHEMA` (strict mode is a server promise; we still check it, and a violation is an
`LLMUpstreamError`), then runs the groundedness guard.

Groundedness guard (`ground`, pure): a verdict must never rest on a made-up quote.
- supports/contradicts whose quote is empty or not found in *its own* chunk's content (after
  `normalise_for_match`: NFC, case folding, curly quotes and dashes to ASCII, whitespace folded)
  becomes `unclear`.
- irrelevant keeps an empty quote (anything the model quoted for it is dropped).
- Judgements for chunk ids we didn't send are dropped; if the model judges a chunk twice, the
  first judgement wins.
- A chunk the model didn't judge becomes `unclear`, not `irrelevant`: a missing judgement is not
  evidence that the passage is off-topic, and `unclear` sends the card to review downstream.
- Output is one `SourceStance` per input chunk, in input order.

Chunks are anything with `chunk_id`, `section`, `content` and `title` attributes (`ChunkLike`), so
the search module's Evidence objects can be passed directly.

Privacy: the claim, context and passages go to OpenAI only; nothing is logged, and exception
messages never contain them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError

from app.check.llm import LLMUpstreamError, OpenAIResponsesClient, Usage

Stance = Literal["supports", "contradicts", "irrelevant", "unclear"]
ModelStance = Literal["supports", "contradicts", "irrelevant"]

MAX_CONTEXT_TURNS = 2
SCHEMA_NAME = "source_stances"


class ChunkLike(Protocol):
    @property
    def chunk_id(self) -> int: ...
    @property
    def section(self) -> str | None: ...
    @property
    def content(self) -> str: ...
    @property
    def title(self) -> str | None: ...


@dataclass(frozen=True)
class ChunkIn:
    """Minimal passage for tests and callers without a search Evidence object."""

    chunk_id: int
    section: str | None
    content: str
    title: str | None = None


@dataclass(frozen=True)
class SourceStance:
    chunk_id: int
    stance: Stance  # after the groundedness guard
    quote: str  # the model's quote, verbatim; "" for irrelevant / missing
    model_stance: ModelStance | None  # what the model said; None if it didn't judge this chunk
    grounded: bool  # False when the guard turned the stance into unclear


@dataclass(frozen=True)
class LunaResult:
    stances: list[SourceStance]
    missing_qualifier: bool = False
    issue: str | None = None
    suggestion: str | None = None
    usage: Usage = field(default_factory=Usage)


LUNA_INSTRUCTIONS = """\
You check a doctor's medical claim against evidence passages.

What you get:
- CLAIM: one sentence a doctor said to a patient. It has been selected because it states a \
general medical fact.
- CONTEXT: up to two of the doctor's previous turns, oldest first. Use them only to understand \
what the claim refers to (for example, which cancer "it" means). Do not judge them.
- PASSAGES: numbered excerpts from guidelines and papers, each labelled with its chunk_id.

Rules:
1. Judge ONLY from the passages. Do not use your own medical knowledge to support or contradict \
the claim, even if you are sure it is right or wrong.
2. Judge every passage on its own, and give exactly one judgement per passage, using its chunk_id:
   - "supports": the passage states something that confirms the claim.
   - "contradicts": the passage states something that is incompatible with the claim, for the \
same disease and the same kind of patient.
   - "irrelevant": the passage does not address what the claim asserts.
3. For "supports" or "contradicts", the quote must be the sentence or sentences from that passage \
that show it, copied word for word: same words, same order, no paraphrase, no ellipsis, nothing \
added. For "irrelevant", the quote is an empty string.
4. A patient-specific detail in the claim (this patient's tumour size, test result or plan) is \
not something a passage can contradict. Judge the general fact the claim relies on.
5. A passage about a different subtype or a different cancer (for example anaplastic thyroid \
cancer when the claim is about papillary thyroid cancer), a different stage, or a different \
population (for example children when the claim is about adults) is "irrelevant", not \
"contradicts".
6. If a passage only partly addresses the claim, choose "supports" or "contradicts" only when the \
quoted text clearly settles the part it addresses; otherwise choose "irrelevant".

Then, for the claim as a whole:
- missing_qualifier: true when the passages support the claim but it leaves out a qualifier that \
changes its meaning (for example "always" where the passages say "usually", or a fact that holds \
only for one stage stated as if it held for all). Otherwise false.
- issue: if the passages contradict the claim or missing_qualifier is true, one short sentence in \
plain language saying what is wrong. Otherwise null.
- suggestion: if issue is not null, one short sentence giving a corrected version of the claim \
that the passages support. Otherwise null.
"""

LUNA_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "stances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "integer"},
                    "stance": {"type": "string", "enum": ["supports", "contradicts", "irrelevant"]},
                    "quote": {"type": "string"},
                },
                "required": ["chunk_id", "stance", "quote"],
                "additionalProperties": False,
            },
        },
        "missing_qualifier": {"type": "boolean"},
        "issue": {"type": ["string", "null"]},
        "suggestion": {"type": ["string", "null"]},
    },
    "required": ["stances", "missing_qualifier", "issue", "suggestion"],
    "additionalProperties": False,
}


# --- answer validation -------------------------------------------------------


class _StanceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: StrictInt
    stance: ModelStance
    quote: StrictStr


class _LunaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stances: list[_StanceOut]
    missing_qualifier: StrictBool
    issue: StrictStr | None
    suggestion: StrictStr | None


def _validate(data: dict) -> _LunaOut:
    try:
        return _LunaOut.model_validate(data)
    except ValidationError:
        # The pydantic error would echo the model's text; keep only the fact it failed.
        raise LLMUpstreamError("OpenAI returned a malformed answer: schema violation") from None


# --- prompt --------------------------------------------------------------------


def build_input(claim: str, context: Sequence[str], chunks: Sequence[ChunkLike]) -> str:
    recent = list(context)[-MAX_CONTEXT_TURNS:] if MAX_CONTEXT_TURNS else []
    parts = [f"CLAIM:\n{claim}", "CONTEXT:"]
    parts += [f"- {turn}" for turn in recent] if recent else ["(none)"]
    parts.append("PASSAGES:")
    for i, c in enumerate(chunks, 1):
        label = " / ".join(x for x in (c.title, c.section) if x)
        head = f"Passage {i} [chunk_id={c.chunk_id}]" + (f" ({label})" if label else "")
        parts.append(f"{head}\n{c.content}")
    return "\n\n".join(parts)


# --- groundedness guard ----------------------------------------------------------

_QUOTES = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "´": "'", "`": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
        "­": None, "​": None, "‌": None, "‍": None, "﻿": None,
    }
)
_WS = re.compile(r"\s+")
_WRAPPING = "\"' \t\n"


def normalise_for_match(text: str) -> str:
    """NFC, curly quotes/dashes to ASCII, invisible characters removed, case and whitespace folded."""
    text = unicodedata.normalize("NFC", text).translate(_QUOTES).casefold()
    return _WS.sub(" ", text).strip()


def _found(quote: str, content: str) -> bool:
    q = normalise_for_match(quote).strip(_WRAPPING)  # a quote wrapped in quotation marks still counts
    return bool(q) and q in normalise_for_match(content)


def ground(stances: Sequence[Mapping], chunks: Sequence[ChunkLike]) -> list[SourceStance]:
    """Apply the groundedness guard; one SourceStance per chunk, in chunk order (see module doc)."""
    by_id: dict[int, Mapping] = {}
    known = {c.chunk_id for c in chunks}
    for s in stances:
        cid = s["chunk_id"]
        if cid in known and cid not in by_id:
            by_id[cid] = s

    out: list[SourceStance] = []
    for c in chunks:
        s = by_id.get(c.chunk_id)
        if s is None:
            out.append(SourceStance(c.chunk_id, "unclear", "", None, False))
        elif s["stance"] == "irrelevant":
            out.append(SourceStance(c.chunk_id, "irrelevant", "", "irrelevant", True))
        elif _found(s["quote"], c.content):
            out.append(SourceStance(c.chunk_id, s["stance"], s["quote"], s["stance"], True))
        else:
            out.append(SourceStance(c.chunk_id, "unclear", s["quote"], s["stance"], False))
    return out


# --- public ----------------------------------------------------------------------


async def luna_stances(
    client: OpenAIResponsesClient,
    claim: str,
    context: Sequence[str],
    chunks: Sequence[ChunkLike],
) -> LunaResult:
    """Judge each chunk against the claim with the checker model; grounded, in chunk order.

    Raises LLMAuthError / LLMRateLimited / LLMUpstreamError (schema violations included).
    """
    if not chunks:
        return LunaResult(stances=[])
    data, usage = await client.structured(
        instructions=LUNA_INSTRUCTIONS,
        input=build_input(claim, context, chunks),
        schema=LUNA_SCHEMA,
        name=SCHEMA_NAME,
    )
    answer = _validate(data)
    stances = ground([s.model_dump() for s in answer.stances], chunks)
    return LunaResult(
        stances=stances,
        missing_qualifier=answer.missing_qualifier,
        issue=answer.issue or None,
        suggestion=answer.suggestion or None,
        usage=usage,
    )
