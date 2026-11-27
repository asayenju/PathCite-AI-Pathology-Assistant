"""Sentence spans for guideline text: the units the semantic split may cut between.

Same approach as `app/triage/segment.py` (a period ends a sentence only if it isn't an abbreviation, an initial or
followed by lower case), extended for written literature: more abbreviations, dotted acronyms ("U.S."), list
numbering at a line start, and every newline as a boundary (guideline lines are paragraphs or list items, often
without a full stop). Generic delimiter splitters cut at "e.g. " and "Dr. ", which the PRD forbids.
"""

import re

ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "approx", "e.g", "i.e", "etc", "fig", "figs", "cf", "al", "ref",
     "refs", "vol", "eq", "ca", "resp", "incl", "jr", "sr", "dept", "no", "nos", "ed", "eds"}
)  # fmt: skip
_BOUNDARY = re.compile(r"(?:\.{2,}|…|[.?!]+)[\"'”’)\]]*(?=\s|$)")
_PRECEDING_TOKEN = re.compile(r"(\S+)$")
_DOTTED_ACRONYM = re.compile(r"(?:[a-z]\.)+[a-z]")
_LINE = re.compile(r"[^\n]+")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Trimmed (start, end) character spans, in order, never crossing a newline."""
    spans: list[tuple[int, int]] = []
    for line in _LINE.finditer(text):
        start = line.start()
        for m in _BOUNDARY.finditer(text, line.start(), line.end()):
            if _is_boundary(text, m, line.start()):
                spans.append((start, m.end()))
                start = m.end()
        spans.append((start, line.end()))
    return [t for t in (_trim(text, s, e) for s, e in spans) if t]


def _is_boundary(text: str, m: re.Match[str], line_start: int) -> bool:
    punct = m.group().rstrip("\"'”’)]")
    rest = text[m.end() :].lstrip(" \t")
    next_is_lower = bool(rest) and rest[0].islower()
    if punct == "…" or punct.startswith(".."):
        return not next_is_lower
    if punct == ".":
        token = _PRECEDING_TOKEN.search(text[line_start : m.start()])
        if not token:
            return False
        if not text[line_start : token.start(1) + line_start].strip():
            return False  # "1." or "A." numbering at the start of a line
        word = token.group(1).lstrip("\"'“‘([").lower()
        if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()) or _DOTTED_ACRONYM.fullmatch(word):
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
