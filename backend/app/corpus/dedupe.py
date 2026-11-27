"""Chunk-level dedupe (docs/PRD-corpus-ingest.md step 7, issue #44): exact, then MinHash near-duplicates.

Lexical only. Embeddings are never used to drop a chunk: "X is recommended" and "X is not recommended" embed at
about 0.95 similarity. Lexical similarity alone isn't enough either: in a 400-word chunk one inserted "not" still
leaves a 5-shingle Jaccard of ~0.98. So a near-duplicate is only dropped when its polarity tokens (negations,
numbers, stage numerals, comparison signs) match the kept copy's exactly; otherwise both are kept and counted as
`guarded`. Fails safe: a false "different" only costs a redundant chunk, a false "same" could lose a contradiction.

Which copy survives: higher trust (high > medium > low), then the lower doc id, then the lower ord. Chunks are
visited in that order and compared only with chunks already kept, so the result doesn't depend on input order.
Survivors keep their input order and their `ord` (gaps are fine: the schema only needs `unique (doc_id, ord)`).
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from datasketch import MinHash, MinHashLSH

from app.corpus.config import ChunkerConfig
from app.corpus.documents import TRUST_RANK, normalise_for_hash

if TYPE_CHECKING:
    from app.corpus.plan import ChunkRecord

_TOKEN = re.compile(r"\w+(?:['’]\w+)*|[<>≤≥=±]")
_NEGATIONS = frozenset(
    "not no never none nor neither without cannot nothing nobody nowhere unless except non".split()
)
_COMPARISONS = frozenset("<>≤≥=±")
_ROMAN = re.compile(r"(?:i{1,3}|iv|vi{0,3}|ix|x)")
LSH_MARGIN = 0.1
"""LSH finds candidates from a little below the threshold, so MinHash estimation error rarely hides a true
near-duplicate; exact Jaccard on the shingle sets makes the final call."""


@dataclass(frozen=True)
class Skip:
    dropped_doc_id: str
    dropped_ord: int
    kept_doc_id: str
    kept_ord: int
    kind: Literal["exact", "near"]
    jaccard: float


@dataclass
class DedupeResult:
    exact: int = 0
    near: int = 0
    guarded: int = 0
    """Near-duplicates over the threshold kept because their polarity tokens differ."""
    by_source: dict[str, int] = field(default_factory=dict)
    """Skipped chunks by the dropped chunk's source."""
    skips: list[Skip] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return self.exact + self.near


def words(text: str) -> list[str]:
    return _TOKEN.findall(normalise_for_hash(text))


def shingles(tokens: list[str], n: int) -> set[str]:
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def polarity(tokens: list[str]) -> Counter[str]:
    """Tokens whose change can flip or alter a claim; a near-duplicate is only dropped if these match exactly."""
    return Counter(
        t
        for t in tokens
        if t in _NEGATIONS
        or t.endswith(("n't", "n’t"))
        or any(ch.isdigit() for ch in t)
        or _ROMAN.fullmatch(t)
        or t in _COMPARISONS
    )


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


def dedupe_chunks(chunks: list["ChunkRecord"], config: ChunkerConfig) -> tuple[list["ChunkRecord"], DedupeResult]:
    result = DedupeResult()
    order = sorted(range(len(chunks)), key=lambda i: (TRUST_RANK[chunks[i].trust], chunks[i].doc_id, chunks[i].ord, i))
    lsh = MinHashLSH(threshold=max(0.0, config.near_dup_threshold - LSH_MARGIN), num_perm=config.minhash_perm)
    by_hash: dict[str, int] = {}
    kept_info: dict[int, tuple[set[str], Counter[str]]] = {}
    dropped: set[int] = set()

    def skip(i: int, k: int, kind: Literal["exact", "near"], score: float) -> None:
        c, held = chunks[i], chunks[k]
        dropped.add(i)
        setattr(result, kind, getattr(result, kind) + 1)
        result.by_source[c.source] = result.by_source.get(c.source, 0) + 1
        result.skips.append(Skip(c.doc_id, c.ord, held.doc_id, held.ord, kind, score))

    for i in order:
        c = chunks[i]
        if c.content_hash in by_hash:
            skip(i, by_hash[c.content_hash], "exact", 1.0)
            continue
        by_hash[c.content_hash] = i
        tokens = words(c.content)
        if len(tokens) < config.shingle_words:
            continue
        sh = shingles(tokens, config.shingle_words)
        pol = polarity(tokens)
        mh = MinHash(num_perm=config.minhash_perm, seed=config.minhash_seed)
        for s in sorted(sh):
            mh.update(s.encode())
        best: tuple[float, int] | None = None
        guarded = False
        for k in sorted(lsh.query(mh)):
            score = jaccard(sh, kept_info[k][0])
            if score < config.near_dup_threshold:
                continue
            if kept_info[k][1] != pol:
                guarded = True
            elif best is None or score > best[0]:
                best = (score, k)
        if best is not None:
            skip(i, best[1], "near", best[0])
            continue
        result.guarded += guarded
        kept_info[i] = (sh, pol)
        lsh.insert(i, mh)

    result.skips.sort(key=lambda s: (s.dropped_doc_id, s.dropped_ord))
    result.by_source = dict(sorted(result.by_source.items()))
    return [c for i, c in enumerate(chunks) if i not in dropped], result
