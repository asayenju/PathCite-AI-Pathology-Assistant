-- Vector search + fusion with keyword ranks from the backend's local BM25 index (issue #56, docs/PRD-rag-checker.md,
-- "v4 local BM25").
--
-- Run once in the Supabase SQL editor after 20261128000000_match_chunks_filter.sql. Safe to run again.
--
-- Why: on Supabase's free tier, sustained searches exhausted the disk IO budget and every query timed out (v3 search
-- eval: ~95 statement timeouts in 330 calls, p95 26 s). Postgres full-text search has to read and rank many rows per
-- query. The keyword search now runs in the backend (app/check/bm25.py, bm25s over every chunk's section + content,
-- English stemming, the cancer filter applied there), which sends the top keyword chunk ids, best first. Supabase is
-- left with one HNSW lookup and primary-key lookups of the fused ids per search.
--
-- match_chunks_fused(query_embedding, keyword_ids, k, candidates, per_doc, rrf_k, min_similarity):
-- - keyword list: keyword_ids as given; rank = position in the array (1-based; a repeated id keeps its first
--   position; ids past `candidates` are ignored);
-- - vector list: the top `candidates` by cosine distance (HNSW; order + limit in the inner query), unfiltered;
-- - full outer join, reciprocal rank fusion (1 / (rrf_k + rank) per list), the min_similarity 0.35 cosine floor
--   computed for every fused id (keyword-only ids too), at most per_doc chunks per document, top k.
-- Returns the same columns as match_chunks (text_rank = keyword position). Ids no longer in chunks are dropped.
-- match_chunks stays as the fallback (no index, or a stale one). Only service_role may execute it.

set search_path = public, extensions;

drop function if exists public.match_chunks_fused(vector, bigint[], int, int, int, int, float8);

create or replace function public.match_chunks_fused(
  query_embedding vector(1536),
  keyword_ids     bigint[],
  k               int default 8,
  candidates      int default 40,
  per_doc         int default 2,
  rrf_k           int default 60,
  min_similarity  float8 default 0.35
)
returns table (
  chunk_id    bigint,
  doc_id      text,
  section     text,
  content     text,
  trust       text,
  source      text,
  title       text,
  url         text,
  year        int,
  vector_rank int,
  text_rank   int,
  rrf_score   float8,
  similarity  float8
)
language sql
stable
security definer
set search_path = public, extensions
as $$
  with vec as (
    -- Order + limit in the inner query so the HNSW index is used; rank afterwards.
    select s.id, s.dist, row_number() over (order by s.dist)::int as r
    from (
      select c.id, c.embedding <=> query_embedding as dist
      from public.chunks c
      order by c.embedding <=> query_embedding
      limit candidates
    ) s
  ),
  kw as (
    -- The backend's BM25 ranking: position in the array.
    select u.id, min(u.pos)::int as r
    from unnest(coalesce(keyword_ids, '{}'::bigint[])) with ordinality as u(id, pos)
    where u.pos <= candidates and u.id is not null
    group by u.id
  ),
  fused as (
    select
      coalesce(vec.id, kw.id) as id,
      vec.r as vector_rank,
      kw.r  as text_rank,
      coalesce(1.0 / (rrf_k + vec.r), 0) + coalesce(1.0 / (rrf_k + kw.r), 0) as score
    from vec full outer join kw on kw.id = vec.id
  ),
  ranked as (
    select
      f.*, c.doc_id,
      row_number() over (partition by c.doc_id order by f.score desc, f.id) as nth_in_doc
    from fused f
    join public.chunks c on c.id = f.id
    where 1 - (c.embedding <=> query_embedding) >= min_similarity
  )
  select
    c.id, c.doc_id, c.section, c.content, c.trust, c.source, d.title, d.url, d.year,
    r.vector_rank, r.text_rank, r.score::float8,
    (1 - (c.embedding <=> query_embedding))::float8
  from ranked r
  join public.chunks c on c.id = r.id
  join public.documents d on d.id = c.doc_id
  where r.nth_in_doc <= per_doc
  order by r.score desc, c.id
  limit k;
$$;

revoke execute on function public.match_chunks_fused(vector, bigint[], int, int, int, int, float8) from public, anon, authenticated;
grant execute on function public.match_chunks_fused(vector, bigint[], int, int, int, int, float8) to service_role;

notify pgrst, 'reload schema';
