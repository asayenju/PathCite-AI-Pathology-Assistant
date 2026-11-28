"""Split a doctor turn into sentence spans for triage.

Jev only labels the input it's given; it can't return spans. So spans are cut here, deterministically, as character
offsets into the turn text (which the importer already NFC-normalised). Offsets, not quotes, are what the UI anchors
on, so a sentence repeated within a turn is still placed correctly.

Two kinds of span are skipped locally, never sent to Jev (`Span.local_skip`):
- filler: no letters, or only filler words ("Um.", "Okay, okay.").
- short_reply: at most 4 words, all from a small allow-list of content-free words, and no digit ("Nobody does.",
  "That's normal.", "Most people do."). Only doctor turns are sent to Jev, so the patient line such a reply answers
  is invisible and Jev can read it as a claim (issue #57). Any other word, such as a disease, drug, organ, verb
  ("spread") or acronym ("HER2"), or any number, keeps the span: "Melanoma can spread." still goes to Jev.
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

SHORT_REPLY_MAX_WORDS = 4
"""A span of at most this many words made only of REPLY_WORDS is a context-dependent reply (local skip)."""
REPLY_WORDS = frozenset(
    {
        # pronouns, determiners, quantifiers over people
        "i", "you", "we", "they", "he", "she", "it", "me", "us", "them", "my", "your", "our", "their", "its",
        "this", "that", "these", "those", "there", "here", "a", "an", "the", "some", "most", "many", "all", "any",
        "nobody", "everybody", "everyone", "someone", "somebody", "anyone", "anybody", "people", "folks", "lots",
        "plenty", "of", "at", "for", "to", "with", "in", "on", "about", "what", "how", "why",
        # auxiliaries and their contractions
        "do", "does", "did", "don't", "doesn't", "didn't", "is", "are", "was", "were", "be", "been", "isn't",
        "aren't", "wasn't", "weren't", "can", "can't", "cannot", "could", "couldn't", "will", "won't", "would",
        "wouldn't", "should", "shouldn't", "may", "might", "must", "have", "has", "had", "haven't", "hasn't",
        "it's", "that's", "there's", "what's", "i'm", "you're", "we're", "they're", "it'll", "that'll", "i'd",
        "you'd", "we'd", "i've", "you've", "we've", "they've", "let's",
        # agreement, reassurance and other content-free words
        "exactly", "absolutely", "definitely", "certainly", "totally", "completely", "entirely", "quite", "very",
        "really", "so", "too", "also", "just", "actually", "indeed", "course", "same", "normal", "fine", "good",
        "great", "true", "right", "correct", "fair", "common", "usual", "typical", "rare", "possible", "likely",
        "probably", "maybe", "perhaps", "sometimes", "often", "always", "never", "not", "no", "yes", "now", "then",
        "again", "enough", "point", "question", "questions", "worry", "worries", "problem", "sense", "okay", "ok",
        "well", "understandable", "natural", "happens", "happen", "thing", "things", "agree", "know", "see",
        "understand", "think", "mean", "guess", "bet", "hope", "sure", "thanks", "thank", "glad", "sorry", "hear",
        "sounds", "mind", "either", "neither", "both", "nothing", "something", "everything", "much", "more",
        "less", "case", "way", "fault", "deal", "else", "wonderful", "lovely", "perfect",
    }
)  # fmt: skip
NUMBER_WORDS = frozenset(
    {
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
        "million", "half", "third", "thirds", "quarter", "percent", "once", "twice", "double", "triple",
    }
)  # fmt: skip

# Terminal punctuation (or an ellipsis), any closing quotes/brackets, then whitespace or the end of the text.
_BOUNDARY = re.compile(r"(?:\.{2,}|…|[.?!]+)[\"'”’)\]]*(?=\s|$)")
_PRECEDING_TOKEN = re.compile(r"(\S+)$")
_WORD = re.compile(r"\S+")
_LETTER_TOKEN = re.compile(r"[a-z]+(?:[-'’][a-z]+)*")
_EDGE = re.compile(r"^[\W_]+|[\W_]+$")  # punctuation around a word (keeps inner apostrophes and hyphens)


@dataclass(frozen=True)
class Span:
    start: int
    end: int  # exclusive
    filler: bool
    """True when the span has no letters or only filler words: it is skipped without calling Jev."""
    short_reply: bool = False
    """True for a short reply with no content word and no number ("Nobody does.", "That's normal."): skipped
    without calling Jev. Its meaning depends on the patient's line, which is never sent, so it can't be checked."""

    @property
    def local_skip(self) -> bool:
        return self.filler or self.short_reply


def split_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for start, end in _sentences(text):
        for s, e in _cut_long(text, start, end):
            filler = _is_filler(text[s:e])
            spans.append(Span(s, e, filler, not filler and is_short_reply(text[s:e])))
    return spans


def is_short_reply(span_text: str) -> bool:
    """At most SHORT_REPLY_MAX_WORDS words, every one in REPLY_WORDS or FILLERS, and no digit.

    The vocabulary is an allow-list of words that can't carry a medical assertion, so any other word (a disease,
    drug, organ, verb like "spread", an acronym like "HER2") keeps the span for Jev. Number words aren't in it."""
    if any(c.isdigit() for c in span_text):
        return False
    words = [_EDGE.sub("", w) for w in _WORD.findall(span_text.lower().replace("’", "'"))]
    words = [w for w in words if w]
    if not words or len(words) > SHORT_REPLY_MAX_WORDS:
        return False
    return all((w in REPLY_WORDS or w in FILLERS) and w not in NUMBER_WORDS for w in words)


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
