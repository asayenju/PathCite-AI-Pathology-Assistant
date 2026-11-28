"""`scripts/ingest_corpus.py`: chunk the corpus, embed it with OpenAI and write it to Supabase (docs/PRD-corpus-ingest.md).

    python scripts/ingest_corpus.py --dry-run                 # report only, no API calls
    python scripts/ingest_corpus.py --dry-run --rechunk       # ignore an existing plan
    python scripts/ingest_corpus.py --limit 3                 # ingest the first 3 planned documents
    python scripts/ingest_corpus.py                           # full ingest; re-runs skip unchanged documents
    python scripts/ingest_corpus.py --prune                   # also delete stored documents the plan no longer has
    python scripts/ingest_corpus.py --rebuild                 # delete the corpus in Supabase and re-ingest
    python scripts/ingest_corpus.py --env PATH --corpus-dir PATH

After every run that reaches the report (not --dry-run, not refused), the local BM25 keyword index of the claim
checker (app/check/bm25.py) is rebuilt from Supabase if it no longer matches the stored corpus; a failed rebuild only
warns (run scripts/build_bm25_index.py) and doesn't change the exit code.

Exit codes: 0 ok, 1 some documents failed (re-run to retry them), 2 refused or stopped (keys, guards, Supabase down).
"""

import argparse
import sys
from pathlib import Path

import httpx

from app.check import bm25
from app.config import REPO_ROOT, Settings
from app.corpus import local_embedder
from app.corpus.config import ChunkerConfig
from app.corpus.embed import EmbedAuthError, EmbedError, OpenAIEmbedder
from app.corpus.ingest import GuardRefused, check_guards, ingest, meta_for, rebuild
from app.corpus.local_embedder import HashingEmbedder
from app.corpus.plan import PLAN_FILE, PlanMismatch, load_or_build_plan, read_plan
from app.corpus.quality import QualityFilterConfig, apply_quality_filter, format_quality_report
from app.corpus.report import format_ingest_report, format_report
from app.corpus.supabase import SupabaseCorpus, SupabaseError

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[2] / ".cache" / "corpus"
DEFAULT_ENV = REPO_ROOT / ".env"
DRY_RUN_PLAN_FILE = "chunks.dry-run.jsonl"
"""The stand-in embedder's plan goes here, never to chunks.jsonl, so ingest can't pick up approximate boundaries."""
EMBEDDER_ID = "openai:text-embedding-3-small"
DEFAULT_BM25_DIR = bm25.DEFAULT_DIR


def main(
    argv: list[str] | None = None,
    *,
    openai_transport: httpx.BaseTransport | None = None,
    supabase_transport: httpx.BaseTransport | None = None,
    sleep=None,
) -> int:
    """The transports and sleep are for tests (httpx.MockTransport fakes)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV, help="the .env with the keys (default: repo root)")
    parser.add_argument("--dry-run", action="store_true", help="print the report; no API calls")
    parser.add_argument("--rechunk", action="store_true", help="rebuild the chunk plan")
    parser.add_argument("--limit", type=int, default=None, help="ingest only the first N planned documents")
    parser.add_argument("--rebuild", action="store_true", help="delete the corpus in Supabase, then re-ingest")
    parser.add_argument("--prune", action="store_true",
                        help="after ingest, delete stored documents (and their chunks) that the plan no longer has")
    args = parser.parse_args(argv)
    config = ChunkerConfig()
    quality = QualityFilterConfig()

    if args.dry_run:
        return _dry_run(args.corpus_dir, config, quality, rechunk=args.rechunk)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    kwargs = {"sleep": sleep} if sleep else {}
    try:
        embedder, store = _clients(args.env, openai_transport, supabase_transport, kwargs)
    except (_ConfigError, EmbedAuthError) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 2
    with embedder, store:
        return _ingest(args, config, quality, embedder, store)


class _ConfigError(Exception):
    pass


def _clients(env: Path, openai_transport, supabase_transport, kwargs) -> tuple[OpenAIEmbedder, SupabaseCorpus]:
    if env != DEFAULT_ENV and not env.is_file():
        raise _ConfigError(f"no env file at {env}")
    settings = Settings(_env_file=env)
    missing = [name for name, v in [("SUPABASE_URL", settings.supabase_url),
                                    ("SUPABASE_SECRET_KEY", settings.supabase_secret_key),
                                    ("OPEN_AI_VECTOR_KEY", settings.openai_vector_key)]
               if v is None or not (v if isinstance(v, str) else v.get_secret_value()).strip()]  # fmt: skip
    if missing:
        raise _ConfigError(f"set {', '.join(missing)} in {env}")
    embedder = OpenAIEmbedder.from_settings(settings, transport=openai_transport, **kwargs)
    store = SupabaseCorpus(
        settings.supabase_url, settings.supabase_secret_key.get_secret_value(), transport=supabase_transport, **kwargs
    )
    return embedder, store


def _ingest(args, config: ChunkerConfig, quality: QualityFilterConfig, embedder: OpenAIEmbedder,
            store: SupabaseCorpus) -> int:  # fmt: skip
    expected = meta_for(model=embedder.model, dims=embedder.dims, config_hash=config.config_hash())
    try:
        # Guards first: a refused run spends nothing on breakpoint embeddings.
        if not args.rebuild and check_guards(store, expected):
            print("First ingest: wrote the corpus_meta locks (model, dims, chunker config).", flush=True)
        try:
            plan, reused = load_or_build_plan(
                args.corpus_dir, embedder, embedder_id=EMBEDDER_ID, config=config, rechunk=args.rechunk
            )
        except PlanMismatch as e:
            print(f"Refused: {e}", file=sys.stderr)
            return 2
        plan_source = f"{args.corpus_dir / PLAN_FILE} ({'reused' if reused else 'built this run'})"
        print(f"Plan: {plan_source}: {plan.stats.documents:,} documents, {plan.stats.chunks:,} chunks", flush=True)
        plan, filtered = apply_quality_filter(plan, quality)
        print(f"Quality filter: {filtered.chunks_before - filtered.chunks_after:,} chunks removed, "
              f"{plan.stats.chunks:,} to store", flush=True)  # fmt: skip
        if args.rebuild:
            rebuild(store, expected)
            print("--rebuild: deleted every document, chunk and lock; wrote this run's locks.", flush=True)
        result = ingest(plan, embedder, store, limit=args.limit, prune=args.prune,
                        progress=lambda m: print(m, flush=True))  # fmt: skip
        filter_note = _record_filter_hash(store, filtered.filter_hash, complete=not result.failed and not args.limit)
        stats = store.corpus_stats()
    except GuardRefused as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 2
    except (EmbedError, SupabaseError) as e:  # a rejected key or Supabase down: every later document would fail
        print(f"Stopped: {e}", file=sys.stderr)
        return 2
    print()
    print(format_ingest_report(plan.stats, plan.chunks, result, tokens_embedded=embedder.total_tokens,
                               corpus_stats=stats, plan_source=plan_source,
                               quality_lines=[*format_quality_report(filtered, quality), filter_note]))  # fmt: skip
    _refresh_bm25(store, DEFAULT_BM25_DIR)
    return 1 if result.failed else 0


def _refresh_bm25(store: SupabaseCorpus, directory: Path) -> None:
    """Rebuild the claim checker's BM25 index if the stored corpus changed. Never fails the ingest."""
    print()
    try:
        index, rebuilt = bm25.refresh_from_store(store, directory, progress=lambda m: print(m, flush=True))
    except Exception as e:  # noqa: BLE001 - the ingest itself succeeded; only the type is shown
        print(f"BM25 keyword index NOT rebuilt ({type(e).__name__}): run python scripts/build_bm25_index.py",
              file=sys.stderr)  # fmt: skip
        return
    if not rebuilt:
        print(f"BM25 keyword index up to date: {directory}")
        return
    size = bm25.dir_size(directory) / 1e6
    print(f"BM25 keyword index rebuilt: {bm25.manifest_summary(index.manifest)}; {size:.1f} MB at {directory}")
    print("Restart the backend so /check loads it.")


def _record_filter_hash(store: SupabaseCorpus, filter_hash: str, *, complete: bool) -> str:
    """corpus_meta records the quality filter the stored corpus reflects. A different stored hash is not refused (the
    filter only removes chunks, and resume re-ingests the documents it changed); it's overwritten once a full run
    finishes with no failures."""
    stored = store.read_meta().get("quality_filter_hash")
    if stored == filter_hash:
        return f"  quality_filter_hash {filter_hash[:12]} (unchanged in corpus_meta)"
    if not complete:
        return (f"  quality_filter_hash {filter_hash[:12]} not recorded (stored {str(stored)[:12]}): "
                "a --limit run or failed documents")  # fmt: skip
    store.write_meta({"quality_filter_hash": filter_hash})
    return f"  quality_filter_hash {filter_hash[:12]} recorded in corpus_meta (was {str(stored)[:12]})"


def _dry_run(corpus_dir: Path, config: ChunkerConfig, quality: QualityFilterConfig, *, rechunk: bool) -> int:
    real = corpus_dir / PLAN_FILE
    if real.exists() and not rechunk:
        plan = read_plan(real)
        embedder_id = plan.meta.get("breakpoint_embedder")
        if plan.meta.get("chunker_config_hash") == config.config_hash():
            source = f"existing plan {real} (breakpoints from {embedder_id})"
            _print_dry_run(plan, quality, approximate=embedder_id == local_embedder.ID, source=source)
            return 0
        print(f"{real} was built with another chunker config; estimating from a fresh local plan instead.\n")
    path = corpus_dir / DRY_RUN_PLAN_FILE
    plan, _ = load_or_build_plan(
        corpus_dir, HashingEmbedder(), embedder_id=local_embedder.ID, config=config, rechunk=True, plan_path=path
    )
    _print_dry_run(plan, quality, approximate=True, source=f"{path} (written for inspection; ingest ignores it)")
    return 0


def _print_dry_run(plan, quality: QualityFilterConfig, *, approximate: bool, source: str) -> None:
    """The stats are after the quality filter (what ingest would store); its section says what it removed."""
    filtered, result = apply_quality_filter(plan, quality)
    lines = format_quality_report(result, quality, show_patterns=True)
    print(format_report(filtered.stats, approximate=approximate, source=source, quality_lines=lines))
