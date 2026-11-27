"""Offline checks on the Supabase corpus migration (docs/PRD-corpus-ingest.md, issue #42).

There is no Postgres connection string, so the SQL can't run in CI. These tests parse the
file text and pin the invariants that matter: idempotency, RLS with no policies, revoked
RPC grants and the 1536-dim vector column.
"""

import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261127000000_corpus.sql"
)
TABLES = ("documents", "chunks", "corpus_meta")
FUNCTIONS = ("ingest_document", "corpus_stats")
IDEMPOTENT_CREATES = (
    "or replace",
    "table if not exists",
    "extension if not exists",
    "index if not exists",
    "schema if not exists",
)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


@pytest.fixture(scope="module")
def sql() -> str:
    assert MIGRATION.exists(), f"missing {MIGRATION}"
    return re.sub(r"\s+", " ", _strip_comments(MIGRATION.read_text())).lower()


def test_vector_extension_is_idempotent(sql):
    assert re.search(r"create extension if not exists vector\b", sql)


@pytest.mark.parametrize("table", TABLES)
def test_tables_created_if_not_exists(sql, table):
    assert re.search(rf"create table if not exists public\.{table} \(", sql)


def test_every_create_is_idempotent(sql):
    creates = [m.start() for m in re.finditer(r"\bcreate ", sql)]
    assert creates
    for start in creates:
        rest = sql[start + len("create "):]
        assert rest.startswith(IDEMPOTENT_CREATES), f"non-idempotent: {sql[start:start + 60]!r}"


def test_no_bare_alter_add_or_drop_without_if_exists(sql):
    # Re-runs must not fail on objects that already exist or are already gone.
    assert not re.search(r"\badd (column|constraint) (?!if not exists)", sql)
    assert not re.search(r"\bdrop (table|index|function|policy|trigger) (?!if exists)", sql)


@pytest.mark.parametrize("table", TABLES)
def test_rls_enabled(sql, table):
    assert re.search(rf"alter table public\.{table} enable row level security", sql)


def test_no_policies(sql):
    assert "create policy" not in sql


def test_embedding_is_not_null_1536(sql):
    assert re.search(r"embedding (extensions\.)?vector\(1536\) not null", sql)


def test_chunks_constraints(sql):
    assert re.search(r"references public\.documents ?\(id\) on delete cascade", sql)
    assert re.search(r"unique ?\(doc_id, ord\)", sql)
    assert re.search(r"fts tsvector generated always as \( ?to_tsvector\('english'", sql)
    assert "check (trust in ('high', 'medium', 'low'))" in sql
    assert "url text not null" in sql


def test_indexes(sql):
    assert re.search(
        r"create index if not exists \w+ on public\.chunks using hnsw "
        r"\(embedding (extensions\.)?vector_cosine_ops\)",
        sql,
    )
    assert re.search(r"create index if not exists \w+ on public\.chunks using gin \(fts\)", sql)
    assert re.search(r"create index if not exists \w+ on public\.chunks using gin \(cancers\)", sql)


@pytest.mark.parametrize("fn", FUNCTIONS)
def test_functions_are_locked_down(sql, fn):
    start = sql.index(f"create or replace function public.{fn}(")
    header = sql[start:sql.index(" as $$", start)]
    assert "security definer" in header
    assert "set search_path = public, extensions" in header
    revoke = re.search(rf"revoke execute on function public\.{fn}\([^)]*\) from ([^;]+);", sql)
    assert revoke, f"no revoke for {fn}"
    roles = {r.strip() for r in revoke.group(1).split(",")}
    assert {"public", "anon", "authenticated"} <= roles
    assert re.search(rf"grant execute on function public\.{fn}\([^)]*\) to service_role", sql)


def test_ingest_document_signature_and_cast(sql):
    assert re.search(r"function public\.ingest_document\(doc jsonb, chunks jsonb\) returns int", sql)
    assert re.search(r"\((\w\.)?c ?->> ?'embedding'\)::(extensions\.)?vector\b", sql)
    assert re.search(r"delete from public\.chunks where doc_id", sql)
    assert "on conflict (id) do update" in sql


def test_corpus_stats_returns_sizes(sql):
    assert re.search(r"function public\.corpus_stats\(\) returns jsonb", sql)
    assert "pg_total_relation_size" in sql
    assert "pg_indexes" in sql
