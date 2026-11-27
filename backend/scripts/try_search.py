"""Try the hybrid search (supabase/migrations/20261127010000_match_chunks.sql) on a few claims. Run from backend/:

    python scripts/try_search.py "Breslow thickness of 1.2 mm is a T2 melanoma." ["another claim" ...] [--k 8]

For each claim: embeds it (OPEN_AI_VECTOR_KEY), calls match_chunks, and prints every result with its rank in the
vector list, the keyword list and the fused list. Nothing is logged or written to disk. Keys come from the repo-root
.env and are never printed.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.corpus.embed import OpenAIEmbedder  # noqa: E402
from app.corpus.supabase import SupabaseCorpus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("claims", nargs="+")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--per-doc", type=int, default=2)
    parser.add_argument("--chars", type=int, default=200)
    parser.add_argument("--rrf-k", type=int, default=60)
    args = parser.parse_args()

    settings = Settings()
    if not (settings.supabase_url and settings.supabase_secret_key and settings.openai_vector_key):
        print("Set SUPABASE_URL, SUPABASE_SECRET_KEY and OPEN_AI_VECTOR_KEY in the repo-root .env", file=sys.stderr)
        return 2

    with (
        OpenAIEmbedder.from_settings(settings) as embedder,
        SupabaseCorpus(settings.supabase_url, settings.supabase_secret_key.get_secret_value()) as store,
    ):
        vectors = embedder.embed(args.claims)
        for claim, vector in zip(args.claims, vectors):
            rows = store._json(
                "POST",
                "/rpc/match_chunks",
                json={
                    "query_embedding": json.dumps(vector),
                    "query_text": claim,
                    "k": args.k,
                    "per_doc": args.per_doc,
                    "rrf_k": args.rrf_k,
                },
            )
            print(f"\n=== {claim}")
            both = sum(1 for r in rows if r["vector_rank"] and r["text_rank"])
            only_v = sum(1 for r in rows if r["vector_rank"] and not r["text_rank"])
            only_t = sum(1 for r in rows if r["text_rank"] and not r["vector_rank"])
            print(f"    {len(rows)} results: {both} found by both, {only_v} by meaning only, {only_t} by keywords only")
            for i, r in enumerate(rows, 1):
                v = r["vector_rank"] or "-"
                t = r["text_rank"] or "-"
                text = " ".join(r["content"].split())[: args.chars]
                print(
                    f"\n #{i}  vec {v:>2}  kw {t:>2}  sim {r['similarity']:.3f}  "
                    f"[{r['trust']}/{r['source']}{', ' + str(r['year']) if r['year'] else ''}]"
                )
                print(f"     {r['section'][:140]}")
                print(f"     {r['url']}")
                print(f"     {text}")
        print(f"\n(embedding tokens used: {embedder.total_tokens})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
