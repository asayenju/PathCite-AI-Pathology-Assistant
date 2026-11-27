-- Hybrid search over the evidence corpus for the claim checker (docs/PRD-rag-checker.md).
--
-- Run once in the Supabase SQL editor after 20261127000000_corpus.sql. Safe to run again.
--
-- match_chunks(query_embedding, query_text, k):
--   1. vector list: the `candidates` nearest chunks by cosine distance (HNSW index);
--   2. keyword list: the `candidates` best full-text matches. The claim's words are OR-ed (any word may match,
--      chunks matching more of them rank higher), because a whole sentence AND-ed together rarely matches anything;
--   3. reciprocal rank fusion: score = 1/(rrf_k + vector rank) + 1/(rrf_k + keyword rank);
--   4. drop anything below `min_similarity` (cosine). Without it, the OR-ed keyword list brings in passages that
--      only share common words ("cell", "skin", "work"), e.g. T-cell lymphoma for a melanoma claim; tried on sample
--      claims, that junk scored 0.23-0.34 and relevant keyword-only hits 0.37+;
--   5. at most `per_doc` chunks from one document, then the top k.
-- Only service_role may execute it (the backend, with the secret key).

set search_path = public, extensions;

-- The first version had no min_similarity; drop it so the new signature doesn't sit beside it as an overload.
drop function if exists public.match_chunks(vector, text, int, int, int, int);

create or replace function public.match_chunks(
  query_embedding vector(1536),
  query_text      text,
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
  with q as (
    -- Lexemes of the claim, OR-ed. Casting text to tsquery keeps the already-stemmed lexemes as they are.
    select (
      select string_agg(quote_literal(l), ' | ')
      from unnest(tsvector_to_array(to_tsvector('english', coalesce(query_text, '')))) as l
    )::tsquery as tsq
  ),
  vec as (
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
    select s.id, row_number() over (order by s.rank desc, s.id)::int as r
    from (
      select c.id, ts_rank_cd(c.fts, q.tsq, 1) as rank
      from public.chunks c, q
      where q.tsq is not null and c.fts @@ q.tsq
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

revoke execute on function public.match_chunks(vector, text, int, int, int, int, float8) from public, anon, authenticated;
grant execute on function public.match_chunks(vector, text, int, int, int, int, float8) to service_role;

notify pgrst, 'reload schema';
