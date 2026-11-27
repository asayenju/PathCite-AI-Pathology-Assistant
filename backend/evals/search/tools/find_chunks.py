"""Find candidate chunks for golden rows. Run from backend/:

    python evals/search/tools/find_chunks.py "breslow thickness definition" ["another query" ...] [--k 8] [--chars 1500]
    python evals/search/tools/find_chunks.py --ids 123 456      # print these chunks in full

Runs the hybrid search (match_chunks) and prints each hit with its chunk id, doc id, the chunk's cancer tags, trust,
section and content (first --chars characters). Read-only; keys come from the repo-root .env (or --env) and are never
printed. Queries are embedded with OPEN_AI_VECTOR_KEY.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.corpus.embed import OpenAIEmbedder  # noqa: E402
from app.corpus.supabase import SupabaseCorpus  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_get import client, get  # noqa: E402


def show(row: dict, cancers: list[str], chars: int) -> None:
    text = " ".join(row["content"].split())[:chars]
    print(f"\n[{row['chunk_id'] if 'chunk_id' in row else row['id']}] doc={row['doc_id']} {cancers} trust={row['trust']}")
    print(f"   section: {row['section'][:160]}")
    print(f"   {text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("queries", nargs="*")
    parser.add_argument("--ids", nargs="*", type=int)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--chars", type=int, default=1500)
    parser.add_argument("--env")
    args = parser.parse_args()
    settings = Settings(_env_file=args.env) if args.env else Settings()

    with client(args.env) as c:
        if args.ids:
            ids = ",".join(str(i) for i in args.ids)
            for row in get(c, "/chunks", {"select": "id,doc_id,section,content,trust,cancers", "id": f"in.({ids})"}):
                show(row, row["cancers"], args.chars)
            return 0
        with (
            OpenAIEmbedder.from_settings(settings) as embedder,
            SupabaseCorpus(settings.supabase_url, settings.supabase_secret_key.get_secret_value()) as store,
        ):
            vectors = embedder.embed(args.queries)
            for query, vector in zip(args.queries, vectors):
                rows = store.match_chunks(vector, query, k=args.k)
                print(f"\n========== {query}")
                tags = {}
                if rows:
                    ids = ",".join(str(r["chunk_id"]) for r in rows)
                    tags = {t["id"]: t["cancers"] for t in get(c, "/chunks", {"select": "id,cancers", "id": f"in.({ids})"})}
                for r in rows:
                    show(r, tags.get(r["chunk_id"], []), args.chars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
