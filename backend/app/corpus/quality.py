"""Quality filter (issue #46): drop low-value chunks after chunking and dedupe, before embedding.

It runs on the loaded (or freshly built) chunk plan, so changing it never re-chunks: its settings live in
`QualityFilterConfig`, whose `filter_hash()` is separate from the chunker config hash and is recorded in
`corpus_meta` as `quality_filter_hash`. It only ever removes whole chunks (never edits text), so kept chunks keep
their `ord` and `content_hash`; a document that loses chunks has `ord` gaps, and resume re-ingests it because its
stored hash list no longer matches the plan.

Rules, checked in this order (a chunk is counted under the first that matches):

| Rule | Drops |
|---|---|
| `sandbox` | every chunk of a WikiDoc document whose title starts with "Sandbox" |
| `nice_economic` | NICE chunks whose own headings (the section path after the document title) are about economic modelling, utility values, comparators, the decision problem, extrapolation, end of life, managed access, the severity modifier (QALYs, ICERs), resource use, innovation or equality, or whose own (last) heading is about the Cancer Drugs Fund (a "Cancer Drugs Fund reconsideration" container keeps its clinical subsections). A section with any heading about clinical effectiveness, clinical evidence, efficacy, an indirect comparison or a network meta-analysis is kept |
| `research` | chunks under a "research recommendation(s)", "recommendations for (further) research" or "research question(s)" heading, and short chunks (< 40 tokens) whose sentences are all questions |
| `cross_reference` | chunks that are only a cross-reference: "Recommendations 1.2.1 to 1.2.8", "Recommendation 1.3.4", "See 1.2" |
| `wiki_navigation` | WikiDoc chunks with fewer than 10 tokens left once "click here" sentences are removed |
| `garbled_table` | chunks over 50 characters where letters are under 50% of the characters and most of the text is in short lines (a table that lost its labels). Long lines are prose: a readable but numeric-heavy results sentence is kept |
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace

from app.corpus.chunking import count_tokens
from app.corpus.plan import ChunkRecord, Plan, fill_chunk_stats
from app.corpus.sections import PATH_SEP
from app.corpus.sentences import split_sentences

_H = r"[\s\-‐‑–]"
"""A space or any hyphen: NICE writes "End‑of‑life" with non-breaking hyphens."""

NICE_ECONOMIC_HEADINGS: tuple[str, ...] = (
    r"\beconomic model",
    r"\b(?:company|manufacturer|erg|evidence review group|assessment group)['’]?s?['’]?\s+(?:[\w\-‑]+\s+){0,3}"
    r"model",
    r"\b(?:the|survival|economic|base" + _H + r"case)\s+model(?:ling|s)?\b",
    r"\bmodel(?:ling|led)?\s+(?:details|structure|(?:the\s+)?(?:overall|progression|survival|time|disease|invasive))",
    r"\bto model\b",
    r"(?<!clinical\s)\b(?:dis)?utilit(?:y|ies)\b",
    r"\bcomparators?\b",
    r"\bdecision problem",
    r"\bextrapolat",
    r"\bend" + _H + r"+of" + _H + r"+life\b",
    r"\bmanaged access\b",
    r"^severity$",
    r"\bseverity\s+(?:modifier|weighting)",
    r"\bqalys?\b",
    r"\bicers?\b",
    r"\bresource use\b",
    r"\binnovation\b",
    r"\bequalit(?:y|ies)\b",
)  # fmt: skip
"""Matched case-insensitively against each NICE heading under the document title (never against the content or the
title: a guideline titled "... end of life care" is not economics), so a matching heading takes its subsections."""

NICE_ECONOMIC_LEAF_HEADINGS: tuple[str, ...] = (r"\bcancer drugs fund\b",)
"""Matched against a section's own (last) heading only. A "Cancer Drugs Fund reconsideration" is a whole re-appraisal
whose subsections include clinical management, patient experience and updated survival data; only the parts that
are about the fund itself go ("X meets the criteria for inclusion in the Cancer Drugs Fund")."""

NICE_CLINICAL_HEADINGS: tuple[str, ...] = (
    r"\bclinical(?:ly)?" + _H + r"+(?:effective(?:ness)?|efficacy|evidence|utility)",
    r"\befficacy\b",
    r"\bindirect(?:\s+treatment)?\s+comparisons?\b",
    r"\bnetwork\s+meta" + _H + r"analys[ei]s\b",
)
"""If any heading in a NICE section's path matches one of these, the economic rule leaves the section alone: it is
clinical evidence even when it discusses a comparator or the model ("Clinical effectiveness › Comparators",
"Appropriate comparators › Nab-paclitaxel has similar efficacy to weekly paclitaxel"). "Trial" and "arm" are not on
the list: economic headings use them too ("It is appropriate to model the comparator arm using ...")."""

RESEARCH_HEADINGS: tuple[str, ...] = (
    r"\bresearch\s+recommendations?\b",
    r"\brecommendations?\s+for\s+(?:further\s+|future\s+)?research\b",
    r"\bresearch\s+questions?\b",
)

_NUM = r"\d+(?:\.\d+)*[a-z]?"
CROSS_REFERENCE = (
    r"(?:see\s+)?(?:(?:the\s+)?(?:recommendations?|sections?)\s+)?" + _NUM
    + r"(?:\s*(?:,|;|and|to|or|–|-)\s*(?:(?:recommendations?|sections?)\s+)?" + _NUM + r")*"
)  # fmt: skip
"""The whole chunk (trimmed, an optional final full stop removed) must match, and it must say "see",
"recommendation" or "section": a bare number is left to the other rules."""

WIKI_NAVIGATION = r"\bclick here\b"
"""A sentence containing this is navigation ("To view the overview of FAP, click here")."""

SANDBOX_TITLE = r"^sandbox\b"


@dataclass(frozen=True)
class QualityFilterConfig:
    version: int = 1
    """Bump when a rule changes in a way the other fields don't capture."""
    encoding: str = "cl100k_base"
    nice_economic_headings: tuple[str, ...] = NICE_ECONOMIC_HEADINGS
    nice_economic_leaf_headings: tuple[str, ...] = NICE_ECONOMIC_LEAF_HEADINGS
    nice_clinical_headings: tuple[str, ...] = NICE_CLINICAL_HEADINGS
    research_headings: tuple[str, ...] = RESEARCH_HEADINGS
    question_max_tokens: int = 40
    """Chunks under this many tokens whose sentences are all questions are dropped."""
    cross_reference: str = CROSS_REFERENCE
    wiki_navigation: str = WIKI_NAVIGATION
    wiki_navigation_min_tokens: int = 10
    sandbox_title: str = SANDBOX_TITLE
    garbled_min_chars: int = 50
    garbled_max_letter_ratio: float = 0.5
    garbled_prose_line_chars: int = 60
    """Lines at least this long are prose; a chunk with at least half its characters in such lines is never garbled."""

    def filter_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()


RULES: tuple[str, ...] = ("sandbox", "nice_economic", "research", "cross_reference", "wiki_navigation", "garbled_table")


@dataclass
class QualityResult:
    filter_hash: str
    removed: list[tuple[ChunkRecord, str]] = field(default_factory=list)
    """(chunk, rule) for every removed chunk, in plan order."""
    by_rule: dict[str, int] = field(default_factory=dict)
    by_rule_source: dict[str, dict[str, int]] = field(default_factory=dict)
    """rule -> source -> chunks removed."""
    documents_emptied: list[str] = field(default_factory=list)
    """Documents that lost every chunk: they are no longer planned (and `--prune` deletes them)."""
    chunks_before: int = 0
    chunks_after: int = 0


class _Compiled:
    def __init__(self, cfg: QualityFilterConfig) -> None:
        self.cfg = cfg
        self.economic = [re.compile(p, re.IGNORECASE) for p in cfg.nice_economic_headings]
        self.economic_leaf = [re.compile(p, re.IGNORECASE) for p in cfg.nice_economic_leaf_headings]
        self.clinical = [re.compile(p, re.IGNORECASE) for p in cfg.nice_clinical_headings]
        self.research = [re.compile(p, re.IGNORECASE) for p in cfg.research_headings]
        self.cross_reference = re.compile(cfg.cross_reference, re.IGNORECASE)
        self.cross_word = re.compile(r"\b(?:see|recommendations?|sections?)\b", re.IGNORECASE)
        self.wiki = re.compile(cfg.wiki_navigation, re.IGNORECASE)
        self.sandbox = re.compile(cfg.sandbox_title, re.IGNORECASE)


def headings_of(chunk: ChunkRecord) -> list[str]:
    """The section's own headings: its path after the document title."""
    prefix = chunk.title + PATH_SEP
    if not chunk.section.startswith(prefix):
        return []
    return [h.strip() for h in chunk.section[len(prefix) :].split(PATH_SEP) if h.strip()]


def _sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]


def _is_economic_section(headings: list[str], c: _Compiled) -> bool:
    if not headings or any(p.search(h) for p in c.clinical for h in headings):
        return False
    return any(p.search(h) for p in c.economic for h in headings) or any(
        p.search(headings[-1]) for p in c.economic_leaf
    )


def _only_questions(chunk: ChunkRecord, c: _Compiled) -> bool:
    if chunk.token_count >= c.cfg.question_max_tokens:
        return False
    sentences = [s.lstrip("-•*▸► ").strip() for s in _sentences(chunk.content)]
    sentences = [s for s in sentences if s]
    return bool(sentences) and all(s.rstrip("\"'”’)]").endswith("?") for s in sentences)


def _is_cross_reference(text: str, c: _Compiled) -> bool:
    t = text.strip().rstrip(".").strip()
    return bool(c.cross_reference.fullmatch(t)) and bool(c.cross_word.search(t))


def _is_wiki_navigation(chunk: ChunkRecord, c: _Compiled) -> bool:
    if not c.wiki.search(chunk.content):
        return False
    rest = " ".join(s for s in _sentences(chunk.content) if not c.wiki.search(s))
    return count_tokens(rest, c.cfg.encoding) < c.cfg.wiki_navigation_min_tokens


def _is_garbled(text: str, cfg: QualityFilterConfig) -> bool:
    if len(text) <= cfg.garbled_min_chars:
        return False
    if sum(ch.isalpha() for ch in text) >= cfg.garbled_max_letter_ratio * len(text):
        return False
    prose = sum(len(line) for line in text.splitlines() if len(line) >= cfg.garbled_prose_line_chars)
    return prose * 2 < len(text)


def removal_rule(chunk: ChunkRecord, config: QualityFilterConfig | None = None, *, _c: _Compiled | None = None):
    """The rule that removes this chunk, or None if it is kept."""
    c = _c or _Compiled(config or QualityFilterConfig())
    if chunk.source == "wikidoc" and c.sandbox.search(chunk.title.strip()):
        return "sandbox"
    headings = headings_of(chunk)
    if chunk.source == "nice" and _is_economic_section(headings, c):
        return "nice_economic"
    if any(p.search(h) for p in c.research for h in headings) or _only_questions(chunk, c):
        return "research"
    if _is_cross_reference(chunk.content, c):
        return "cross_reference"
    if chunk.source == "wikidoc" and _is_wiki_navigation(chunk, c):
        return "wiki_navigation"
    if _is_garbled(chunk.content, c.cfg):
        return "garbled_table"
    return None


def apply_quality_filter(plan: Plan, config: QualityFilterConfig | None = None) -> tuple[Plan, QualityResult]:
    """Returns the filtered plan (same meta plus `quality_filter_hash`, stats recomputed) and what was removed."""
    config = config or QualityFilterConfig()
    compiled = _Compiled(config)
    result = QualityResult(filter_hash=config.filter_hash(), chunks_before=len(plan.chunks))
    kept: list[ChunkRecord] = []
    for chunk in plan.chunks:
        rule = removal_rule(chunk, _c=compiled)
        if rule is None:
            kept.append(chunk)
            continue
        result.removed.append((chunk, rule))
        result.by_rule[rule] = result.by_rule.get(rule, 0) + 1
        per_source = result.by_rule_source.setdefault(rule, {})
        per_source[chunk.source] = per_source.get(chunk.source, 0) + 1
    result.chunks_after = len(kept)
    before_ids = {c.doc_id for c in plan.chunks}
    after_ids = {c.doc_id for c in kept}
    result.documents_emptied = sorted(before_ids - after_ids)

    stats = replace(plan.stats, documents_without_chunks=plan.stats.documents_without_chunks
                    + len(result.documents_emptied))  # fmt: skip
    fill_chunk_stats(stats, kept, plan.meta.get("config", {}).get("encoding", config.encoding))
    meta = {**plan.meta, "quality_filter_hash": result.filter_hash}
    return Plan(meta, kept, stats, plan.dedupe), result


def format_quality_report(result: QualityResult, config: QualityFilterConfig | None = None, *,
                          show_patterns: bool = False) -> list[str]:  # fmt: skip
    """Report lines: removed per rule and per source, emptied documents, and (dry run) the rule patterns."""
    config = config or QualityFilterConfig()
    removed = result.chunks_before - result.chunks_after
    lines = [f"Quality filter     {removed:,} chunks removed ({result.chunks_before:,} -> {result.chunks_after:,}); "
             f"hash {result.filter_hash[:12]}"]  # fmt: skip
    for rule in RULES:
        n = result.by_rule.get(rule, 0)
        by = ", ".join(f"{s} {k:,}" for s, k in sorted(result.by_rule_source.get(rule, {}).items())) or "-"
        lines.append(f"  {rule:<17} {n:>5,}  ({by})")
    sources: dict[str, int] = {}
    for per in result.by_rule_source.values():
        for s, k in per.items():
            sources[s] = sources.get(s, 0) + k
    lines.append("  by source         " + (", ".join(f"{s} {k:,}" for s, k in sorted(sources.items())) or "none"))
    if result.documents_emptied:
        more = " ..." if len(result.documents_emptied) > 10 else ""
        lines.append(f"  documents emptied {len(result.documents_emptied):,} (no longer planned): "
                     f"{', '.join(result.documents_emptied[:10])}{more}")  # fmt: skip
    if show_patterns:
        lines += [
            "  patterns (case-insensitive):",
            f"    sandbox title          {config.sandbox_title}",
            *[f"    nice economic heading  {p}" for p in config.nice_economic_headings],
            *[f"    nice economic (own)    {p}" for p in config.nice_economic_leaf_headings],
            *[f"    nice keep (clinical)   {p}" for p in config.nice_clinical_headings],
            *[f"    research heading       {p}" for p in config.research_headings],
            f"    research question      every sentence ends in '?' and < {config.question_max_tokens} tokens",
            f"    cross-reference        fullmatch {config.cross_reference}",
            f"    wiki navigation        {config.wiki_navigation}; < {config.wiki_navigation_min_tokens} tokens left",
            f"    garbled table          > {config.garbled_min_chars} chars, letters < "
            f"{config.garbled_max_letter_ratio:.0%}, under half in lines >= {config.garbled_prose_line_chars} chars",
        ]
    return lines
