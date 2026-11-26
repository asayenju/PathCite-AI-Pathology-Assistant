"""Split a doctor turn into sentence spans for triage.

Jev only labels the input it's given; it can't return spans. So spans are cut here, deterministically, as character
offsets into the turn text (which the importer already NFC-normalised). Offsets, not quotes, are what the UI anchors
on, so a sentence repeated within a turn is still placed correctly.
"""

import re
from dataclasses import dataclass

MAX_SPAN_WORDS = 40
"""Unpunctuated runs (common in WhisperX output) longer than this are cut."""
MIN_CUT_WORDS = 5
"""A soft cut (comma or conjunction) never leaves a piece shorter than this."""

ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "approx", "e.g", "i.e", "etc", "fig", "cf", "al"}
)
FILLERS = frozenset(
    {
        "um", "uh", "erm", "er", "hmm", "hm", "mm", "mhm", "mm-hmm", "uh-huh", "okay", "ok", "yeah", "yes", "yep",
        "no", "nope", "right", "alright", "sure", "oh", "ah", "so", "well",
    }
)  # fmt: skip
CONJUNCTIONS = frozenset({"and", "but", "so", "because", "or"})

# Terminal punctuation (or an ellipsis), any closing quotes/brackets, then whitespace or the end of the text.
_BOUNDARY = re.compile(r"(?:\.{2,}|…|[.?!]+)[\"'”’)\]]*(?=\s|$)")
_PRECEDING_TOKEN = re.compile(r"(\S+)$")
_WORD = re.compile(r"\S+")
_LETTER_TOKEN = re.compile(r"[a-z]+(?:[-'’][a-z]+)*")


@dataclass(frozen=True)
class Span:
    start: int
    end: int  # exclusive
    filler: bool
    """True when the span has no letters or only filler words: it is skipped without calling Jev."""


def split_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for start, end in _sentences(text):
        for s, e in _cut_long(text, start, end):
            spans.append(Span(s, e, _is_filler(text[s:e])))
    return spans


def _sentences(text: str) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if _is_boundary(text, m):
            pieces.append((start, m.end()))
            start = m.end()
    pieces.append((start, len(text)))
    return [t for t in (_trim(text, s, e) for s, e in pieces) if t]


def _is_boundary(text: str, m: re.Match[str]) -> bool:
    punct = m.group().rstrip("\"'”’)]")
    rest = text[m.end() :].lstrip()
    next_is_lower = bool(rest) and rest[0].islower()
    if punct == "…" or punct.startswith(".."):
        return not next_is_lower  # "um... it's" continues; "Well… That" ends
    if punct == ".":
        token = _PRECEDING_TOKEN.search(text[: m.start()])
        word = token.group(1).lstrip("\"'“‘(").lower() if token else ""
        if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
            return False
        if next_is_lower:
            return False
    return True


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _cut_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Cut a span of more than MAX_SPAN_WORDS words, preferring a comma or a conjunction as the cut point."""
    words = [(start + m.start(), start + m.end()) for m in _WORD.finditer(text[start:end])]
    out: list[tuple[int, int]] = []
    while len(words) > MAX_SPAN_WORDS:
        cut = MAX_SPAN_WORDS  # number of words in the next piece
        for i in range(MAX_SPAN_WORDS - 1, MIN_CUT_WORDS - 2, -1):
            word = text[words[i][0] : words[i][1]]
            following = text[words[i + 1][0] : words[i + 1][1]].lower()
            if word.endswith((",", ";", ":")) or following in CONJUNCTIONS:
                cut = i + 1
                break
        out.append((words[0][0], words[cut - 1][1]))
        words = words[cut:]
    if words:
        out.append((words[0][0], words[-1][1]))
    return out


def _is_filler(span_text: str) -> bool:
    tokens = _LETTER_TOKEN.findall(span_text.lower())
    return all(t in FILLERS for t in tokens)
