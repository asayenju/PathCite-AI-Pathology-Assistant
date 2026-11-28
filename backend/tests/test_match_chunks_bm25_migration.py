"""Offline checks on the match_chunks_fused migration (issue #56: keyword ranks come from the backend's BM25
index). The SQL can't run in CI, so these parse the file text, like test_match_chunks_filter_migration.py."""

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "20261128010000_match_chunks_bm25.sql"
SIG = r"vector, bigint\[\], int, int, int, int, float8"
SIG_TEXT = "vector, bigint[], int, int, int, int, float8"


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
    assert "issue #56" in head and "bm25" in head and "disk io" in head


def test_is_the_latest_migration_and_older_files_still_exist():
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[-1] == MIGRATION.name
    assert "20261128000000_match_chunks_filter.sql" in names


def test_leaves_match_chunks_in_place(sql):
    assert "match_chunks(" not in sql.replace("match_chunks_fused(", "")


def test_no_bare_drop(sql):
    assert not re.search(r"\bdrop (table|index|function|policy|trigger) (?!if exists)", sql)


def test_every_create_is_idempotent(sql):
    for m in re.finditer(r"\bcreate ", sql):
        assert sql[m.end():].startswith("or replace"), sql[m.start():m.start() + 60]


def test_signature(sql):
    start = sql.index("create or replace function public.match_chunks_fused(")
    params = sql[start : sql.index(") returns table", start)]
    expected = [
        "query_embedding vector(1536)",
        "keyword_ids bigint[]",
        "k int default 8",
        "candidates int default 40",
        "per_doc int default 2",
        "rrf_k int default 60",
        "min_similarity float8 default 0.35",
    ]
    positions = [params.index(p) for p in expected]
    assert positions == sorted(positions)
    assert "query_text" not in params and "cancer" not in params


def test_keyword_rank_is_the_position_in_keyword_ids(sql):
    kw = _cte(sql, "kw", "fused")
    assert "unnest(" in kw and "with ordinality" in kw and "keyword_ids" in kw
    assert "public.chunks" not in kw  # no full-text work in Postgres any more
    assert "ts_rank" not in sql and "tsquery" not in sql and "fts" not in sql


def test_vector_list_orders_and_limits_inside(sql):
    vec = _cte(sql, "vec", "kw")
    inner = vec[vec.index("from public.chunks c") :]
    assert "order by c.embedding <=> query_embedding" in inner and "limit candidates" in inner


def test_keeps_rrf_floor_per_doc_and_columns(sql):
    assert "full outer join" in sql
    assert "1.0 / (rrf_k + vec.r)" in sql and "1.0 / (rrf_k + kw.r)" in sql
    ranked = _cte(sql, "ranked", "x") if "x as (" in sql else sql[sql.index("ranked as (") :]
    assert "1 - (c.embedding <=> query_embedding) >= min_similarity" in ranked
    assert "r.nth_in_doc <= per_doc" in sql
    assert "limit k" in sql
    returns = sql[sql.index("returns table") : sql.index(" as $$")]
    for col in ("chunk_id bigint", "doc_id text", "section text", "content text", "trust text", "source text",
                "title text", "url text", "year int", "vector_rank int", "text_rank int", "rrf_score float8",
                "similarity float8"):  # fmt: skip
        assert col in returns, col


def test_locked_down(sql):
    start = sql.index("create or replace function public.match_chunks_fused(")
    header = sql[start : sql.index(" as $$", start)]
    assert "security definer" in header and "stable" in header
    assert "set search_path = public, extensions" in header
    assert f"drop function if exists public.match_chunks_fused({SIG_TEXT});" in sql
    revoke = re.search(rf"revoke execute on function public\.match_chunks_fused\({SIG}\) from ([^;]+);", sql)
    assert revoke
    assert {"public", "anon", "authenticated"} <= {r.strip() for r in revoke.group(1).split(",")}
    assert f"grant execute on function public.match_chunks_fused({SIG_TEXT}) to service_role;" in sql
    assert "notify pgrst, 'reload schema';" in sql
