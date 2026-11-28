# Supabase

Postgres + pgvector store for the cancer evidence corpus (`docs/PRD-corpus-ingest.md`).

## Running the migration

There's no Postgres connection string for this project, so migrations are run by hand:

1. Open the project in the Supabase Dashboard → **SQL Editor** → **New query**.
2. Paste the whole of `migrations/20261127000000_corpus.sql`.
3. Click **Run**. It should finish with "Success. No rows returned".

Then run the later migrations the same way, in file-name order, each once (all are safe to re-run):

- `migrations/20261127010000_match_chunks.sql`: the hybrid search RPC for the claim checker.
- `migrations/20261128000000_match_chunks_filter.sql` (issue #55): recreates `match_chunks` with an optional `cancer` filter on the keyword half, which the backend now sends with Luna's key terms. It drops the two earlier signatures first. Run it before using the new backend: until then `/check` searches fail (PostgREST finds no `match_chunks` with a `cancer` argument), unless the cancer is unknown.
- `migrations/20261128010000_match_chunks_bm25.sql` (issue #56): adds `match_chunks_fused(query_embedding, keyword_ids, ...)`: vector search (HNSW) fused with keyword ranks the backend computes with its local BM25 index (`keyword_ids`, best first), so Postgres no longer runs full-text search per claim (the free tier's disk IO ran out under sustained searches). `match_chunks` stays as the fallback. Until it is run, the backend notices the missing function (PostgREST `PGRST202`) on the first search, logs it once and keeps using `match_chunks`. Then build the index: `cd backend && python scripts/build_bm25_index.py` and restart the backend.

The corpus migration is safe to run again: every statement is `if not exists` / `create or replace` (or idempotent by nature, like enabling RLS and the grants), so a second run gives no errors and no duplicate objects.

pgvector is installed into the `extensions` schema, as Supabase recommends. If `vector` was already enabled in another schema, `create extension if not exists` does nothing and the functions still find the type through `search_path = public, extensions`.

## Check it

In the SQL editor:

```sql
select corpus_stats();
```

returns row counts and on-disk sizes (bytes) for `documents`, `chunks`, `corpus_meta` and each of their indexes, plus a total.

From the repo root, this checks access rules and `ingest_document` against the real project via the REST API (it writes and then deletes a test document, `test:verify-schema`):

```
backend/.venv/bin/python backend/scripts/verify_supabase_schema.py
```

## Keys and access

- Row-level security is on for all three tables and there are **no policies**. The publishable key (`SUPABASE_PUBLISHABLE_KEY`, roles `anon`/`authenticated`) therefore gets `[]` from every table, and can't execute `ingest_document` or `corpus_stats`. This keeps the licensed guideline text from being readable through Supabase's automatic REST API.
- Only the **secret key** (`SUPABASE_SECRET_KEY`, role `service_role`, which bypasses RLS) can read or write the corpus and call the RPCs. It is for backend code and scripts only: **never ship it to the frontend**, log it, or commit it. It lives in the repo-root `.env` (gitignored).
- New `sb_publishable_…` / `sb_secret_…` keys are sent in the `apikey` header only; they are not JWTs, so don't put them in `Authorization: Bearer`.
