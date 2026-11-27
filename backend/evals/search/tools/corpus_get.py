"""Read-only PostgREST GET helper for browsing the corpus while writing golden rows. Run from backend/:

    python evals/search/tools/corpus_get.py /chunks select=id,doc_id,section,trust,content cancers=cs.{melanoma} limit=5
    python evals/search/tools/corpus_get.py /documents select=id,title,trust,cancers id=eq.12

Prints the JSON response. Needs SUPABASE_URL and SUPABASE_SECRET_KEY in the repo-root .env (or --env PATH); the key
is sent only in the `apikey` header and is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402


def client(env: str | None = None) -> httpx.Client:
    settings = Settings(_env_file=env) if env else Settings()
    if not (settings.supabase_url and settings.supabase_secret_key):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SECRET_KEY in the repo-root .env")
    return httpx.Client(
        base_url=settings.supabase_url.rstrip("/") + "/rest/v1",
        headers={"apikey": settings.supabase_secret_key.get_secret_value()},
        timeout=60,
    )


def get(c: httpx.Client, path: str, params: dict[str, str]) -> list[dict]:
    r = c.get(path, params=params)
    if r.status_code != 200:
        raise SystemExit(f"HTTP {r.status_code}")
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("params", nargs="*", help="key=value PostgREST query parameters")
    parser.add_argument("--env")
    args = parser.parse_args()
    params = dict(p.split("=", 1) for p in args.params)
    with client(args.env) as c:
        print(json.dumps(get(c, args.path, params), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
