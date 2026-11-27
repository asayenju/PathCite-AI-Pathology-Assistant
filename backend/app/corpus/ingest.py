"""Steps 9-11 of docs/PRD-corpus-ingest.md: guards, then embed and write each planned document to Supabase.

- **Guards:** `corpus_meta` locks `embedding_model`, `dims` and `chunker_config_hash`. A mismatch (or a corpus with
  documents but no locks) is refused unless `rebuild`, which deletes everything first. The first run writes them.
- **Resume:** a document whose stored `content_hash` list (in `ord` order) equals the plan's is skipped, so a re-run
  only embeds what is missing or changed.
- **Atomic per document:** `ingest_document` replaces a document's chunks in one transaction. Embeddings are fetched
  for several documents at a time (fewer requests), but each document is written on its own.
- **Errors:** a document whose embedding or write fails is reported and the run continues (`failed` is non-empty).
  A rejected key or an unreachable project stops the run, since every later document would fail the same way.
- Documents in the database but no longer in the plan are reported, never deleted (only `rebuild` deletes).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.corpus.embed import EmbedAuthError, EmbedError
from app.corpus.plan import ChunkRecord, Plan
from app.corpus.supabase import SupabaseAuthError, SupabaseCorpus, SupabaseError, SupabaseUnreachable

EMBED_GROUP_CHUNKS = 512
"""Chunks embedded per group of documents; an interrupted run loses at most one group's embeddings."""


class GuardRefused(Exception):
    """corpus_meta doesn't match this run's model, dims or chunker config."""


@dataclass
class IngestResult:
    planned: int = 0
    skipped: int = 0
    written: int = 0
    chunks_written: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    """(doc_id, reason); reasons never contain keys or chunk text."""
    stale: list[str] = field(default_factory=list)
    """Document ids in the database that the plan no longer has."""
    stored_doc_ids: set[str] = field(default_factory=set)
    """Plan documents now stored with the plan's chunks (skipped as unchanged, or written)."""


def meta_for(*, model: str, dims: int, config_hash: str) -> dict:
    return {"embedding_model": model, "dims": dims, "chunker_config_hash": config_hash}


def check_guards(store: SupabaseCorpus, expected: dict) -> bool:
    """Returns whether the locks were written (first run). Raises GuardRefused on a mismatch."""
    stored = store.read_meta()
    if not stored:
        if store.document_ids():
            raise GuardRefused("the corpus has documents but no corpus_meta locks; rerun with --rebuild")
        store.write_meta(expected)
        return True
    diffs = [f"{k}: stored {stored.get(k)!r}, this run {v!r}" for k, v in expected.items() if stored.get(k) != v]
    if diffs:
        raise GuardRefused("corpus_meta doesn't match this run (" + "; ".join(diffs) + "); rerun with --rebuild "
                           "to delete the corpus and re-ingest it")  # fmt: skip
    return False


def rebuild(store: SupabaseCorpus, expected: dict) -> None:
    """--rebuild: delete every document (chunks cascade) and the locks, then write this run's locks."""
    store.delete_all()
    store.write_meta(expected)


def documents_in_order(plan: Plan) -> list[tuple[str, list[ChunkRecord]]]:
    by_doc: dict[str, list[ChunkRecord]] = {}
    for c in plan.chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    return [(d, sorted(cs, key=lambda c: c.ord)) for d, cs in by_doc.items()]


def doc_payload(c: ChunkRecord) -> dict:
    return {"id": c.doc_id, "source": c.source, "trust": c.trust, "title": c.title, "url": c.url, "pmid": c.pmid,
            "journal": c.journal, "year": c.year, "pub_types": c.pub_types, "cancers": c.cancers}  # fmt: skip


def chunk_payload(c: ChunkRecord, embedding: list[float]) -> dict:
    return {"ord": c.ord, "section": c.section, "content": c.content, "content_hash": c.content_hash,
            "token_count": c.token_count, "embedding": embedding}  # fmt: skip


def ingest(
    plan: Plan,
    embedder,
    store: SupabaseCorpus,
    *,
    limit: int | None = None,
    progress: Callable[[str], None] = lambda _: None,
    group_chunks: int = EMBED_GROUP_CHUNKS,
) -> IngestResult:
    docs = documents_in_order(plan)
    if limit is not None:
        docs = docs[:limit]
    result = IngestResult(planned=len(docs))
    stored = store.existing_hashes()
    plan_ids = {d for d, _ in documents_in_order(plan)}
    result.stale = sorted(d for d in store.document_ids() if d not in plan_ids)

    todo: list[tuple[str, list[ChunkRecord]]] = []
    for doc_id, chunks in docs:
        if stored.get(doc_id) == [c.content_hash for c in chunks]:
            result.skipped += 1
            result.stored_doc_ids.add(doc_id)
        else:
            todo.append((doc_id, chunks))
    progress(f"{len(docs):,} documents planned: {result.skipped:,} unchanged, {len(todo):,} to embed and write")

    for group in _groups(todo, group_chunks):
        texts = [c.embed_text for _, chunks in group for c in chunks]
        try:
            vectors = embedder.embed(texts)
        except EmbedAuthError:
            raise
        except (EmbedError, ValueError) as e:
            for doc_id, _ in group:
                result.failed.append((doc_id, f"embedding failed: {e}"))
            progress(f"  embedding failed for {len(group)} documents: {e}")
            continue
        i = 0
        for doc_id, chunks in group:
            vecs, i = vectors[i : i + len(chunks)], i + len(chunks)
            payload = [chunk_payload(c, v) for c, v in zip(chunks, vecs, strict=True)]
            try:
                n = store.ingest_document(doc_payload(chunks[0]), payload)
            except (SupabaseAuthError, SupabaseUnreachable):
                raise
            except SupabaseError as e:
                result.failed.append((doc_id, str(e)))
                progress(f"  FAILED {doc_id}: {e}")
                continue
            if n != len(chunks):
                result.failed.append((doc_id, f"wrote {n} chunks, expected {len(chunks)}"))
                continue
            result.written += 1
            result.chunks_written += n
            result.stored_doc_ids.add(doc_id)
        done = result.written + len(result.failed)
        progress(f"  {done:,}/{len(todo):,} documents processed ({result.chunks_written:,} chunks written, "
                 f"{len(result.failed)} failed)")  # fmt: skip
    return result


def _groups(todo: list[tuple[str, list[ChunkRecord]]], max_chunks: int):
    group: list[tuple[str, list[ChunkRecord]]] = []
    n = 0
    for item in todo:
        if group and n + len(item[1]) > max_chunks:
            yield group
            group, n = [], 0
        group.append(item)
        n += len(item[1])
    if group:
        yield group
