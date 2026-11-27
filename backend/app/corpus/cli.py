"""`scripts/ingest_corpus.py`: build the chunk plan and report on it. Embedding and the Supabase write are issue #45.

    python scripts/ingest_corpus.py --dry-run                 # report only, no API calls
    python scripts/ingest_corpus.py --dry-run --rechunk       # ignore an existing plan
    python scripts/ingest_corpus.py --dry-run --corpus-dir /path/to/corpus
"""

import argparse
from pathlib import Path

from app.corpus import local_embedder
from app.corpus.config import ChunkerConfig
from app.corpus.local_embedder import HashingEmbedder
from app.corpus.plan import PLAN_FILE, load_or_build_plan, read_plan
from app.corpus.report import format_report

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[2] / ".cache" / "corpus"
DRY_RUN_PLAN_FILE = "chunks.dry-run.jsonl"
"""The stand-in embedder's plan goes here, never to chunks.jsonl, so ingest can't pick up approximate boundaries."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--dry-run", action="store_true", help="print the report; no API calls")
    parser.add_argument("--rechunk", action="store_true", help="rebuild the chunk plan")
    args = parser.parse_args(argv)
    config = ChunkerConfig()

    if args.dry_run:
        return _dry_run(args.corpus_dir, config, rechunk=args.rechunk)
    # Issue #45: build the real plan with the OpenAI embedder (#43), then embed and write to Supabase:
    #   plan, _ = load_or_build_plan(args.corpus_dir, OpenAIEmbedder(...), embedder_id="openai:text-embedding-3-small",
    #                                config=config, rechunk=args.rechunk)
    raise NotImplementedError("embedding and writing to Supabase is issue #45; use --dry-run")


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
