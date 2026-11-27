"""Chunker settings. Their hash is stored in the plan (and later in `corpus_meta`) so a changed config is noticed."""

import hashlib
import json
from dataclasses import asdict, dataclass

BOILERPLATE_HEADINGS: tuple[str, ...] = (
    r"cost", r"price", r"pprs", r"economic", r"marketing authori[sz]ation", r"committee", r"references?",
    r"acknowledge?ments?", r"information for the public", r"implementation", r"about this", r"external links?",
    r"see also", r"historical perspective",
)  # fmt: skip
"""Regexes matched case-insensitively at a word start against every heading in a section's path (not the title).
Low-value text for a fact checker: about 19% of the guideline text (docs/PRD-corpus-ingest.md)."""


@dataclass(frozen=True)
class ChunkerConfig:
    version: int = 1
    """Bump when the algorithm changes in a way the other fields don't capture."""
    encoding: str = "cl100k_base"
    """The tokenizer text-embedding-3-* uses."""
    max_tokens: int = 512
    min_merge_tokens: int = 64
    """Pieces under this merge into a neighbour in the same section."""
    min_tokens: int = 10
    """Pieces under this are dropped."""
    similarity_window: int = 3
    """Sentences per side when comparing neighbouring windows."""
    boilerplate_headings: tuple[str, ...] = BOILERPLATE_HEADINGS

    def config_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()
