from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import NOTICE

Source = Literal["deepgram", "aws_transcribe", "whisperx", "openai_diarized"]
Role = Literal["doctor", "patient", "other"]


class Word(BaseModel):
    text: str = Field(description="The word as transcribed, with any attached punctuation.")
    start: float | None = Field(description="Seconds from the start of the audio; null if the vendor gave none.")
    end: float | None
    confidence: float | None = Field(description="Speech-to-text confidence 0-1; null if the vendor gave none.")


class Turn(BaseModel):
    id: str = Field(description="Stable turn id, e.g. t0001.")
    speaker: str = Field(description="Speaker id, e.g. S1. Roles are assigned by the user, not here.")
    start: float | None
    end: float | None
    text: str = Field(description="Original wording (fillers kept), Unicode NFC only.")
    is_final: bool = Field(description="Always true for file imports; false for interim streaming results.")
    words: list[Word]
    min_confidence: float | None = Field(description="Lowest word confidence in the turn; null if none given.")


class Speaker(BaseModel):
    id: str
    source_label: str = Field(description="The vendor's label, e.g. spk_0, SPEAKER_00, ch_1.")
    word_count: int = 0
    talk_time_s: float = 0.0
    preview: list[str] = Field(default_factory=list)
    suggested_role: Role | None = None


class TranscriptImport(BaseModel):
    source: Source
    language: str | None = None
    duration_s: float | None = None
    speakers: list[Speaker]
    turns: list[Turn]
    warnings: list[str] = Field(default_factory=list)
    notice: str = NOTICE


@dataclass
class Segment:
    """What every vendor parser emits: one stretch of speech by one speaker."""

    speaker_label: str
    start: float | None
    end: float | None
    text: str
    words: list[Word] = field(default_factory=list)
    is_final: bool = True
