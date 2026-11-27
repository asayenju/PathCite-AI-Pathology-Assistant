"""Check the corpus migration against the real Supabase project (issue #42).

Run after pasting supabase/migrations/20261127000000_corpus.sql into the SQL editor:

    python backend/scripts/verify_supabase_schema.py [--env path/to/.env]

Reads SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY from the environment
or the repo-root .env (key values are never printed). Talks to PostgREST only. Writes and
then deletes a test document with id "test:verify-schema". Prints PASS/FAIL per check and
exits 1 on any failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

TEST_ID = "test:verify-schema"
DIMS = 1536
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    env.update({k: v for k, v in os.environ.items() if k.startswith("SUPABASE_")})
    return env


def describe(r: httpx.Response) -> str:
    """Status plus PostgREST's error code/message; response bodies never contain keys."""
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if isinstance(body, dict) and ("code" in body or "message" in body):
        return f"HTTP {r.status_code} {body.get('code')}: {body.get('message')}"
    return f"HTTP {r.status_code}"


class Checker:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        self.failed += not ok
        return ok


def client(base: str, key: str) -> httpx.Client:
    # New sb_publishable_/sb_secret_ keys are not JWTs: send them in `apikey` only and let
    # the Supabase gateway mint the role JWT. (A Bearer token is accepted only if it equals
    # apikey, so omitting it is the safe choice.)
    return httpx.Client(
        base_url=f"{base.rstrip('/')}/rest/v1",
        headers={"apikey": key, "Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    )


def emb(seed: int, dims: int = DIMS) -> list[float]:
    return [round(((i * 31 + seed * 17) % 97) / 97 - 0.5, 4) for i in range(dims)]


def chunk(ord_: int, dims: int = DIMS) -> dict:
    return {
        "ord": ord_,
        "section": "Verify schema › Test",
        "content": f"Schema verification chunk {ord_}. Safe to delete.",
        "content_hash": f"verify-{ord_}",
        "token_count": 8,
        "embedding": emb(ord_, dims),
    }


DOC = {
    "id": TEST_ID,
    "source": "test",
    "trust": "low",
    "title": "Schema verification document",
    "url": "https://example.invalid/verify-schema",
    "year": 2026,
    "pub_types": ["test"],
    "cancers": ["colorectal"],
}


def chunk_ords(sb: httpx.Client) -> list[int] | None:
    r = sb.get("/chunks", params={"doc_id": f"eq.{TEST_ID}", "select": "ord", "order": "ord"})
    return [row["ord"] for row in r.json()] if r.status_code == 200 else None


def denied(r: httpx.Response) -> bool:
    # 404 counts only when PostgREST knows the function but hides it; PGRST202 means
    # "function not found", i.e. the migration hasn't run.
    if r.status_code in (401, 403):
        return True
    if r.status_code == 404:
        try:
            return r.json().get("code") != "PGRST202"
        except ValueError:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", type=Path, default=REPO_ROOT / ".env", help="path to .env")
    args = ap.parse_args()

    env = load_env(args.env)
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY")
               if not env.get(k)]
    if missing:
        print(f"FAIL  config  (missing {', '.join(missing)} in env or {args.env})")
        return 1

    c = Checker()
    try:
        with client(env["SUPABASE_URL"], env["SUPABASE_PUBLISHABLE_KEY"]) as anon, \
             client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"]) as sb:
            # (a) publishable key sees nothing and can't call the RPCs.
            for table in ("documents", "chunks"):
                r = anon.get(f"/{table}", params={"select": "*", "limit": "1"})
                c.check(f"publishable: select {table} returns []",
                        r.status_code == 200 and r.json() == [], describe(r))
            r = anon.post("/rpc/ingest_document", json={"doc": DOC, "chunks": [chunk(0)]})
            c.check("publishable: ingest_document denied", denied(r), describe(r))
            r = anon.post("/rpc/corpus_stats", json={})
            c.check("publishable: corpus_stats denied", denied(r), describe(r))

            # (b) replace semantics: 2 chunks, then 1 chunk -> exactly 1 left.
            r = sb.post("/rpc/ingest_document", json={"doc": DOC, "chunks": [chunk(0), chunk(1)]})
            c.check("secret: ingest_document with 2 chunks returns 2",
                    r.status_code == 200 and r.json() == 2, describe(r))
            r = sb.post("/rpc/ingest_document", json={"doc": DOC, "chunks": [chunk(0)]})
            c.check("secret: re-ingest with 1 chunk returns 1",
                    r.status_code == 200 and r.json() == 1, describe(r))
            ords = chunk_ords(sb)
            c.check("secret: exactly 1 chunk left for the test doc", ords == [0], f"ords={ords}")

            # (c) wrong embedding length is rejected, and the old chunk survives (one transaction).
            bad = chunk(5, dims=3)
            r = sb.post("/rpc/ingest_document", json={"doc": DOC, "chunks": [chunk(0), bad]})
            # pgvector: "expected 1536 dimensions, not 3" (SQLSTATE 22000 -> HTTP 400).
            c.check("secret: 3-dim embedding rejected",
                    r.status_code == 400 and "dimension" in r.text.lower(), describe(r))
            ords = chunk_ords(sb)
            c.check("secret: failed ingest rolled back (still 1 chunk)", ords == [0], f"ords={ords}")

            # (d) stats.
            r = sb.post("/rpc/corpus_stats", json={})
            ok = r.status_code == 200 and isinstance(r.json(), dict) and "rows" in r.json()
            detail = describe(r)
            if ok:
                s = r.json()
                detail = f"rows={s['rows']}, total={s.get('total_pretty')}"
            c.check("secret: corpus_stats returns", ok, detail)

            # (e) clean up; chunks go with the document (on delete cascade).
            r = sb.delete("/documents", params={"id": f"eq.{TEST_ID}"})
            c.check("secret: delete test doc", r.status_code in (200, 204), describe(r))
            ords = chunk_ords(sb)
            c.check("secret: chunks cascaded away", ords == [], f"ords={ords}")
    except httpx.HTTPError as e:
        # Only the exception type and URL host; no headers.
        print(f"FAIL  network  ({type(e).__name__}: is the project paused or SUPABASE_URL wrong?)")
        return 1

    print(f"\n{'ALL PASS' if not c.failed else f'{c.failed} FAILED'}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
