"""Opt-in live smoke test of the ingest against the real Supabase project and OpenAI (`pytest -m corpus -s`).

Re-ingests the first 3 documents of the real chunk plan: deletes them, runs the CLI with `--limit 3`, and checks
through REST and `corpus_stats()` that their stored chunks match the plan. Safe next to a full ingest: it never uses
--rebuild, and the 3 documents end up exactly as the full ingest wrote them (a fraction of a cent of embeddings).

Needs the real plan (`chunks.jsonl`, built by the first real ingest) and the keys. Override the locations with
CORPUS_DIR and CORPUS_ENV (defaults: backend/.cache/corpus and the repo-root .env).
"""

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.corpus import cli
from app.corpus.config import ChunkerConfig
from app.corpus.ingest import documents_in_order
from app.corpus.plan import PLAN_FILE, read_plan
from app.corpus.quality import apply_quality_filter
from app.corpus.supabase import SupabaseCorpus

pytestmark = pytest.mark.corpus

CORPUS_DIR = Path(os.environ.get("CORPUS_DIR", cli.DEFAULT_CORPUS_DIR))
ENV = Path(os.environ.get("CORPUS_ENV", cli.DEFAULT_ENV))


def test_live_ingest_of_three_documents(capsys):
    plan_path = CORPUS_DIR / PLAN_FILE
    if not plan_path.exists():
        pytest.skip(f"no real chunk plan at {plan_path}: run scripts/ingest_corpus.py first")
    settings = Settings(_env_file=ENV)
    if not (settings.supabase_url and settings.supabase_secret_key and settings.openai_vector_key):
        pytest.skip(f"SUPABASE_URL, SUPABASE_SECRET_KEY and OPEN_AI_VECTOR_KEY are needed in {ENV}")
    plan = read_plan(plan_path)
    if plan.meta.get("chunker_config_hash") != ChunkerConfig().config_hash():
        pytest.skip("the real plan was built with another chunker config")
    plan, _ = apply_quality_filter(plan)  # what ingest stores
    first3 = documents_in_order(plan)[:3]

    with SupabaseCorpus(settings.supabase_url, settings.supabase_secret_key.get_secret_value()) as store:
        before = store.corpus_stats()["rows"]
        store.delete_documents([d for d, _ in first3])
        assert cli.main(["--limit", "3", "--corpus-dir", str(CORPUS_DIR), "--env", str(ENV)]) == 0
        out = capsys.readouterr().out
        print(out)
        assert "3 planned: 0 skipped (unchanged), 3 written, 0 failed" in out

        stored = store.existing_hashes()
        for doc_id, chunks in first3:
            assert stored[doc_id] == [c.content_hash for c in chunks], doc_id
        after = store.corpus_stats()["rows"]
        if before["documents"] >= 3 and all(d in stored for d, _ in first3):
            assert after["documents"] == max(before["documents"], 3)
        assert after["chunks"] >= sum(len(cs) for _, cs in first3)

        # A second --limit 3 run embeds nothing.
        assert cli.main(["--limit", "3", "--corpus-dir", str(CORPUS_DIR), "--env", str(ENV)]) == 0
        out = capsys.readouterr().out
        assert "3 skipped (unchanged), 0 written" in out and "Tokens embedded    0 this run" in out
