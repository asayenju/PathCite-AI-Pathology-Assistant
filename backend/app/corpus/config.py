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
    near_dup_threshold: float = 0.95
    """Chunk dedupe (PRD step 7): drop a chunk whose 5-word-shingle Jaccard with a more trusted kept chunk is at
    least this. Raised from the PRD's 0.9 after the real-corpus dry run: at 0.9 WikiDoc's "MRI can b helpful in the
    diagnosis of severe ulcerative colitis..." was dropped as a copy of "CT scan can b helpful..." (32 words,
    Jaccard exactly 0.900): a different claim. A swapped word near a chunk's edge touches few shingles, so short
    chunks reach 0.9 easily; 0.95 cost only 4 harmless redundant chunks on the real corpus.
    Negation: inserting one word into an n-word text gives Jaccard (n-8)/(n+1), so on Jaccard alone 0.9 keeps
    "X is recommended" vs "X is not recommended" in texts under ~90 words and 0.95 under ~180; longer chunks rely
    on the polarity guard in `dedupe.py`, which never drops a near-duplicate whose negations, numbers, stage numerals
    or comparison signs differ. A one-word substitution mid-text gives (n-9)/(n+1): caught at 0.95 from ~190 words."""
    shingle_words: int = 5
    """Chunks with fewer words than this are deduped on exact content only."""
    minhash_perm: int = 128
    minhash_seed: int = 1

    def config_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()
