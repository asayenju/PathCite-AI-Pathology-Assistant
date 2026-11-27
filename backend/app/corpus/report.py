"""The `--dry-run` report: what an ingest would embed and store, and roughly what it costs. No API calls."""

from app.corpus.plan import PlanStats

PRICE_PER_MILLION_TOKENS = 0.02
"""text-embedding-3-small, USD."""
DIMS = 1536
ROW_OVERHEAD_BYTES = 1024
"""Per chunk: ids, hashes, tags, the fts tsvector and Postgres row overhead."""
INDEX_FACTOR = 2
"""HNSW and GIN indexes roughly double the table."""


def estimate_cost_usd(tokens: int) -> float:
    return tokens * PRICE_PER_MILLION_TOKENS / 1_000_000


def estimate_mb(chunks: int, avg_text_bytes: float) -> float:
    return chunks * (DIMS * 4 + avg_text_bytes + ROW_OVERHEAD_BYTES) * INDEX_FACTOR / 1_000_000


def format_report(stats: PlanStats, *, approximate: bool, source: str = "") -> str:
    s = stats
    avg_bytes = s.text_bytes / s.chunks if s.chunks else 0.0
    avg_tokens = s.content_tokens / s.chunks if s.chunks else 0.0
    by_source = ", ".join(f"{k} {v:,}" for k, v in sorted(s.documents_by_source.items()))
    total_tokens = s.embed_tokens + s.breakpoint_tokens
    if s.chunk_dedupe_skipped is None:
        dedupe = "not run (plan predates issue #44)"
    else:
        by = ", ".join(f"{k} {v:,}" for k, v in sorted(s.chunk_dedupe_by_source.items())) or "none"
        dedupe = (f"{s.chunk_dedupe_skipped:,} skipped ({s.chunk_dedupe_exact:,} exact, "
                  f"{s.chunk_dedupe_near:,} near-duplicate; {by}); guard kept {s.chunk_dedupe_guarded:,} "
                  "(a negation, number or comparison differs)")  # fmt: skip
    lines = [
        "Corpus ingest dry run (no API calls)",
        *([f"  Plan:             {source}"] if source else []),
        f"Documents          {s.documents_loaded:,} loaded ({by_source})",
        f"  dropped          {s.pmid_dropped:,} abstracts of a guideline in the corpus (same PMID), "
        f"{s.duplicate_dropped:,} exact duplicates",
        f"  kept             {s.documents:,} ({s.documents_without_chunks:,} with no chunks)",
        f"Sections           {s.sections:,} kept",
        f"Sections dropped   {s.sections_dropped:,} boilerplate ({s.sections_dropped_chars:,} characters)",
        f"Chunks             {s.chunks:,} (tokens min {s.min_chunk_tokens}, avg {avg_tokens:.0f}, "
        f"max {s.max_chunk_tokens}; longest embed_text {s.max_embed_tokens})",
        f"  missing url      {s.chunks_missing_url:,}, missing section {s.chunks_missing_section:,}",
        f"  chunk dedupe     {dedupe}",
        f"Tokens             {s.embed_tokens:,} to embed (section + content), {s.content_tokens:,} of content, "
        f"{s.breakpoint_tokens:,} for semantic breakpoints",
        f"Estimated cost     ${estimate_cost_usd(total_tokens):.2f} at ${PRICE_PER_MILLION_TOKENS}/1M tokens "
        f"({total_tokens:,} tokens)",
        f"Estimated size     {estimate_mb(s.chunks, avg_bytes):.0f} MB (chunks x ({DIMS}x4 + {avg_bytes:.0f} text "
        f"bytes + {ROW_OVERHEAD_BYTES}) x{INDEX_FACTOR} for indexes)",
    ]
    if approximate:
        lines.append(
            "Note: breakpoints inside long sections came from a local hashed bag-of-words stand-in, not OpenAI, "
            "so those boundaries are approximate; counts are close to the real run."
        )
    return "\n".join(lines)


def format_ingest_report(
    stats: PlanStats,
    chunks: list,
    result,
    *,
    tokens_embedded: int,
    corpus_stats: dict | None,
    plan_source: str,
) -> str:
    """The report after a real ingest. `chunks` are the plan's ChunkRecords; `result` an ingest.IngestResult."""
    stored = [c for c in chunks if c.doc_id in result.stored_doc_ids]
    by_cancer: dict[str, list] = {}
    by_source: dict[str, list] = {}
    for c in stored:
        keys = [(k, by_cancer) for k in (c.cancers or ["(none)"])] + [(f"{c.source} ({c.trust})", by_source)]
        for key, table in keys:
            row = table.setdefault(key, [set(), 0])
            row[0].add(c.doc_id)
            row[1] += 1

    def rows(table: dict) -> list[str]:
        return [f"  {k:<18} {len(v[0]):>5,} docs {v[1]:>7,} chunks" for k, v in sorted(table.items())]

    lines = [
        "Corpus ingest report",
        f"  Plan:            {plan_source}",
        f"Documents          {result.planned:,} planned: {result.skipped:,} skipped (unchanged), "
        f"{result.written:,} written, {len(result.failed):,} failed",
        f"Chunks written     {result.chunks_written:,}",
        f"Chunk dedupe       {stats.chunk_dedupe_skipped or 0:,} skipped as duplicates in the plan "
        f"({stats.chunk_dedupe_exact:,} exact, {stats.chunk_dedupe_near:,} near); "
        f"{stats.documents_without_chunks:,} documents left with no chunks are never ingested",
        f"Stored from plan   {len({c.doc_id for c in stored}):,} documents, {len(stored):,} chunks",
        "By cancer (a document can have several)",
        *rows(by_cancer),
        "By source (trust)",
        *rows(by_source),
        f"Tokens embedded    {tokens_embedded:,} this run (breakpoints + chunks), actual cost "
        f"${estimate_cost_usd(tokens_embedded):.4f} at ${PRICE_PER_MILLION_TOKENS}/1M",
    ]
    if result.stale:
        more = " ..." if len(result.stale) > 10 else ""
        lines.append(f"Not in the plan    {len(result.stale):,} documents in the database (kept; --rebuild removes "
                     f"them): {', '.join(result.stale[:10])}{more}")  # fmt: skip
    for doc_id, why in result.failed:
        lines.append(f"FAILED             {doc_id}: {why}")
    if corpus_stats is not None:
        r = corpus_stats.get("rows", {})
        lines.append(f"corpus_stats()     documents {r.get('documents', 0):,}, chunks {r.get('chunks', 0):,}, "
                     f"corpus_meta {r.get('corpus_meta', 0):,}; total {corpus_stats.get('total_pretty')} "
                     f"({corpus_stats.get('total_bytes', 0):,} bytes)")  # fmt: skip
    return "\n".join(lines)
