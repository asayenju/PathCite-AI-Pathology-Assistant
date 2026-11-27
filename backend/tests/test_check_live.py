"""Opt-in live test of the evidence search against the real OpenAI and Supabase (`pytest -m check -s`).

Three sample claims each return 1..8 passages, at most 2 per document, each with a URL and similarity >= 0.35.
Costs a fraction of a cent (three claim embeddings). Keys come from the env file named by CHECK_ENV (default: the
repo-root .env) and are never printed; only titles, sections, URLs and scores are printed.
"""

import os
from collections import Counter
from pathlib import Path

import pytest

from app.check.search import EvidenceSearch
from app.config import REPO_ROOT, Settings

pytestmark = pytest.mark.check

ENV = Path(os.environ.get("CHECK_ENV", REPO_ROOT / ".env"))

CLAIMS = [
    "Colon cancers are tested for mismatch repair proteins.",
    "Breslow thickness is how wide the melanoma spot was.",
    "Diffuse large B-cell lymphoma is treated with R-CHOP chemotherapy.",
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("claim", CLAIMS)
async def test_live_search(claim):
    settings = Settings(_env_file=ENV)
    if not (settings.supabase_url and settings.supabase_secret_key and settings.openai_vector_key):
        pytest.skip(f"SUPABASE_URL, SUPABASE_SECRET_KEY and OPEN_AI_VECTOR_KEY are needed in {ENV}")
    async with EvidenceSearch.from_settings(settings) as search:
        results = await search.search(claim)

    print(f"\n=== {claim}: {len(results)} results")
    for r in results:
        print(f"  sim {r.similarity:.3f} rrf {r.rrf_score:.4f} vec {r.vector_rank} kw {r.text_rank} "
              f"[{r.trust}/{r.source}] {r.title} | {r.section[:60]} | {r.url}")  # fmt: skip

    assert 1 <= len(results) <= 8
    assert max(Counter(r.doc_id for r in results).values()) <= 2
    for r in results:
        assert r.url.startswith("http")
        assert r.similarity >= 0.35
        assert r.content.strip()
    assert [r.rrf_score for r in results] == sorted((r.rrf_score for r in results), reverse=True)
