-- Cancer evidence corpus (docs/PRD-corpus-ingest.md, issue #42).
--
-- Run once in the Supabase SQL editor (see supabase/README.md). Safe to run again:
-- every statement is "if not exists", "create or replace" or naturally idempotent.
--
-- Access model: row-level security is on for every table and there are NO policies,
-- so the publishable key (anon/authenticated roles) sees no rows through the REST API.
-- Only the secret key (service_role, which bypasses RLS) can read or write, and only
-- service_role may execute the two RPCs.

-- pgvector. Supabase keeps extensions in the "extensions" schema; if vector was already
-- enabled elsewhere (e.g. public) this is a no-op and the search_path below still finds it.
create schema if not exists extensions;
create extension if not exists vector with schema extensions;

set search_path = public, extensions;

-- One row per source document, with the URL used for citations.
create table if not exists public.documents (
  id          text primary key,
  source      text not null,
  trust       text not null check (trust in ('high', 'medium', 'low')),
  title       text,
  url         text not null,
  pmid        text,
  journal     text,
  year        int,
  pub_types   text[] not null default '{}',
  cancers     text[] not null default '{}',
  ingested_at timestamptz not null default now()
);

-- Chunks of a document. cancers/source/trust are copied from the document so search can
-- filter without a join. The embedding length is enforced by the column type.
create table if not exists public.chunks (
  id           bigint generated always as identity primary key,
  doc_id       text not null references public.documents (id) on delete cascade,
  ord          int not null,
  section      text not null default '',
  content      text not null,
  content_hash text not null,
  token_count  int,
  cancers      text[] not null default '{}',
  source       text not null,
  trust        text not null check (trust in ('high', 'medium', 'low')),
  embedding    vector(1536) not null,
  fts          tsvector generated always as (
                 to_tsvector('english', coalesce(section, '') || ' ' || coalesce(content, ''))
               ) stored,
  unique (doc_id, ord)
);

-- Model and chunker locks (embedding_model, dims, chunker_config_hash).
create table if not exists public.corpus_meta (
  key   text primary key,
  value jsonb not null
);

create index if not exists chunks_embedding_hnsw on public.chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_fts_gin on public.chunks using gin (fts);
create index if not exists chunks_cancers_gin on public.chunks using gin (cancers);

alter table public.documents enable row level security;
alter table public.chunks enable row level security;
alter table public.corpus_meta enable row level security;

-- Upsert one document and replace all of its chunks. A function call is a single
-- transaction, so a failure (e.g. a wrong-length embedding) leaves the old chunks in place.
-- doc:    {id, source, trust, title, url, pmid, journal, year, pub_types[], cancers[]}
-- chunks: [{ord?, section, content, content_hash, token_count, embedding: [1536 numbers]}]
-- Returns the number of chunks written.
create or replace function public.ingest_document(doc jsonb, chunks jsonb)
returns int
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  d public.documents;
  n int;
begin
  if doc ->> 'id' is null then
    raise exception 'ingest_document: doc.id is required';
  end if;
  if jsonb_typeof(ingest_document.chunks) is distinct from 'array' then
    raise exception 'ingest_document: chunks must be a JSON array';
  end if;

  insert into public.documents as t
    (id, source, trust, title, url, pmid, journal, year, pub_types, cancers, ingested_at)
  values (
    doc ->> 'id',
    doc ->> 'source',
    doc ->> 'trust',
    doc ->> 'title',
    doc ->> 'url',
    doc ->> 'pmid',
    doc ->> 'journal',
    (doc ->> 'year')::int,
    case when jsonb_typeof(doc -> 'pub_types') = 'array'
         then array(select jsonb_array_elements_text(doc -> 'pub_types')) else '{}' end,
    case when jsonb_typeof(doc -> 'cancers') = 'array'
         then array(select jsonb_array_elements_text(doc -> 'cancers')) else '{}' end,
    now()
  )
  on conflict (id) do update set
    source      = excluded.source,
    trust       = excluded.trust,
    title       = excluded.title,
    url         = excluded.url,
    pmid        = excluded.pmid,
    journal     = excluded.journal,
    year        = excluded.year,
    pub_types   = excluded.pub_types,
    cancers     = excluded.cancers,
    ingested_at = excluded.ingested_at
  returning * into d;

  delete from public.chunks where doc_id = d.id;

  -- jsonb ->> of a number array yields "[0.1, 0.2, ...]", which is pgvector's text format.
  insert into public.chunks
    (doc_id, ord, section, content, content_hash, token_count, cancers, source, trust, embedding)
  select
    d.id,
    coalesce((e.c ->> 'ord')::int, (e.i - 1)::int),
    coalesce(e.c ->> 'section', ''),
    e.c ->> 'content',
    e.c ->> 'content_hash',
    (e.c ->> 'token_count')::int,
    d.cancers,
    d.source,
    d.trust,
    (e.c ->> 'embedding')::vector
  from jsonb_array_elements(ingest_document.chunks) with ordinality as e(c, i);

  get diagnostics n = row_count;
  return n;
end;
$$;

-- Row counts and on-disk sizes (bytes) for each corpus table and index.
create or replace function public.corpus_stats()
returns jsonb
language sql
stable
security definer
set search_path = public, extensions
as $$
  with t(name) as (values ('documents'), ('chunks'), ('corpus_meta'))
  select jsonb_build_object(
    'rows', jsonb_build_object(
      'documents',   (select count(*) from public.documents),
      'chunks',      (select count(*) from public.chunks),
      'corpus_meta', (select count(*) from public.corpus_meta)
    ),
    'tables', (
      select jsonb_object_agg(name, pg_total_relation_size(format('public.%I', name)::regclass))
      from t
    ),
    'indexes', (
      select coalesce(jsonb_object_agg(
               indexname, pg_total_relation_size(format('public.%I', indexname)::regclass)), '{}')
      from pg_indexes
      where schemaname = 'public' and tablename in (select name from t)
    ),
    'total_bytes', (
      select sum(pg_total_relation_size(format('public.%I', name)::regclass))::bigint from t
    ),
    'total_pretty', (
      select pg_size_pretty(sum(pg_total_relation_size(format('public.%I', name)::regclass))) from t
    )
  );
$$;

-- Functions are executable by PUBLIC by default, and Supabase also grants anon and
-- authenticated on new functions in public. Lock both RPCs to service_role.
revoke execute on function public.ingest_document(jsonb, jsonb) from public, anon, authenticated;
revoke execute on function public.corpus_stats() from public, anon, authenticated;
grant execute on function public.ingest_document(jsonb, jsonb) to service_role;
grant execute on function public.corpus_stats() to service_role;

-- Ask PostgREST to pick up the new tables and functions now.
notify pgrst, 'reload schema';
