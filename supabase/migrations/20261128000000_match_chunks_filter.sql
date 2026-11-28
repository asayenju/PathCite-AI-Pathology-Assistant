-- Faster keyword search for the claim checker: an optional cancer filter (issue #55, docs/PRD-rag-checker.md, "v3").
--
-- Run once in the Supabase SQL editor after 20261127010000_match_chunks.sql. Safe to run again.
--
-- Why: the keyword half of match_chunks scored (ts_rank_cd) and sorted every chunk matching ANY word of the query.
-- With the whole claim as the query, common words ("cancer", "spread") matched thousands of the ~9,500 chunks, so on
-- the free tier one call took 1-9 s and hit the 8 s statement timeout (57014) under load. The backend now sends
-- 2-5 key terms (Luna's, e.g. "Breslow", "PSA nadir") as query_text, and, when Luna can tell which cancer the claim
-- is about, `cancer` ('colorectal', 'breast', ...). The filter `c.cancers @> array[cancer]` sits INSIDE the keyword
-- candidate query, next to `c.fts @@ q.tsq`, so Postgres can bitmap-AND the fts and cancers GIN indexes and only
-- ranks the rows of that cancer. The vector list stays unfiltered as a safety net (a wrong or missing cancer label
-- can't hide the nearest passages), and the min_similarity floor still applies to everything.
--
-- Otherwise identical to 20261127010000_match_chunks.sql: vector list (HNSW) + keyword list (lexemes OR-ed),
-- reciprocal rank fusion, the min_similarity 0.35 floor, at most per_doc chunks per document, top k.
-- cancer = null (the default) means no filter. Only service_role may execute it (the backend, with the secret key).

set search_path = public, extensions;

-- Drop both earlier signatures so the new one doesn't sit beside them as an overload (PostgREST would refuse to pick).
drop function if exists public.match_chunks(vector, text, int, int, int, int);
drop function if exists public.match_chunks(vector, text, int, int, int, int, float8);

create or replace function public.match_chunks(
  query_embedding vector(1536),
  query_text      text,
  k               int default 8,
  candidates      int default 40,
  per_doc         int default 2,
  rrf_k           int default 60,
  min_similarity  float8 default 0.35,
  cancer          text default null
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
  with q as (
    -- Lexemes of the query text, OR-ed. Casting text to tsquery keeps the already-stemmed lexemes as they are.
    select (
      select string_agg(quote_literal(l), ' | ')
      from unnest(tsvector_to_array(to_tsvector('english', coalesce(query_text, '')))) as l
    )::tsquery as tsq
  ),
  vec as (
    -- Order + limit in the inner query so the HNSW index is used; rank afterwards. Not filtered by cancer.
    select s.id, s.dist, row_number() over (order by s.dist)::int as r
    from (
      select c.id, c.embedding <=> query_embedding as dist
      from public.chunks c
      order by c.embedding <=> query_embedding
      limit candidates
    ) s
  ),
  kw as (
    select s.id, row_number() over (order by s.rank desc, s.id)::int as r
    from (
      select c.id, ts_rank_cd(c.fts, q.tsq, 1) as rank
      from public.chunks c, q
      where q.tsq is not null
        and c.fts @@ q.tsq
        and (cancer is null or c.cancers @> array[cancer])
      order by rank desc, c.id
      limit candidates
    ) s
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

revoke execute on function public.match_chunks(vector, text, int, int, int, int, float8, text) from public, anon, authenticated;
grant execute on function public.match_chunks(vector, text, int, int, int, int, float8, text) to service_role;

notify pgrst, 'reload schema';
