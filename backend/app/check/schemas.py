from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

MAX_QUOTE_CHARS = 2000
MAX_CONTEXT_CHARS = 5000
MAX_CONTEXT_ITEMS = 50  # only the last 2 are used; this just bounds the request

ContextTurn = Annotated[str, StringConstraints(max_length=MAX_CONTEXT_CHARS)]


class CheckRequest(BaseModel):
    turn_id: str = Field(description="The doctor turn the claim came from (echoed by the frontend, not used here).")
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS, description="The claim sentence. Must not be blank.")
    context: list[ContextTurn] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_ITEMS,
        description="Previous doctor turns, oldest first. Only the last 2 are used.",
    )


Stance = Literal["supports", "contradicts", "irrelevant", "unclear"]


class CheckSource(BaseModel):
    chunk_id: int
    title: str | None
    section: str
    url: str
    trust: str = Field(description="high | medium | low")
    year: int | None
    quote: str = Field(description="Luna's word-for-word quote from this passage; empty when it has none grounded.")
    stance: Stance = Field(description="The stance the verdict uses: Luna's, after the groundedness guard.")
    luna_stance: Stance = Field(description="Luna's stance after the groundedness guard (same as stance).")


class CheckConfidence(BaseModel):
    label: Literal["supported", "contradicted", "insufficient"] = Field(description="Jev's top answer.")
    p: float = Field(description="Jev's probability for its top answer.")
    p_verdict: float | None = Field(description="Jev's probability for the class of Luna's verdict; null if disputed.")
    agrees: bool | None = Field(description="Jev's top answer is the class of Luna's verdict; null if disputed.")


class CheckResponse(BaseModel):
    status: Literal["checked"] = "checked"
    verdict: Literal["accurate", "incomplete", "inaccurate", "disputed", "unverified"]
    issue: str | None
    suggestion: str | None
    needs_review: bool
    review_reasons: list[Literal["sources_disagree", "jev_unavailable", "jev_disagrees", "low_confidence"]]
    sources: list[CheckSource] = Field(description="Every merged passage, best first.")
    search_queries: list[str] = Field(description="The searches run: the raw claim first, then Luna's queries.")
    confidence: CheckConfidence | None = Field(description="Jev's independent confidence; null when Jev failed.")
    model: str
    jev_model: str | None
