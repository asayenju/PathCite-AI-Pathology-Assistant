"""One module per speech-to-text vendor. Each turns raw JSON into vendor-neutral Segments."""

from dataclasses import dataclass, field

from app.transcripts.schemas import Segment


@dataclass
class ParseResult:
    segments: list[Segment]
    language: str | None = None
    duration_s: float | None = None
    warnings: list[str] = field(default_factory=list)


def language_warnings(language: str | None) -> list[str]:
    if language and not language.lower().startswith("en"):
        return [f"Detected language is '{language}'. Fact checking is built for English."]
    return []
