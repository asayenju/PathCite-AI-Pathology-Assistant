"""Split one section into chunks: semantic cuts for long sections, then the size limits (PRD step 5).

Why our own breakpoint step and not Chonkie's `SemanticChunker` (the PRD's first choice, with this as the fallback):
Chonkie 1.7 splits sentences on plain delimiters (". ", "! ", "? ", newline), so it cuts "Dr. Smith" and "Fig. 3";
the PRD's sentence-boundary tests fail. Fixing that means overriding its private `_split_sentences`, and its cut
search and size enforcement run on its own tokenizer, so we'd be patching internals across upgrades. The step itself
is short, so it lives here with numpy over `sentences.split_sentences`, and `chonkie` is not a dependency.

Algorithm, per section:
1. A section of up to `max_tokens` (tiktoken `cl100k_base`) is one chunk; nothing is embedded.
2. Otherwise it is cut into units: sentences, except that a bullet list (with a lead-in line ending in ":") is one
   unit when it fits, or one unit per item when it doesn't. A sentence over the limit is cut at word boundaries.
3. Every unit is embedded once through the injected `Embedder` (so tests and `--dry-run` never call OpenAI). The
   similarity at each gap is the cosine between the mean of the `similarity_window` units before it and after it.
4. A piece over the limit is cut at its weakest gap (lowest similarity), preferring gaps that leave at least
   `min_merge_tokens` on both sides, until every piece fits. So cuts land where the topic drifts most.
5. A piece under `min_merge_tokens` merges into the neighbour it is most similar to, if the result fits. Merging
   only happens inside a section, so it never crosses a heading.
6. A piece under `min_tokens` is dropped.
Chunks are verbatim slices of the section text.
"""

import re
from collections.abc import Callable
from functools import lru_cache

import numpy as np
import tiktoken

from app.corpus.config import ChunkerConfig
from app.corpus.embedder import Embedder
from app.corpus.sentences import split_sentences

Span = tuple[int, int]

_LINE = re.compile(r"[^\n]+")
_WORD = re.compile(r"\S+")
_LIST_ITEM = re.compile(r"\s*(?:[-*+•▪◦]|\d{1,3}[.)]|\([a-zA-Z0-9]{1,3}\))\s")


@lru_cache(maxsize=4)
def _encoding(name: str) -> tiktoken.Encoding:
    return tiktoken.get_encoding(name)


def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    return len(_encoding(encoding).encode_ordinary(text))


def chunk_section(text: str, embedder: Embedder, config: ChunkerConfig) -> list[str]:
    def count(t: str) -> int:
        return count_tokens(t, config.encoding)

    text = text.strip()
    if not text:
        return []
    if count(text) <= config.max_tokens:
        pieces = [text]
    else:
        units = _units(text, config, count)
        sims = _gap_similarities(text, units, embedder, config.similarity_window)
        ranges = _cut(text, units, sims, config, count)
        ranges = _merge_small(text, units, ranges, sims, config, count)
        pieces = [text[units[a][0] : units[b - 1][1]] for a, b in ranges]
    return [p for p in pieces if count(p) >= config.min_tokens]


def _units(text: str, config: ChunkerConfig, count: Callable[[str], int]) -> list[Span]:
    lines = [t for t in (_trim(text, m.start(), m.end()) for m in _LINE.finditer(text)) if t]
    is_item = [bool(_LIST_ITEM.match(text, s)) for s, _ in lines]
    units: list[Span] = []
    i = 0
    while i < len(lines):
        lead_in = not is_item[i] and text[lines[i][1] - 1] == ":" and i + 1 < len(lines) and is_item[i + 1]
        if is_item[i] or lead_in:
            j = i + 1
            while j < len(lines) and is_item[j]:
                j += 1
            block = (lines[i][0], lines[j - 1][1])
            if count(text[block[0] : block[1]]) <= config.max_tokens:
                units.append(block)
            else:
                for k in range(i, j):
                    units.extend(_line_units(text, lines[k], is_item[k], config, count))
            i = j
        else:
            units.extend(_line_units(text, lines[i], False, config, count))
            i += 1
    return units


def _line_units(text: str, line: Span, whole: bool, config: ChunkerConfig, count: Callable[[str], int]) -> list[Span]:
    """A list item stays whole if it fits; prose is cut into sentences; anything still too long, at words."""
    if whole and count(text[line[0] : line[1]]) <= config.max_tokens:
        return [line]
    out: list[Span] = []
    for s, e in split_sentences(text[line[0] : line[1]]):
        span = (line[0] + s, line[0] + e)
        out.extend([span] if count(text[span[0] : span[1]]) <= config.max_tokens else _hard_cut(text, span, config, count))
    return out


def _hard_cut(text: str, span: Span, config: ChunkerConfig, count: Callable[[str], int]) -> list[Span]:
    """The last resort for a "sentence" over the limit (a table row, an unpunctuated run): cut between words."""
    words = [(span[0] + m.start(), span[0] + m.end()) for m in _WORD.finditer(text[span[0] : span[1]])]
    out: list[Span] = []
    i = 0
    while i < len(words):
        j, used = i, 0
        while j < len(words) and (j == i or used + count(" " + text[words[j][0] : words[j][1]]) <= config.max_tokens):
            used += count(" " + text[words[j][0] : words[j][1]])
            j += 1
        while j - i > 1 and count(text[words[i][0] : words[j - 1][1]]) > config.max_tokens:
            j -= 1
        out.append((words[i][0], words[j - 1][1]))
        i = j
    return out


def _gap_similarities(text: str, units: list[Span], embedder: Embedder, window: int) -> np.ndarray:
    """sims[g - 1] is the similarity across the gap before unit g."""
    texts = [text[s:e] for s, e in units]
    vectors = embedder.embed(texts)
    if len(vectors) != len(texts):
        raise ValueError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
    e = np.asarray(vectors, dtype=np.float64)
    sims = np.ones(max(len(units) - 1, 0))
    for g in range(1, len(units)):
        left = e[max(0, g - window) : g].mean(axis=0)
        right = e[g : g + window].mean(axis=0)
        denom = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denom > 0:
            sims[g - 1] = float(left @ right) / denom
    return sims


def _cut(
    text: str, units: list[Span], sims: np.ndarray, config: ChunkerConfig, count: Callable[[str], int]
) -> list[Span]:
    """Unit ranges [a, b), cut at the weakest gap until each fits."""
    unit_tokens = np.array([count(text[s:e]) for s, e in units])
    prefix = np.concatenate(([0], np.cumsum(unit_tokens)))
    done: list[Span] = []
    stack = [(0, len(units))]
    while stack:
        a, b = stack.pop()
        if b - a == 1 or count(text[units[a][0] : units[b - 1][1]]) <= config.max_tokens:
            done.append((a, b))
            continue
        gaps = range(a + 1, b)
        guarded = [
            g for g in gaps
            if prefix[g] - prefix[a] >= config.min_merge_tokens and prefix[b] - prefix[g] >= config.min_merge_tokens
        ]  # fmt: skip
        g = min(guarded or gaps, key=lambda g: (sims[g - 1], g))
        stack.extend([(g, b), (a, g)])  # a..g is popped first, so `done` stays in order
    return done


def _merge_small(
    text: str,
    units: list[Span],
    ranges: list[Span],
    sims: np.ndarray,
    config: ChunkerConfig,
    count: Callable[[str], int],
) -> list[Span]:
    def size(r: Span) -> int:
        return count(text[units[r[0]][0] : units[r[1] - 1][1]])

    ranges = list(ranges)
    while True:
        sizes = [size(r) for r in ranges]
        for i in sorted((i for i, s in enumerate(sizes) if s < config.min_merge_tokens), key=lambda i: sizes[i]):
            options = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(ranges):
                    lo, hi = min(i, j), max(i, j)
                    merged = (ranges[lo][0], ranges[hi][1])
                    if size(merged) <= config.max_tokens:
                        options.append((sims[ranges[hi][0] - 1], lo, merged))
            if options:
                _, lo, merged = max(options)
                ranges[lo : lo + 2] = [merged]
                break
        else:
            return ranges


def _trim(text: str, start: int, end: int) -> Span | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None
