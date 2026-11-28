"""Offline checks on the match_chunks cancer-filter migration (issue #55). The SQL can't run in CI (no connection
string), so these parse the file text, like test_supabase_migration.py."""

import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261128000000_match_chunks_filter.sql"
)
NEW_SIG = r"vector, text, int, int, int, int, float8, text"


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


@pytest.fixture(scope="module")
def raw() -> str:
    assert MIGRATION.exists(), f"missing {MIGRATION}"
    return MIGRATION.read_text()


@pytest.fixture(scope="module")
def sql(raw) -> str:
    return re.sub(r"\s+", " ", _strip_comments(raw)).lower()


def _cte(sql: str, name: str, nxt: str) -> str:
    start = sql.index(f"{name} as (")
    return sql[start : sql.index(f"{nxt} as (", start)]


def test_header_comment_explains_why(raw):
    head = raw.lower()[:3000]
    assert raw.startswith("--")
    assert "issue #55" in head and "cancer" in head and "timeout" in head


@pytest.mark.parametrize(
    "sig", [r"vector, text, int, int, int, int", r"vector, text, int, int, int, int, float8"]
)
def test_drops_both_old_overloads(sql, sig):
    assert f"drop function if exists public.match_chunks({sig});" in sql


def test_no_bare_drop(sql):
    assert not re.search(r"\bdrop (table|index|function|policy|trigger) (?!if exists)", sql)


def test_every_create_is_idempotent(sql):
    for m in re.finditer(r"\bcreate ", sql):
        assert sql[m.end():].startswith("or replace"), sql[m.start():m.start() + 60]


def test_signature_adds_an_optional_cancer(sql):
    start = sql.index("create or replace function public.match_chunks(")
    params = sql[start : sql.index(") returns table", start)]
    for p in (
        "query_embedding vector(1536)",
        "query_text text",
        "k int default 8",
        "candidates int default 40",
        "per_doc int default 2",
        "rrf_k int default 60",
        "min_similarity float8 default 0.35",
        "cancer text default null",
    ):
        assert p in params, p
    assert params.index("min_similarity") < params.index("cancer text")  # appended last


def test_filter_is_inside_the_keyword_candidates_before_ranking(sql):
    kw = _cte(sql, "kw", "fused")
    inner = kw[kw.index("from public.chunks c") :]
    assert "(cancer is null or c.cancers @> array[cancer])" in inner
    assert inner.index("c.cancers @>") < inner.index("order by rank desc")
    assert "c.fts @@ q.tsq" in inner


def test_vector_list_is_unfiltered(sql):
    assert "cancers" not in _cte(sql, "vec", "kw")


def test_keeps_floor_per_doc_rrf_and_columns(sql):
    assert "1 - (c.embedding <=> query_embedding) >= min_similarity" in sql
    assert "r.nth_in_doc <= per_doc" in sql
    assert "1.0 / (rrf_k + vec.r)" in sql and "1.0 / (rrf_k + kw.r)" in sql
    for col in ("chunk_id bigint", "doc_id text", "vector_rank int", "text_rank int", "rrf_score float8", "similarity float8"):
        assert col in sql


def test_locked_down(sql):
    start = sql.index("create or replace function public.match_chunks(")
    header = sql[start : sql.index(" as $$", start)]
    assert "security definer" in header
    assert "set search_path = public, extensions" in header
    revoke = re.search(rf"revoke execute on function public\.match_chunks\({NEW_SIG}\) from ([^;]+);", sql)
    assert revoke
    assert {"public", "anon", "authenticated"} <= {r.strip() for r in revoke.group(1).split(",")}
    assert f"grant execute on function public.match_chunks({NEW_SIG}) to service_role;" in sql
    assert "notify pgrst, 'reload schema';" in sql

