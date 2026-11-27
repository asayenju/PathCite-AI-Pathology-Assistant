"""Steps 1-7 of docs/PRD-corpus-ingest.md: corpus in, chunk plan (`chunks.jsonl`) out.

The plan is cached because OpenAI embeddings aren't bit-for-bit deterministic: re-chunking could move semantic
boundaries and change every content hash, so ingest re-uses the plan unless told `--rechunk`. Its first line is a
meta record with the chunker config hash and the embedder that found the breakpoints; every other line is a chunk.
"""

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.corpus.chunking import chunk_section, count_tokens
from app.corpus.config import ChunkerConfig
from app.corpus.dedupe import DedupeResult, dedupe_chunks
from app.corpus.documents import Document, dedupe_documents, load_corpus, normalise_for_hash
from app.corpus.embedder import Embedder
from app.corpus.sections import Section, filter_sections, split_sections

PLAN_FILE = "chunks.jsonl"


class PlanMismatch(Exception):
    """The cached plan was built with another chunker config or breakpoint embedder."""


@dataclass(frozen=True)
class ChunkRecord:
    doc_id: str
    ord: int
    section: str
    content: str
    embed_text: str
    """What gets embedded: the section path gives the chunk its context. URLs and ids are never embedded."""
    content_hash: str
    token_count: int
    cancers: list[str]
    source: str
    trust: str
    url: str
    title: str
    pmid: str | None
    journal: str | None
    year: int | None
    pub_types: list[str]


@dataclass
class PlanStats:
    documents_loaded: int = 0
    documents_by_source: dict[str, int] = field(default_factory=dict)
    pmid_dropped: int = 0
    duplicate_dropped: int = 0
    documents: int = 0
    documents_without_chunks: int = 0
    sections: int = 0
    sections_dropped: int = 0
    sections_dropped_chars: int = 0
    chunks: int = 0
    content_tokens: int = 0
    embed_tokens: int = 0
    """Tokens of `embed_text` over all chunks: what the ingest will embed."""
    breakpoint_tokens: int = 0
    """Tokens sent to the embedder to find semantic breakpoints in long sections."""
    min_chunk_tokens: int = 0
    max_chunk_tokens: int = 0
    max_embed_tokens: int = 0
    text_bytes: int = 0
    """UTF-8 bytes of content + section over all chunks, for the size estimate."""
    chunks_missing_url: int = 0
    chunks_missing_section: int = 0
    chunk_dedupe_skipped: int | None = None
    """Chunks dropped as exact or near-duplicates of a more trusted chunk (None in plans from before issue #44)."""
    chunk_dedupe_exact: int = 0
    chunk_dedupe_near: int = 0
    chunk_dedupe_guarded: int = 0
    """Near-duplicates kept because their negations, numbers or comparison signs differ."""
    chunk_dedupe_by_source: dict[str, int] = field(default_factory=dict)


@dataclass
class Plan:
    meta: dict
    chunks: list[ChunkRecord]
    stats: PlanStats
    dedupe: DedupeResult | None = None
    """What chunk dedupe dropped and why; only on a freshly built plan, not written to the plan file."""


class _CountingEmbedder:
    """Counts the tokens the breakpoint search sends, for the cost estimate."""

    def __init__(self, inner: Embedder, encoding: str) -> None:
        self.inner, self.encoding, self.tokens = inner, encoding, 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.tokens += sum(count_tokens(t, self.encoding) for t in texts)
        return self.inner.embed(texts)


def build_plan(docs: list[Document], embedder: Embedder, config: ChunkerConfig, *, embedder_id: str) -> Plan:
    stats = PlanStats(documents_loaded=len(docs))
    for d in docs:
        key = f"{d.kind}:{d.source}"
        stats.documents_by_source[key] = stats.documents_by_source.get(key, 0) + 1
    kept, dedupe = dedupe_documents(docs)
    stats.pmid_dropped, stats.duplicate_dropped = len(dedupe.pmid_dropped), len(dedupe.duplicate_dropped)
    stats.documents = len(kept)

    counting = _CountingEmbedder(embedder, config.encoding)
    chunks: list[ChunkRecord] = []
    for doc in kept:
        sections, dropped = filter_sections(split_sections(doc), config)
        stats.sections += len(sections)
        stats.sections_dropped += len(dropped)
        stats.sections_dropped_chars += sum(len(s.text) for s in dropped)
        doc_chunks = [
            _record(doc, section, content, config)
            for section in sections
            for content in chunk_section(section.text, counting, config)
        ]
        chunks.extend(ChunkRecord(**{**asdict(c), "ord": i}) for i, c in enumerate(doc_chunks))

    chunks, chunk_dedupe = dedupe_chunks(chunks, config)
    stats.chunk_dedupe_skipped = chunk_dedupe.skipped
    stats.chunk_dedupe_exact, stats.chunk_dedupe_near = chunk_dedupe.exact, chunk_dedupe.near
    stats.chunk_dedupe_guarded, stats.chunk_dedupe_by_source = chunk_dedupe.guarded, chunk_dedupe.by_source
    # Counted after dedupe: a document that lost every chunk to a more trusted copy is never ingested.
    stats.documents_without_chunks = len({d.id for d in kept} - {c.doc_id for c in chunks})

    _fill_chunk_stats(stats, chunks, config)
    stats.breakpoint_tokens = counting.tokens
    meta = {
        "kind": "chunk_plan",
        "chunker_config_hash": config.config_hash(),
        "config": asdict(config),
        "breakpoint_embedder": embedder_id,
        "stats": asdict(stats),
    }
    return Plan(meta, chunks, stats, chunk_dedupe)


def _record(doc: Document, section: Section, content: str, config: ChunkerConfig) -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc.id,
        ord=0,
        section=section.path,
        content=content,
        embed_text=f"{section.path}\n\n{content}",
        content_hash=hashlib.sha256(normalise_for_hash(content).encode()).hexdigest(),
        token_count=count_tokens(content, config.encoding),
        cancers=list(doc.cancers),
        source=doc.source,
        trust=doc.trust,
        url=doc.url,
        title=doc.title,
        pmid=doc.pmid,
        journal=doc.journal,
        year=doc.year,
        pub_types=list(doc.pub_types),
    )


def _fill_chunk_stats(stats: PlanStats, chunks: list[ChunkRecord], config: ChunkerConfig) -> None:
    stats.chunks = len(chunks)
    embed_tokens = [count_tokens(c.embed_text, config.encoding) for c in chunks]
    stats.content_tokens = sum(c.token_count for c in chunks)
    stats.embed_tokens = sum(embed_tokens)
    stats.min_chunk_tokens = min((c.token_count for c in chunks), default=0)
    stats.max_chunk_tokens = max((c.token_count for c in chunks), default=0)
    stats.max_embed_tokens = max(embed_tokens, default=0)
    stats.text_bytes = sum(len(c.content.encode()) + len(c.section.encode()) for c in chunks)
    stats.chunks_missing_url = sum(not c.url for c in chunks)
    stats.chunks_missing_section = sum(not c.section for c in chunks)


def write_plan(path: Path, plan: Plan) -> None:
    """Written to a temp file and renamed, so an interrupted run never leaves a truncated plan."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": plan.meta}, ensure_ascii=False) + "\n")
        for c in plan.chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_plan(path: Path) -> Plan:
    with Path(path).open(encoding="utf-8") as f:
        meta = json.loads(f.readline())["meta"]
        chunks = [ChunkRecord(**json.loads(line)) for line in f if line.strip()]
    return Plan(meta, chunks, PlanStats(**meta["stats"]))


def load_or_build_plan(
    corpus_dir: Path,
    embedder: Embedder,
    *,
    embedder_id: str,
    config: ChunkerConfig,
    rechunk: bool,
    plan_path: Path | None = None,
) -> tuple[Plan, bool]:
    """Returns (plan, reused). A cached plan is reused only if it was built with the same config and embedder;
    a different one is refused rather than silently mixed, and `rechunk` rebuilds it."""
    corpus_dir = Path(corpus_dir)
    path = Path(plan_path) if plan_path else corpus_dir / PLAN_FILE
    if path.exists() and not rechunk:
        plan = read_plan(path)
        same_config = plan.meta.get("chunker_config_hash") == config.config_hash()
        if same_config and plan.meta.get("breakpoint_embedder") == embedder_id:
            return plan, True
        raise PlanMismatch(
            f"{path} was built with chunker config {plan.meta.get('chunker_config_hash', '?')[:12]} and embedder "
            f"{plan.meta.get('breakpoint_embedder')!r}, not {config.config_hash()[:12]} and {embedder_id!r}; "
            "rerun with --rechunk to rebuild it"
        )
    plan = build_plan(load_corpus(corpus_dir), embedder, config, embedder_id=embedder_id)
    write_plan(path, plan)
    return plan, False
