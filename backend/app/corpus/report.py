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
    dedupe = "not run (issue #44)" if s.chunk_dedupe_skipped is None else f"{s.chunk_dedupe_skipped:,} skipped"
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
