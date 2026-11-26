from typing import Literal

from pydantic import BaseModel, Field


class TriageWord(BaseModel):
    text: str
    confidence: float | None = None


class TriageTurn(BaseModel):
    id: str
    text: str = Field(description="Doctor turn text, as imported (NFC). Span offsets index into it.")
    words: list[TriageWord] = Field(default_factory=list, description="Word confidences, when the vendor gave them.")


class TriageRequest(BaseModel):
    turns: list[TriageTurn] = Field(description="Doctor turns only, in transcript order. Ids must be unique.")


class TriageSpanRequest(BaseModel):
    text: str = Field(description="A doctor's typed correction of one span.")
    previous_doctor_turns: list[str] = Field(default_factory=list, description="Only the last 2 are used.")


class TriageSpanResponse(BaseModel):
    kind: Literal["skip", "claim"]
    p_claim: float
