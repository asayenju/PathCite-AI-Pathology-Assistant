"""Offline tests for the search eval runner (backend/evals/search/run.py), with fake search, Luna and judge.
No network."""

import json

import pytest

from app.check.llm import LLMUpstreamError
from app.check.search import Evidence
from evals.search.run import (
    JUDGE_SCHEMA,
    VARIANTS,
    Cache,
    ChunkInfo,
    build_judge_input,
    cost_usd,
    evaluate,
    latency_stats,
    load_golden,
    render_markdown,
    run_eval,
    summarize,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def ev(cid, doc=None):
    return Evidence(
        chunk_id=cid,
        doc_id=doc or f"d{cid}",
        section=f"S{cid}",
        content=f"content {cid}",
        trust="high",
        source="nice",
        title=f"T{cid}",
        url="https://example.org",
        year=None,
        vector_rank=None,
        text_rank=None,
        rrf_score=0.0,
        similarity=0.5,
    )


CLAIM = "Breslow thickness is measured from the top of the granular layer."


def row(id_="mel-1", type_="supported", gold=(5,), claim=CLAIM, style="technical", cancer="melanoma"):
    return {
        "id": id_,
        "cancer": cancer,
        "type": type_,
        "claim": claim,
        "context": [],
        "needed": "How Breslow thickness is measured.",
        "gold_chunk_ids": list(gold),
        "style": style,
        "reviewed": False,
    }


class FakePlanner:
    """generate(): v2's queries; plan(): v3's queries, key terms and cancer (counted separately)."""

    def __init__(self, queries=("q-a", "q-b"), fail=False, key_terms=("Breslow", "granular layer"), cancer="melanoma",
                 plan_queries=("q-a", "q-c")):
        self.queries = list(queries)
        self.fail = fail
        self.calls = 0
        self.plan_calls = 0
        self.key_terms, self.cancer, self.plan_queries = list(key_terms), cancer, list(plan_queries)

    async def generate(self, claim, context):
        self.calls += 1
        if self.fail:
            raise LLMUpstreamError("boom")
        return list(self.queries)

    async def plan(self, claim, context):
        self.plan_calls += 1
        if self.fail:
            raise LLMUpstreamError("boom")
        return {"queries": list(self.plan_queries), "key_terms": list(self.key_terms), "cancer": self.cancer}


class FakeSearcher:
    """results[(mode, query)] -> chunk ids, best first; anything else returns []. mode is "terms" when key terms
    (keyword_text) are given; those calls are also recorded in `terms_calls` with the keyword text and cancer."""

    def __init__(self, results, fingerprint="fp1"):
        self.results = results
        self.calls = []
        self.terms_calls = []
        self.bm25_calls = []
        self.fetched = []
        self.fp = fingerprint

    async def search(self, query, *, vector_only, k, keyword_text=None, cancer=None, engine="sql"):
        mode = "vector" if vector_only else ("hybrid" if keyword_text is None else "terms")
        if engine == "bm25":
            mode = "bm25"
            self.bm25_calls.append((query, keyword_text, cancer))
        self.calls.append((mode, query, k))
        if keyword_text is not None and engine != "bm25":
            self.terms_calls.append((query, keyword_text, cancer))
        return [ev(c) for c in self.results.get((mode, query), [])[:k]]

    async def fetch_chunks(self, ids):
        self.fetched.append(sorted(ids))
        return [ChunkInfo(chunk_id=c, doc_id=f"d{c}", title=f"T{c}", section=f"S{c}", content=f"content {c}") for c in ids]

    async def fingerprint(self):
        return self.fp


class FakeJudge:
    """Grades every chunk 1 unless overridden; `drop` ids are left out of the answer; `extra` adds unknown ids."""

    def __init__(self, grades=None, drop=(), extra=()):
        self.grades = grades or {}
        self.drop = set(drop)
        self.extra = list(extra)
        self.calls = []

    async def grade(self, row, chunks):
        self.calls.append(sorted(c.chunk_id for c in chunks))
        out = [{"chunk_id": c.chunk_id, "grade": self.grades.get(c.chunk_id, 1)} for c in chunks if c.chunk_id not in self.drop]
        out += [{"chunk_id": x, "grade": 2} for x in self.extra]
        return {"grades": out}


def results_for(claim=CLAIM):
    return {
        ("hybrid", claim): list(range(1, 12)),  # 1..11
        ("hybrid", "q-a"): [20, 21, 1],
        ("hybrid", "q-b"): [21, 22],
        ("vector", claim): [30, 1, 2],
        ("vector", "q-a"): [40],
        ("vector", "q-b"): [41, 40],
    }


async def run(rows, tmp_path, *, planner=None, searcher=None, judge=None, variants=None, refresh=()):
    cache = Cache(tmp_path / "cache")
    cache.load()
    result = await evaluate(
        [r if not isinstance(r, dict) else load_row(r) for r in rows],
        planner=planner or FakePlanner(),
        searcher=searcher or FakeSearcher(results_for()),
        judge=judge or FakeJudge(),
        cache=cache,
        variants=variants or list(VARIANTS),
        refresh=set(refresh),
        concurrency=2,
    )
    cache.save()
    return result


def load_row(d):
    from evals.search.run import GoldenRow

    return GoldenRow.model_validate(d)


# --- golden file ---------------------------------------------------------------


def test_load_golden(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text(json.dumps(row()) + "\n\n" + json.dumps(row("mel-2", "not_covered", gold=())) + "\n")
    rows = load_golden(p)
    assert [r.id for r in rows] == ["mel-1", "mel-2"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "maybe"},
        {"style": "formal"},
        {"gold_chunk_ids": ["5"]},
        {"surprise": 1},
    ],
)
def test_load_golden_rejects_bad_rows(tmp_path, bad):
    p = tmp_path / "g.jsonl"
    p.write_text(json.dumps({**row(), **bad}) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        load_golden(p)


def test_load_golden_rejects_duplicate_ids(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text(json.dumps(row()) + "\n" + json.dumps(row()) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_golden(p)


# --- variants and pooling ------------------------------------------------------------


@pytest.mark.anyio
async def test_variants_and_pool_union(tmp_path):
    judge = FakeJudge()
    searcher = FakeSearcher(results_for())
    result = await run([row(gold=(5, 99))], tmp_path, judge=judge, searcher=searcher)
    [c] = result["claims"]
    v = c["variants"]
    assert c["queries"] == [CLAIM, "q-a", "q-b"]
    assert v["v1_hybrid_claim"]["ranked"] == list(range(1, 9))
    assert v["v1_hybrid_claim"]["pool"] == list(range(1, 11))
    assert v["vec_claim"]["ranked"] == [30, 1, 2]
    # v2: RRF over the claim, q-a and q-b lists; 21 (ranks 2, 1) and 1 (ranks 1, 3) appear twice and come first
    assert v["v2_hybrid_queries"]["ranked"][:2] == [21, 1]
    assert v["vec_queries"]["ranked"][:2] == [40, 30]
    # every search fetched 10 (pool depth)
    assert {k for _, _, k in searcher.calls} == {10}
    # pool: union of every variant's top 10 plus the gold chunks, judged in one call
    expected_pool = set(range(1, 11)) | {20, 21, 22, 30, 40, 41, 99}
    assert len(judge.calls) == 1
    assert set(judge.calls[0]) == expected_pool
    # gold chunk 99 was never retrieved, so it was fetched by id
    assert searcher.fetched == [[99]]


@pytest.mark.anyio
async def test_v2_ranking_matches_production_top_8(tmp_path):
    """The scored top 8 of a multi-query variant merges each query's top 8 (as /check does), not its top 10."""
    res = {
        ("hybrid", CLAIM): [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        ("hybrid", "q-a"): [11, 12, 13, 14, 15, 16, 17, 18, 9, 10],
        ("hybrid", "q-b"): [21, 22, 23, 24, 25, 26, 27, 28, 9, 10],
    }
    result = await run([row()], tmp_path, searcher=FakeSearcher(res), variants=["v2_hybrid_queries"])
    v2 = result["claims"][0]["variants"]["v2_hybrid_queries"]
    # 9 and 10 are at rank 9-10 in all three lists: with top-10 lists they would win RRF, in production they're absent
    assert 9 not in v2["ranked"] and 10 not in v2["ranked"]
    assert v2["pool"][:2] == [9, 10]


@pytest.mark.anyio
async def test_only_selected_variants_run(tmp_path):
    planner = FakePlanner()
    searcher = FakeSearcher(results_for())
    result = await run([row()], tmp_path, planner=planner, searcher=searcher, variants=["vec_claim"])
    assert planner.calls == 0
    assert searcher.calls == [("vector", CLAIM, 10)]
    assert list(result["claims"][0]["variants"]) == ["vec_claim"]


# --- grading ------------------------------------------------------------------


@pytest.mark.anyio
async def test_gold_is_forced_to_2_and_disagreement_recorded(tmp_path):
    judge = FakeJudge(grades={5: 0, 3: 2})
    result = await run([row(gold=(5,))], tmp_path, judge=judge)
    c = result["claims"][0]
    assert c["grades"]["5"] == 2
    assert c["luna_grades"]["5"] == 0
    assert c["grades"]["3"] == 2
    assert c["gold_disagreements"] == [{"chunk_id": 5, "luna_grade": 0}]


@pytest.mark.anyio
async def test_missing_grades_count_as_0_and_unknown_ids_are_ignored(tmp_path):
    judge = FakeJudge(drop={2, 3}, extra=[777])
    result = await run([row(gold=())], tmp_path, judge=judge)
    c = result["claims"][0]
    assert c["grades"]["2"] == 0 and c["grades"]["3"] == 0
    assert sorted(c["missing_grades"]) == [2, 3]
    assert c["unknown_ids"] == [777]
    assert "777" not in c["grades"]
    assert result["stats"]["missing_grades"] == 2


@pytest.mark.anyio
async def test_missing_grade_for_gold_counts_as_disagreement(tmp_path):
    result = await run([row(gold=(5,))], tmp_path, judge=FakeJudge(drop={5}))
    assert result["claims"][0]["gold_disagreements"] == [{"chunk_id": 5, "luna_grade": None}]
    assert result["claims"][0]["grades"]["5"] == 2


def test_judge_input_numbers_the_passages_with_title_and_section():
    from evals.search.run import GoldenRow

    text = build_judge_input(
        GoldenRow.model_validate(row()),
        [ChunkInfo(7, "d7", "Melanoma guideline", "Staging", "Breslow is measured ...")],
    )
    assert "CLAIM:" in text and CLAIM in text
    assert "NEEDED:" in text
    assert "[1] chunk_id=7" in text
    assert "Melanoma guideline" in text and "Staging" in text


def test_judge_schema_is_strict():
    item = JUDGE_SCHEMA["properties"]["grades"]["items"]
    assert item["properties"]["grade"]["enum"] == [0, 1, 2]
    assert item["additionalProperties"] is False
    assert JUDGE_SCHEMA["additionalProperties"] is False


# --- caching ------------------------------------------------------------------


@pytest.mark.anyio
async def test_rerun_reuses_every_cache(tmp_path):
    await run([row()], tmp_path)
    planner, searcher, judge = FakePlanner(), FakeSearcher(results_for()), FakeJudge()
    result = await run([row()], tmp_path, planner=planner, searcher=searcher, judge=judge)
    assert planner.calls == 0
    assert searcher.calls == []
    assert searcher.fetched == []
    assert judge.calls == []
    assert result["claims"][0]["variants"]["v1_hybrid_claim"]["ranked"] == list(range(1, 9))


@pytest.mark.anyio
async def test_refresh_redoes_everything(tmp_path):
    await run([row()], tmp_path)
    planner, searcher, judge = FakePlanner(), FakeSearcher(results_for()), FakeJudge()
    await run([row()], tmp_path, planner=planner, searcher=searcher, judge=judge, refresh={"queries", "search", "judge"})
    assert planner.calls == 1
    assert searcher.calls
    assert len(judge.calls) == 1


@pytest.mark.anyio
async def test_only_new_pool_chunks_are_judged_on_rerun(tmp_path):
    await run([row()], tmp_path, variants=["v1_hybrid_claim"])
    judge = FakeJudge()
    await run([row()], tmp_path, judge=judge, variants=["v1_hybrid_claim", "vec_claim"])
    assert judge.calls == [[30]]


@pytest.mark.anyio
async def test_edited_claim_invalidates_its_queries_and_judgements(tmp_path):
    await run([row()], tmp_path)
    planner, judge = FakePlanner(), FakeJudge()
    edited = row(claim="Breslow thickness is measured in millimetres.")
    res = {**results_for(), ("hybrid", edited["claim"]): [1], ("vector", edited["claim"]): [1]}
    await run([edited], tmp_path, planner=planner, judge=judge, searcher=FakeSearcher(res))
    assert planner.calls == 1
    assert len(judge.calls) == 1


@pytest.mark.anyio
async def test_corpus_change_drops_search_and_judgement_caches(tmp_path):
    await run([row()], tmp_path)
    planner, searcher, judge = FakePlanner(), FakeSearcher(results_for(), fingerprint="fp2"), FakeJudge()
    await run([row()], tmp_path, planner=planner, searcher=searcher, judge=judge)
    assert planner.calls == 0  # queries don't depend on the corpus
    assert searcher.calls
    assert len(judge.calls) == 1


@pytest.mark.anyio
async def test_failed_query_generation_falls_back_and_is_not_cached(tmp_path):
    r1 = await run([row()], tmp_path, planner=FakePlanner(fail=True))
    assert r1["claims"][0]["queries"] == [CLAIM]
    assert r1["claims"][0]["query_fallback"] is True
    planner = FakePlanner()
    r2 = await run([row()], tmp_path, planner=planner)
    assert planner.calls == 1
    assert r2["claims"][0]["queries"] == [CLAIM, "q-a", "q-b"]


@pytest.mark.anyio
async def test_a_failing_claim_is_recorded_not_fatal(tmp_path):
    class Broken(FakeSearcher):
        async def search(self, query, *, vector_only, k, **kw):
            if "broken" in query:
                raise RuntimeError("upstream")
            return await super().search(query, vector_only=vector_only, k=k, **kw)

    rows = [row(), row("mel-2", claim="broken claim")]
    result = await run(rows, tmp_path, searcher=Broken(results_for()))
    errors = {c["id"]: c.get("error") for c in result["claims"]}
    assert errors == {"mel-1": None, "mel-2": "RuntimeError"}


# --- summary and report --------------------------------------------------------------


@pytest.mark.anyio
async def test_report_has_the_tables(tmp_path):
    rows = [
        row(gold=(5,)),
        row("mel-2", "contradicted", gold=(2,), style="lay"),
        row("crc-1", "supported", gold=(3,), cancer="colorectal"),
        row("nc-1", "not_covered", gold=()),
    ]
    result = await run(rows, tmp_path, judge=FakeJudge(grades={5: 0}))
    summary = summarize(result)
    assert summary["variants"]["v1_hybrid_claim"]["all"]["n"] == 3
    md = render_markdown(result, summary)
    for needle in [
        "| Variant | @1 | @3 | @5 | @8 |",
        "Hit rate",
        "Recall",
        "Strict precision",
        "Lenient precision",
        "NDCG",
        "MRR",
        "By cancer",
        "By style",
        "By type",
        "Noise",
        "Worst misses",
        "Gold disagreements",
        "mel-1",
        "Cost",
        "Wall time",
        "v2_hybrid_queries",
        "v3_terms_cancer",
        "colorectal",
        "## Search latency",
        "| Variant | Calls | Mean (s) | p50 (s) | p95 (s) | Timeouts |",
    ]:
        assert needle in md, needle


@pytest.mark.anyio
async def test_run_eval_writes_json_and_md(tmp_path):
    golden = tmp_path / "g.jsonl"
    golden.write_text(json.dumps(row()) + "\n")
    json_path, md_path = await run_eval(
        golden=golden,
        limit=None,
        variants=list(VARIANTS),
        refresh=set(),
        out_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
        planner=FakePlanner(),
        searcher=FakeSearcher(results_for()),
        judge=FakeJudge(),
        usage=lambda: {"query_llm": (100, 50), "judge_llm": (1000, 200), "embedding_tokens": 30},
    )
    data = json.loads(json_path.read_text())
    assert data["claims"][0]["id"] == "mel-1"
    assert md_path.suffix == ".md" and "Hit rate" in md_path.read_text()
    assert (tmp_path / "cache" / "judgements.json").exists()


def test_cost():
    c = cost_usd({"query_llm": (1_000_000, 0), "judge_llm": (0, 1_000_000), "embedding_tokens": 1_000_000})
    assert c["total"] == pytest.approx(0.10 + 0.50 + 0.02)


# --- v3: key terms + cancer filter (issue #55) -----------------------------------------------------------------


def test_v3_variant_is_registered():
    assert "v3_terms_cancer" in VARIANTS


@pytest.mark.anyio
async def test_v3_searches_every_plan_query_with_the_key_terms_and_cancer(tmp_path):
    planner = FakePlanner()
    res = {("terms", CLAIM): [1, 2], ("terms", "q-a"): [3, 1], ("terms", "q-c"): [4]}
    searcher = FakeSearcher(res)
    result = await run([row()], tmp_path, planner=planner, searcher=searcher, variants=["v3_terms_cancer"])
    assert planner.calls == 0 and planner.plan_calls == 1
    assert sorted(searcher.terms_calls) == sorted(
        (q, "Breslow granular layer", "melanoma") for q in (CLAIM, "q-a", "q-c")
    )
    assert {m for m, _, _ in searcher.calls} == {"terms"}
    c = result["claims"][0]
    assert c["v3"] == {
        "queries": [CLAIM, "q-a", "q-c"],
        "key_terms": ["Breslow", "granular layer"],
        "cancer": "melanoma",
        "keyword_text": "Breslow granular layer",
        "fallback": False,
    }
    assert c["variants"]["v3_terms_cancer"]["ranked"][0] == 1  # in two lists


@pytest.mark.anyio
async def test_v3_plan_is_cached_apart_from_the_v2_queries(tmp_path):
    await run([row()], tmp_path, variants=["v2_hybrid_queries"])
    v2_entry = json.loads((tmp_path / "cache" / "queries.json").read_text())["mel-1"]

    planner = FakePlanner()
    await run([row()], tmp_path, planner=planner, variants=["v2_hybrid_queries", "v3_terms_cancer"])
    assert planner.calls == 0  # v2's cached queries are still valid
    assert planner.plan_calls == 1  # v3 needs the new fields, so it asks once
    cached = json.loads((tmp_path / "cache" / "queries.json").read_text())
    assert cached["mel-1"] == v2_entry
    v3 = [v for k, v in cached.items() if k != "mel-1"]
    assert len(v3) == 1 and v3[0]["key_terms"] == ["Breslow", "granular layer"] and v3[0]["cancer"] == "melanoma"

    planner = FakePlanner()
    await run([row()], tmp_path, planner=planner, variants=["v3_terms_cancer"])
    assert planner.plan_calls == 0

    planner = FakePlanner()
    await run([row()], tmp_path, planner=planner, variants=["v3_terms_cancer"], refresh={"queries"})
    assert planner.plan_calls == 1


@pytest.mark.anyio
async def test_v3_failed_plan_falls_back_to_the_claim_and_is_not_cached(tmp_path):
    searcher = FakeSearcher(results_for())
    r = await run([row()], tmp_path, planner=FakePlanner(fail=True), searcher=searcher, variants=["v3_terms_cancer"])
    c = r["claims"][0]
    assert c["v3"]["fallback"] is True and c["v3"]["queries"] == [CLAIM]
    assert c["v3"]["keyword_text"] == CLAIM and c["v3"]["cancer"] is None
    assert searcher.calls == [("hybrid", CLAIM, 10)]  # exactly the old hybrid claim search
    assert c["variants"]["v3_terms_cancer"]["ranked"] == list(range(1, 9))
    planner = FakePlanner()
    await run([row()], tmp_path, planner=planner, variants=["v3_terms_cancer"])
    assert planner.plan_calls == 1


@pytest.mark.anyio
async def test_v3_without_key_terms_uses_the_claim_and_unknown_cancer_no_filter(tmp_path):
    searcher = FakeSearcher({})
    planner = FakePlanner(key_terms=(), cancer=None)
    r = await run([row()], tmp_path, planner=planner, searcher=searcher, variants=["v3_terms_cancer"])
    assert r["claims"][0]["v3"]["keyword_text"] == CLAIM
    # the claim query is the plain hybrid search; the other queries keep the claim as keyword text
    assert ("hybrid", CLAIM, 10) in searcher.calls
    assert sorted(searcher.terms_calls) == [("q-a", CLAIM, None), ("q-c", CLAIM, None)]


# --- search latency ----------------------------------------------------------------------------------------------


def test_latency_stats():
    calls = [{"s": x, "timeout": False} for x in (0.1, 0.2, 0.3, 0.4, 1.0)] + [{"s": 8.0, "timeout": True}]
    st = latency_stats(calls)
    assert st["n"] == 6
    assert st["mean"] == pytest.approx(10.0 / 6)
    assert st["p50"] == pytest.approx(0.35)
    assert st["p95"] == pytest.approx(6.25)
    assert st["timeouts"] == 1
    assert latency_stats([]) == {"n": 0, "mean": None, "p50": None, "p95": None, "timeouts": 0}


@pytest.mark.anyio
async def test_latency_is_recorded_per_variant_for_searches_run_now(tmp_path):
    result = await run([row()], tmp_path)
    lat = result["latency"]
    assert set(lat) == set(VARIANTS)
    assert len(lat["v1_hybrid_claim"]) == 1
    assert len(lat["v2_hybrid_queries"]) == 3  # the claim (shared with v1) and two queries
    assert len(lat["v3_terms_cancer"]) == 3
    assert all(c["s"] >= 0 and c["timeout"] is False for calls in lat.values() for c in calls)
    summary = summarize(result)
    assert summary["latency"]["v2_hybrid_queries"]["n"] == 3
    # cached searches aren't timed
    again = await run([row()], tmp_path)
    assert all(calls == [] for calls in again["latency"].values())
    assert summarize(again)["latency"]["v1_hybrid_claim"]["n"] == 0
    assert "## Search latency" in render_markdown(again, summarize(again))


@pytest.mark.anyio
async def test_statement_timeouts_are_counted(tmp_path):
    class Slow(FakeSearcher):
        async def search(self, query, *, vector_only, k, **kw):
            if query == "q-a" and not vector_only:
                raise RuntimeError("POST /rpc/match_chunks failed: HTTP 500 57014: canceling statement")
            return await super().search(query, vector_only=vector_only, k=k, **kw)

    result = await run([row()], tmp_path, searcher=Slow(results_for()))
    assert result["claims"][0]["error"] == "RuntimeError"
    summary = summarize(result)
    assert summary["latency"]["v2_hybrid_queries"]["timeouts"] == 1
    assert summary["latency"]["v3_terms_cancer"]["timeouts"] == 1
    assert summary["latency"]["v1_hybrid_claim"]["timeouts"] == 0
    md = render_markdown(result, summary)
    line = next(ln for ln in md.splitlines() if ln.startswith("| v2_hybrid_queries |") and ln.count("|") == 7)
    assert line.rstrip().endswith("| 1 |")


# --- v4: local BM25 keyword ids + match_chunks_fused (issue #56) -------------------------------------------------


def test_v4_variant_is_registered():
    assert "v4_bm25" in VARIANTS


@pytest.mark.anyio
async def test_v4_runs_the_v3_plan_through_the_bm25_engine(tmp_path):
    planner = FakePlanner()
    res = {("bm25", CLAIM): [1, 2], ("bm25", "q-a"): [3, 1], ("bm25", "q-c"): [4]}
    searcher = FakeSearcher(res)
    result = await run([row()], tmp_path, planner=planner, searcher=searcher, variants=["v4_bm25"])
    assert planner.calls == 0 and planner.plan_calls == 1
    assert sorted(searcher.bm25_calls) == sorted(
        (q, "Breslow granular layer", "melanoma") for q in (CLAIM, "q-a", "q-c")
    )
    assert {m for m, _, _ in searcher.calls} == {"bm25"}
    c = result["claims"][0]
    assert c["v3"]["queries"] == [CLAIM, "q-a", "q-c"]
    assert c["variants"]["v4_bm25"]["ranked"][0] == 1


@pytest.mark.anyio
async def test_v4_reuses_the_cached_v3_plan_and_does_not_share_v3_searches(tmp_path):
    await run([row()], tmp_path, variants=["v3_terms_cancer"])
    planner = FakePlanner()
    searcher = FakeSearcher(results_for())
    await run([row()], tmp_path, planner=planner, searcher=searcher, variants=["v3_terms_cancer", "v4_bm25"])
    assert planner.plan_calls == 0
    assert {m for m, _, _ in searcher.calls} == {"bm25"}  # v3's are cached; v4's are new
    again = FakeSearcher(results_for())
    await run([row()], tmp_path, searcher=again, variants=["v4_bm25"])
    assert again.calls == []  # cached under their own key


@pytest.mark.anyio
async def test_v4_without_key_terms_searches_bm25_with_the_query(tmp_path):
    searcher = FakeSearcher({})
    planner = FakePlanner(key_terms=(), cancer=None)
    await run([row()], tmp_path, planner=planner, searcher=searcher, variants=["v4_bm25"])
    assert (CLAIM, None, None) in searcher.bm25_calls  # keyword text = the query itself
    assert ("q-a", CLAIM, None) in searcher.bm25_calls


@pytest.mark.anyio
async def test_v4_is_in_the_latency_table(tmp_path):
    result = await run([row()], tmp_path)
    assert len(result["latency"]["v4_bm25"]) == 3
    md = render_markdown(result, summarize(result))
    assert any(ln.startswith("| v4_bm25 |") and ln.count("|") == 7 for ln in md.splitlines())
