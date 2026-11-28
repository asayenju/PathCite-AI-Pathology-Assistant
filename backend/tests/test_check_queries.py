"""Offline tests for Luna's search queries and the multi-query merge (app/check/queries.py). No network."""

import json
import logging

import httpx
import pytest

from app.check.llm import LLMAuthError, LLMRateLimited, LLMUpstreamError, OpenAIResponsesClient
from app.check.queries import (
    CANCERS,
    MAX_KEY_TERMS,
    MAX_QUERIES,
    QUERY_INSTRUCTIONS,
    QUERY_SCHEMA,
    SearchPlan,
    clean_key_terms,
    generate_plan,
    generate_queries,
    merge_results,
    multi_search,
    plan_queries,
    plan_search,
)
from app.check.search import Evidence, SearchRateLimited

KEY = "sk-openai-test-key-do-not-leak"
CLAIM = "Because it's HER2 negative, hormone-blocking pills won't work for you."
CONTEXT = ["Older turn.", "Your breast cancer is HER2 negative.", "Latest turn."]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def responses_body(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def client_for(payload=None, seen=None, status=200):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"echo": CLAIM})
        return httpx.Response(200, json=responses_body(payload))

    async def no_sleep(_):
        pass

    return OpenAIResponsesClient(
        KEY, model="gpt-6-luna", transport=httpx.MockTransport(handler), sleep=no_sleep, max_retries=0
    )


GOOD = {
    "queries": ["HER2 status and endocrine therapy response", "hormone receptor positive breast cancer endocrine therapy"],
    "key_terms": ["HER2", "endocrine therapy", "hormone receptor"],
    "cancer": "breast",
}


def answer(queries=(), key_terms=(), cancer="unknown"):
    return {"queries": list(queries), "key_terms": list(key_terms), "cancer": cancer}


# --- generate_queries ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_sends_structured_request_with_low_effort():
    seen = []
    async with client_for(GOOD, seen) as llm:
        queries, usage = await generate_queries(llm, CLAIM, CONTEXT)
    assert queries == GOOD["queries"]
    assert usage.input_tokens == 120
    [body] = seen
    assert body["instructions"] == QUERY_INSTRUCTIONS
    assert body["reasoning"] == {"effort": "low"}
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"] == QUERY_SCHEMA
    assert CLAIM in body["input"]
    assert "Your breast cancer is HER2 negative." in body["input"] and "Latest turn." in body["input"]
    assert "Older turn." not in body["input"]  # only the last 2 turns


def test_schema_is_a_strict_object_of_strings():
    assert QUERY_SCHEMA["required"] == ["queries", "key_terms", "cancer"]
    assert QUERY_SCHEMA["additionalProperties"] is False
    assert QUERY_SCHEMA["properties"]["queries"]["items"] == {"type": "string"}


def test_schema_has_key_terms_and_a_cancer_enum():
    props = QUERY_SCHEMA["properties"]
    assert props["key_terms"] == {"type": "array", "items": {"type": "string"}}
    assert props["cancer"] == {"type": "string", "enum": list(CANCERS)}
    assert CANCERS == ("colorectal", "breast", "melanoma", "lymphoma", "prostate", "thyroid", "unknown")


def test_instructions_ask_for_distinctive_key_terms_and_the_cancer():
    text = QUERY_INSTRUCTIONS
    assert "key_terms" in text and "2 to 5" in text
    for example in ("Breslow", "IPI", "PSA nadir", "Dukes C"):
        assert example in text
    for generic in ("cancer", "tumour", "patient", "treatment", "spread", "risk"):
        assert generic in text.split("NOT generic words", 1)[1][:200]
    assert '"unknown"' in text


def test_instructions_ask_for_general_fact_queries():
    text = QUERY_INSTRUCTIONS.lower()
    assert "general medical fact" in text
    assert "1 to 3" in text or "one to three" in text


@pytest.mark.anyio
async def test_generate_cleans_dedupes_and_caps_at_three():
    payload = answer(["  HER2   and endocrine therapy ", "her2 and endocrine therapy", "", "q2", "q3", "q4", "x" * 400])
    async with client_for(payload) as llm:
        queries, _ = await generate_queries(llm, CLAIM, [])
    assert queries == ["HER2 and endocrine therapy", "q2", "q3"]
    assert len(queries) <= MAX_QUERIES


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {**answer(), "queries": "not a list"},
        {**answer(), "queries": [1, 2]},
        {"other": []},
        {**answer(), "x": 1},
        {"queries": []},  # the new fields are required
        {**answer(), "key_terms": "Breslow"},
        {**answer(), "key_terms": [3]},
        {**answer(), "cancer": "lung"},
        {**answer(), "cancer": None},
    ],
)
async def test_generate_schema_violation_is_upstream_error(payload):
    async with client_for(payload) as llm:
        with pytest.raises(LLMUpstreamError):
            await generate_queries(llm, CLAIM, [])


# --- plan_queries ----------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plan_puts_the_raw_claim_first():
    async with client_for(GOOD) as llm:
        assert await plan_queries(llm, CLAIM, CONTEXT) == [CLAIM, *GOOD["queries"]]


@pytest.mark.anyio
async def test_plan_drops_a_query_equal_to_the_claim():
    async with client_for(answer([CLAIM.upper(), "q1"])) as llm:
        assert await plan_queries(llm, CLAIM, []) == [CLAIM, "q1"]


@pytest.mark.anyio
async def test_plan_with_no_generated_queries_is_the_claim_alone():
    async with client_for(answer()) as llm:
        assert await plan_queries(llm, CLAIM, []) == [CLAIM]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_plan_falls_back_to_the_claim_on_http_failure(status, caplog):
    caplog.set_level(logging.DEBUG)
    async with client_for(status=status) as llm:
        assert await plan_queries(llm, CLAIM, CONTEXT) == [CLAIM]
    assert caplog.text  # the failure is logged ...
    assert CLAIM not in caplog.text and KEY not in caplog.text  # ... by exception type only
    assert any(name in caplog.text for name in (LLMAuthError.__name__, LLMRateLimited.__name__, LLMUpstreamError.__name__))


@pytest.mark.anyio
async def test_plan_falls_back_on_a_malformed_answer(caplog):
    caplog.set_level(logging.DEBUG)
    async with client_for("not json") as llm:
        assert await plan_queries(llm, CLAIM, []) == [CLAIM]
    assert "LLMUpstreamError" in caplog.text
    assert CLAIM not in caplog.text


# --- key terms and cancer (issue #55) ----------------------------------------------------------------------------------


def test_clean_key_terms_folds_drops_and_caps():
    raw = ["  PSA   nadir ", "psa NADIR", "", "   ", "x" * 41, "Gleason", "ADT", "Dukes C", "IPI", "Breslow", "extra"]
    assert clean_key_terms(raw) == ["PSA nadir", "Gleason", "ADT", "Dukes C", "IPI"]
    assert len(clean_key_terms(raw)) == MAX_KEY_TERMS == 5


def test_clean_key_terms_keeps_a_40_char_term():
    assert clean_key_terms(["y" * 40]) == ["y" * 40]


def test_clean_key_terms_drops_lone_generic_words():
    assert clean_key_terms(["Cancer", "tumour", "risk", "Breslow", "cancer staging"]) == ["Breslow", "cancer staging"]


@pytest.mark.anyio
async def test_generate_plan_returns_queries_terms_and_cancer():
    async with client_for(GOOD) as llm:
        (queries, key_terms, cancer), usage = await generate_plan(llm, CLAIM, CONTEXT)
    assert queries == GOOD["queries"]
    assert key_terms == GOOD["key_terms"]
    assert cancer == "breast"
    assert usage.input_tokens == 120


@pytest.mark.anyio
async def test_generate_plan_unknown_cancer_is_none():
    async with client_for(answer(["q"], ["Breslow"], "unknown")) as llm:
        (_, _, cancer), _ = await generate_plan(llm, CLAIM, [])
    assert cancer is None


@pytest.mark.anyio
async def test_plan_search_uses_key_terms_as_keyword_text_and_the_cancer_filter():
    async with client_for(GOOD) as llm:
        plan = await plan_search(llm, CLAIM, CONTEXT)
    assert plan == SearchPlan(
        queries=[CLAIM, *GOOD["queries"]], keyword_text="HER2 endocrine therapy hormone receptor", cancer="breast"
    )


@pytest.mark.anyio
async def test_plan_search_without_key_terms_searches_keywords_of_the_claim():
    async with client_for(answer(["q1"], ["  ", "cancer"], "melanoma")) as llm:
        plan = await plan_search(llm, CLAIM, [])
    assert plan == SearchPlan(queries=[CLAIM, "q1"], keyword_text=CLAIM, cancer="melanoma")


@pytest.mark.anyio
async def test_plan_search_unknown_cancer_means_no_filter():
    async with client_for(answer(["q1"], ["Breslow"], "unknown")) as llm:
        plan = await plan_search(llm, CLAIM, [])
    assert plan.cancer is None and plan.keyword_text == "Breslow"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_plan_search_failure_falls_back_to_the_old_behaviour(status, caplog):
    caplog.set_level(logging.DEBUG)
    async with client_for(status=status) as llm:
        plan = await plan_search(llm, CLAIM, CONTEXT)
    assert plan == SearchPlan(queries=[CLAIM], keyword_text=CLAIM, cancer=None)
    assert CLAIM not in caplog.text and KEY not in caplog.text


@pytest.mark.anyio
async def test_plan_search_malformed_answer_falls_back():
    async with client_for({**answer(["q"], ["Breslow"]), "cancer": "lung"}) as llm:
        plan = await plan_search(llm, CLAIM, [])
    assert plan == SearchPlan(queries=[CLAIM], keyword_text=CLAIM, cancer=None)


# --- merge_results ---------------------------------------------------------------------------------------------------


def ev(cid, doc=None, **kw):
    values = dict(
        chunk_id=cid, doc_id=doc or f"doc-{cid}", section="s", content=f"content {cid}", trust="high", source="nice",
        title=f"T{cid}", url=f"https://example.org/{cid}", year=2020, vector_rank=1, text_rank=None, rrf_score=0.03,
        similarity=0.6,
    )
    values.update(kw)
    return Evidence(**values)


def ids(results):
    return [e.chunk_id for e in results]


def test_merge_single_list_keeps_its_order():
    assert ids(merge_results([[ev(1), ev(2), ev(3)]], k=8)) == [1, 2, 3]


def test_merge_sums_reciprocal_ranks_across_queries():
    # chunk 3 is 3rd and 1st: 1/63 + 1/61 beats chunk 1 (1/61 only) and chunk 2 (1/62 + nothing)
    q1 = [ev(1), ev(2), ev(3)]
    q2 = [ev(3), ev(4)]
    assert ids(merge_results([q1, q2], k=8)) == [3, 1, 2, 4]


def test_merge_ties_keep_first_appearance():
    # 1 and 5 are both rank 1 in one list: query order decides
    assert ids(merge_results([[ev(1)], [ev(5)]], k=8)) == [1, 5]


def test_merge_dedupes_by_chunk_id_and_keeps_the_first_object():
    first = ev(7, similarity=0.9)
    merged = merge_results([[first], [ev(7, similarity=0.4)]], k=8)
    assert merged == [first]


def test_merge_caps_two_chunks_per_document():
    q1 = [ev(1, "A"), ev(2, "A"), ev(3, "A"), ev(4, "B")]
    q2 = [ev(5, "A"), ev(6, "C")]
    merged = merge_results([q1, q2], k=8)
    # scores: 1 and 5 = 1/61, 2 and 6 = 1/62, 3 = 1/63, 4 = 1/64; doc A keeps only its best two (1, 5)
    assert ids(merged) == [1, 5, 6, 4]
    assert sum(e.doc_id == "A" for e in merged) == 2


def test_merge_keeps_top_k():
    q1 = [ev(i) for i in range(1, 11)]
    q2 = [ev(i) for i in range(11, 21)]
    merged = merge_results([q1, q2], k=8)
    assert len(merged) == 8
    assert ids(merged) == [1, 11, 2, 12, 3, 13, 4, 14]


def test_merge_of_nothing_is_empty():
    assert merge_results([[], []], k=8) == []


# --- multi_search ----------------------------------------------------------------------------------------------------


class FakeSearch:
    def __init__(self, by_query, error_for=None):
        self.by_query, self.error_for = by_query, error_for
        self.calls = []
        self.kwargs = []

    async def search(self, query, k=None, **kwargs):
        self.calls.append((query, k))
        self.kwargs.append(kwargs)
        if query == self.error_for:
            raise SearchRateLimited("x")
        return self.by_query.get(query, [])


@pytest.mark.anyio
async def test_multi_search_runs_every_query_and_merges():
    search = FakeSearch({"a": [ev(1), ev(2)], "b": [ev(2), ev(3)]})
    merged = await multi_search(search, ["a", "b"], k=8)
    assert sorted(search.calls) == [("a", 8), ("b", 8)]
    assert ids(merged) == [2, 1, 3]


@pytest.mark.anyio
async def test_multi_search_runs_concurrently():
    import asyncio

    started = []
    both = asyncio.Event()

    class Barrier:
        async def search(self, query, k=None):
            started.append(query)
            if len(started) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=2)
            return [ev(len(started))]

    assert len(await multi_search(Barrier(), ["a", "b"], k=8)) >= 1


@pytest.mark.anyio
async def test_multi_search_error_propagates():
    with pytest.raises(SearchRateLimited):
        await multi_search(FakeSearch({"a": [ev(1)]}, error_for="b"), ["a", "b"], k=8)


@pytest.mark.anyio
async def test_multi_search_sends_the_same_keyword_text_and_cancer_with_every_query():
    search = FakeSearch({"a": [ev(1)], "b": [ev(2)]})
    await multi_search(search, ["a", "b"], k=8, keyword_text="Breslow ulceration", cancer="melanoma")
    assert search.kwargs == [{"keyword_text": "Breslow ulceration", "cancer": "melanoma"}] * 2


@pytest.mark.anyio
async def test_multi_search_leaves_out_unset_options():
    search = FakeSearch({"a": [ev(1)]})
    await multi_search(search, ["a"], k=8, keyword_text="x", cancer=None)
    assert search.kwargs == [{"keyword_text": "x"}]
