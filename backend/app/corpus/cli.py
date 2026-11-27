"""`scripts/ingest_corpus.py`: chunk the corpus, embed it with OpenAI and write it to Supabase (docs/PRD-corpus-ingest.md).

    python scripts/ingest_corpus.py --dry-run                 # report only, no API calls
    python scripts/ingest_corpus.py --dry-run --rechunk       # ignore an existing plan
    python scripts/ingest_corpus.py --limit 3                 # ingest the first 3 planned documents
    python scripts/ingest_corpus.py                           # full ingest; re-runs skip unchanged documents
    python scripts/ingest_corpus.py --rebuild                 # delete the corpus in Supabase and re-ingest
    python scripts/ingest_corpus.py --env PATH --corpus-dir PATH

Exit codes: 0 ok, 1 some documents failed (re-run to retry them), 2 refused or stopped (keys, guards, Supabase down).
"""

import argparse
import sys
from pathlib import Path

import httpx

from app.config import REPO_ROOT, Settings
from app.corpus import local_embedder
from app.corpus.config import ChunkerConfig
from app.corpus.embed import EmbedAuthError, EmbedError, OpenAIEmbedder
from app.corpus.ingest import GuardRefused, check_guards, ingest, meta_for, rebuild
from app.corpus.local_embedder import HashingEmbedder
from app.corpus.plan import PLAN_FILE, PlanMismatch, load_or_build_plan, read_plan
from app.corpus.report import format_ingest_report, format_report
from app.corpus.supabase import SupabaseCorpus, SupabaseError

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[2] / ".cache" / "corpus"
DEFAULT_ENV = REPO_ROOT / ".env"
DRY_RUN_PLAN_FILE = "chunks.dry-run.jsonl"
"""The stand-in embedder's plan goes here, never to chunks.jsonl, so ingest can't pick up approximate boundaries."""
EMBEDDER_ID = "openai:text-embedding-3-small"


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
    args = parser.parse_args(argv)
    config = ChunkerConfig()

    if args.dry_run:
        return _dry_run(args.corpus_dir, config, rechunk=args.rechunk)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    kwargs = {"sleep": sleep} if sleep else {}
    try:
        embedder, store = _clients(args.env, openai_transport, supabase_transport, kwargs)
    except (_ConfigError, EmbedAuthError) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 2
    with embedder, store:
        return _ingest(args, config, embedder, store)


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


def _ingest(args, config: ChunkerConfig, embedder: OpenAIEmbedder, store: SupabaseCorpus) -> int:
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
        if args.rebuild:
            rebuild(store, expected)
            print("--rebuild: deleted every document, chunk and lock; wrote this run's locks.", flush=True)
        result = ingest(plan, embedder, store, limit=args.limit, progress=lambda m: print(m, flush=True))
        stats = store.corpus_stats()
    except GuardRefused as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 2
    except (EmbedError, SupabaseError) as e:  # a rejected key or Supabase down: every later document would fail
        print(f"Stopped: {e}", file=sys.stderr)
        return 2
    print()
    print(format_ingest_report(plan.stats, plan.chunks, result, tokens_embedded=embedder.total_tokens,
                               corpus_stats=stats, plan_source=plan_source))  # fmt: skip
    return 1 if result.failed else 0


def _dry_run(corpus_dir: Path, config: ChunkerConfig, *, rechunk: bool) -> int:
    real = corpus_dir / PLAN_FILE
    if real.exists() and not rechunk:
        plan = read_plan(real)
        embedder_id = plan.meta.get("breakpoint_embedder")
        if plan.meta.get("chunker_config_hash") == config.config_hash():
            source = f"existing plan {real} (breakpoints from {embedder_id})"
            print(format_report(plan.stats, approximate=embedder_id == local_embedder.ID, source=source))
            return 0
        print(f"{real} was built with another chunker config; estimating from a fresh local plan instead.\n")
    path = corpus_dir / DRY_RUN_PLAN_FILE
    plan, _ = load_or_build_plan(
        corpus_dir, HashingEmbedder(), embedder_id=local_embedder.ID, config=config, rechunk=True, plan_path=path
    )
    print(format_report(plan.stats, approximate=True, source=f"{path} (written for inspection; ingest ignores it)"))
    return 0
