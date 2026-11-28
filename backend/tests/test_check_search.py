"""Offline tests for the evidence search (app/check/search.py) against httpx.MockTransport fakes of OpenAI and
PostgREST. No network."""

import json
import logging

import httpx
import pytest

from app.check.search import (
    Evidence,
    EvidenceSearch,
    SearchError,
    SearchRateLimited,
    SearchUnavailable,
    SearchUpstreamError,
)
from app.config import Settings
from app.corpus.embed import EMBED_DIMS

OPENAI_KEY = "sk-openai-test-key-do-not-leak"
SB_KEY = "sb_secret_test-key-do-not-leak"
SB_URL = "https://example.supabase.co"
CLAIM = "Colon cancers are tested for mismatch repair proteins."
VECTOR = [0.25] + [0.0] * (EMBED_DIMS - 1)

ROW = {
    "chunk_id": 42,
    "doc_id": "nice-ng151",
    "section": "1.2 Mismatch repair",
    "content": "Offer testing for Lynch syndrome to all people with colorectal cancer.",
    "trust": "high",
    "source": "nice",
    "title": "Colorectal cancer",
    "url": "https://www.nice.org.uk/guidance/ng151",
    "year": 2020,
    "vector_rank": 1,
    "text_rank": 3,
    "rrf_score": 0.0322,
    "similarity": 0.61,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def openai_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [{"object": "embedding", "index": i, "embedding": VECTOR} for i in range(len(body["input"]))],
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        },
    )


def supabase_rows(*rows):
    return lambda request: httpx.Response(200, json=list(rows))


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def make(openai=openai_ok, supabase=supabase_rows(ROW)):
    oa, sb = Recorder(openai), Recorder(supabase)
    search = EvidenceSearch.from_settings(
        settings(),
        openai_transport=httpx.MockTransport(oa),
        supabase_transport=httpx.MockTransport(sb),
        sleep=lambda s: None,
    )
    return search, oa, sb


def settings(**overrides) -> Settings:
    values = dict(
        OPEN_AI_VECTOR_KEY=OPENAI_KEY, supabase_url=SB_URL, supabase_secret_key=SB_KEY, _env_file=None
    )
    values.update(overrides)
    return Settings(**values)


# --- happy path ----------------------------------------------------------


@pytest.mark.anyio
async def test_search_embeds_the_claim_and_calls_match_chunks():
    search, oa, sb = make()
    with search:
        results = await search.search(CLAIM)

    assert len(oa.requests) == 1
    req = oa.requests[0]
    assert req.url.path == "/v1/embeddings"
    assert req.headers["authorization"] == f"Bearer {OPENAI_KEY}"
    assert json.loads(req.content)["input"] == [CLAIM]

    assert len(sb.requests) == 1
    req = sb.requests[0]
    assert str(req.url) == f"{SB_URL}/rest/v1/rpc/match_chunks"
    assert req.method == "POST"
    assert req.headers["apikey"] == SB_KEY
    assert "authorization" not in req.headers  # the new secret key is not a JWT
    body = json.loads(req.content)
    # Only the embedding and the text: k, per_doc, min_similarity come from the SQL defaults.
    assert set(body) == {"query_embedding", "query_text"}
    assert body["query_text"] == CLAIM
    assert isinstance(body["query_embedding"], str)
    assert body["query_embedding"].startswith("[0.25,0.0,")
    assert json.loads(body["query_embedding"]) == VECTOR

    assert results == [Evidence(**ROW)]


@pytest.mark.anyio
async def test_overrides_are_sent():
    search, _, sb = make()
    with search:
        await search.search(CLAIM, k=3, per_doc=1, min_similarity=0.5)
    body = json.loads(sb.requests[0].content)
    assert body["k"] == 3 and body["per_doc"] == 1 and body["min_similarity"] == 0.5


@pytest.mark.anyio
async def test_parses_every_field_and_null_ranks_and_year():
    keyword_only = {**ROW, "chunk_id": 7, "vector_rank": None, "year": None, "section": "", "title": None}
    vector_only = {**ROW, "chunk_id": 8, "text_rank": None}
    search, _, _ = make(supabase=supabase_rows(ROW, keyword_only, vector_only))
    with search:
        results = await search.search(CLAIM)

    assert [r.chunk_id for r in results] == [42, 7, 8]
    first = results[0]
    for field, value in ROW.items():
        assert getattr(first, field) == value
    assert results[1].vector_rank is None and results[1].year is None and results[1].text_rank == 3
    assert results[2].text_rank is None and results[2].vector_rank == 1
    with pytest.raises(AttributeError):
        first.url = "x"  # frozen


@pytest.mark.anyio
async def test_no_results_is_an_empty_list():
    search, _, _ = make(supabase=supabase_rows())
    with search:
        assert await search.search(CLAIM) == []


# --- input validation ----------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["", "   ", "\n\t"])
async def test_empty_claim_raises_value_error_without_calls(claim):
    search, oa, sb = make()
    with search, pytest.raises(ValueError):
        await search.search(claim)
    assert oa.requests == [] and sb.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [{"k": 0}, {"per_doc": 0}, {"min_similarity": 1.5}])
async def test_bad_overrides_raise_value_error_without_calls(kwargs):
    search, oa, sb = make()
    with search, pytest.raises(ValueError):
        await search.search(CLAIM, **kwargs)
    assert oa.requests == [] and sb.requests == []


# --- from_settings -------------------------------------------------------


@pytest.mark.parametrize(
    "missing, name",
    [
        ({"OPEN_AI_VECTOR_KEY": None}, "OPEN_AI_VECTOR_KEY"),
        ({"OPEN_AI_VECTOR_KEY": "  "}, "OPEN_AI_VECTOR_KEY"),
        ({"supabase_url": None}, "SUPABASE_URL"),
        ({"supabase_secret_key": None}, "SUPABASE_SECRET_KEY"),
        ({"supabase_secret_key": ""}, "SUPABASE_SECRET_KEY"),
    ],
)
def test_from_settings_names_the_missing_variable(missing, name):
    with pytest.raises(SearchUnavailable) as e:
        EvidenceSearch.from_settings(settings(**missing))
    msg = str(e.value)
    assert name in msg
    assert OPENAI_KEY not in msg and SB_KEY not in msg


def test_repr_has_no_keys():
    search, _, _ = make()
    with search:
        assert OPENAI_KEY not in repr(search) and SB_KEY not in repr(search)


# --- error mapping -------------------------------------------------------


def status(code, *, json_body=None, html=False):
    def handler(request):
        if html:
            return httpx.Response(code, headers={"content-type": "text/html"}, text=f"<html>{CLAIM}</html>")
        return httpx.Response(code, json=json_body if json_body is not None else {"message": CLAIM})

    return handler


def network_error(request):
    raise httpx.ConnectError(f"boom {CLAIM}", request=request)


def timeout(request):
    raise httpx.ReadTimeout("slow", request=request)


CASES = [
    # (openai handler, supabase handler, expected error)
    (status(401), supabase_rows(ROW), SearchUnavailable),
    (status(403), supabase_rows(ROW), SearchUnavailable),
    (status(429), supabase_rows(ROW), SearchRateLimited),
    (status(500), supabase_rows(ROW), SearchUpstreamError),
    (status(400), supabase_rows(ROW), SearchUpstreamError),
    (network_error, supabase_rows(ROW), SearchUpstreamError),
    (timeout, supabase_rows(ROW), SearchUpstreamError),
    (lambda r: httpx.Response(200, json={"data": "nope"}), supabase_rows(ROW), SearchUpstreamError),
    (openai_ok, status(401), SearchUnavailable),
    (openai_ok, status(403), SearchUnavailable),
    (openai_ok, status(429), SearchRateLimited),
    (openai_ok, status(503), SearchUpstreamError),
    (openai_ok, status(500), SearchUpstreamError),
    (openai_ok, status(500, html=True), SearchUpstreamError),
    (openai_ok, status(200, html=True), SearchUpstreamError),
    (openai_ok, status(400, json_body={"code": "22P02", "message": CLAIM}), SearchUpstreamError),
    (openai_ok, status(404, json_body={"code": "PGRST202", "message": "no function"}), SearchUpstreamError),
    (openai_ok, network_error, SearchUpstreamError),
    (openai_ok, timeout, SearchUpstreamError),
    (openai_ok, lambda r: httpx.Response(200, text="not json"), SearchUpstreamError),
]


@pytest.mark.anyio
@pytest.mark.parametrize("openai, supabase, expected", CASES)
async def test_error_mapping_never_leaks_claim_or_keys(openai, supabase, expected, caplog):
    caplog.set_level(logging.DEBUG)
    search, _, _ = make(openai=openai, supabase=supabase)
    with search, pytest.raises(expected) as e:
        await search.search(CLAIM)
    assert isinstance(e.value, SearchError)
    text = str(e.value) + repr(e.value)
    cause = e.value.__cause__ or e.value.__context__
    if cause is not None and not e.value.__suppress_context__:
        text += str(cause) + repr(cause)
    for secret in (CLAIM, OPENAI_KEY, SB_KEY, "mismatch repair"):
        assert secret not in text
        assert secret not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "rows",
    [
        {"not": "a list"},
        [{"chunk_id": 1}],  # fields missing
        [{**ROW, "url": 5}],  # wrong type
        [{**ROW, "chunk_id": "42"}],
        [{**ROW, "similarity": "high"}],
        [{**ROW, "vector_rank": True}],
        ["row"],
    ],
)
async def test_malformed_rows_are_upstream_errors(rows):
    search, _, _ = make(supabase=lambda r: httpx.Response(200, json=rows))
    with search, pytest.raises(SearchUpstreamError):
        await search.search(CLAIM)


@pytest.mark.anyio
async def test_supabase_429_is_retried_then_succeeds():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(429, json={}) if calls["n"] == 1 else httpx.Response(200, json=[ROW])

    search, _, sb = make(supabase=flaky)
    with search:
        assert await search.search(CLAIM) == [Evidence(**ROW)]
    assert len(sb.requests) == 2


@pytest.mark.anyio
async def test_async_context_manager_closes():
    search, _, _ = make()
    async with search:
        assert len(await search.search(CLAIM)) == 1


# --- cold start: a JSON 5xx from PostgREST (statement timeout) is retried once on match_chunks -------------------

COLD = {"code": "57014", "message": "canceling statement due to statement timeout"}


@pytest.mark.anyio
async def test_match_chunks_json_500_is_retried_once_then_succeeds():
    calls = {"n": 0}

    def cold(request):
        calls["n"] += 1
        return httpx.Response(500, json=COLD) if calls["n"] == 1 else httpx.Response(200, json=[ROW])

    search, oa, sb = make(supabase=cold)
    with search:
        assert await search.search(CLAIM) == [Evidence(**ROW)]
    assert len(sb.requests) == 2
    assert len(oa.requests) == 1  # the embedding isn't redone


@pytest.mark.anyio
async def test_match_chunks_json_500_twice_is_an_upstream_error_after_one_retry():
    search, _, sb = make(supabase=lambda r: httpx.Response(500, json=COLD))
    with search, pytest.raises(SearchUpstreamError):
        await search.search(CLAIM)
    assert len(sb.requests) == 2


def test_other_rpcs_do_not_retry_a_json_500():
    from app.corpus.supabase import SupabaseCorpus, SupabaseRequestError

    rec = Recorder(lambda r: httpx.Response(500, json=COLD))
    with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(rec), sleep=lambda s: None) as store:
        with pytest.raises(SupabaseRequestError):
            store.corpus_stats()
    assert len(rec.requests) == 1


# --- key terms and the cancer filter (issue #55) ------------------------------------------------------------------


@pytest.mark.anyio
async def test_keyword_text_replaces_the_claim_as_query_text_and_the_claim_is_still_embedded():
    search, oa, sb = make()
    with search:
        await search.search(CLAIM, keyword_text="mismatch repair Lynch syndrome", cancer="colorectal")
    assert json.loads(oa.requests[0].content)["input"] == [CLAIM]
    body = json.loads(sb.requests[0].content)
    assert set(body) == {"query_embedding", "query_text", "cancer"}
    assert body["query_text"] == "mismatch repair Lynch syndrome"
    assert body["cancer"] == "colorectal"


@pytest.mark.anyio
@pytest.mark.parametrize("keyword_text", [None, "", "   "])
async def test_no_keyword_text_searches_the_claim_and_no_cancer_is_not_sent(keyword_text):
    search, _, sb = make()
    with search:
        await search.search(CLAIM, keyword_text=keyword_text, cancer=None)
    body = json.loads(sb.requests[0].content)
    assert body["query_text"] == CLAIM
    assert "cancer" not in body


@pytest.mark.anyio
@pytest.mark.parametrize("cancer", ["", "  ", 5])
async def test_bad_cancer_raises_value_error_without_calls(cancer):
    search, oa, sb = make()
    with search, pytest.raises(ValueError):
        await search.search(CLAIM, cancer=cancer)
    assert oa.requests == [] and sb.requests == []


@pytest.mark.anyio
async def test_cancer_filter_keeps_the_cold_start_retry():
    calls = {"n": 0}

    def cold(request):
        calls["n"] += 1
        return httpx.Response(500, json=COLD) if calls["n"] == 1 else httpx.Response(200, json=[ROW])

    search, _, sb = make(supabase=cold)
    with search:
        assert await search.search(CLAIM, keyword_text="Lynch", cancer="colorectal") == [Evidence(**ROW)]
    assert len(sb.requests) == 2
    assert all(json.loads(r.content)["cancer"] == "colorectal" for r in sb.requests)


def test_store_match_chunks_sends_cancer_only_when_set():
    from app.corpus.supabase import SupabaseCorpus

    rec = Recorder(supabase_rows())
    with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(rec), sleep=lambda s: None) as store:
        store.match_chunks(VECTOR, "Breslow", cancer="melanoma")
        store.match_chunks(VECTOR, "Breslow")
        store.match_chunks(VECTOR, "Breslow", cancer=None)
    bodies = [json.loads(r.content) for r in rec.requests]
    assert bodies[0]["cancer"] == "melanoma" and bodies[0]["query_text"] == "Breslow"
    assert "cancer" not in bodies[1] and "cancer" not in bodies[2]


# --- local BM25 keyword index + match_chunks_fused (issue #56) ----------------------------------------------------


class FakeIndex:
    def __init__(self, hits=((7, 3.2), (42, 2.5), (3, 1.0))):
        self.hits = list(hits)
        self.calls = []

    def search(self, terms, k=40, cancer=None):
        self.calls.append((terms, k, cancer))
        return self.hits[:k]


class FakeProvider:
    def __init__(self, index=None):
        self.index = index
        self.stores = []
        self.disabled = []

    def get(self, store):
        self.stores.append(store)
        return None if self.disabled else self.index

    def disable(self, reason):
        self.disabled.append(reason)


def make_bm25(provider, supabase=supabase_rows(ROW)):
    oa, sb = Recorder(openai_ok), Recorder(supabase)
    search = EvidenceSearch.from_settings(
        settings(),
        openai_transport=httpx.MockTransport(oa),
        supabase_transport=httpx.MockTransport(sb),
        sleep=lambda s: None,
        keyword_index=provider,
    )
    return search, oa, sb


@pytest.mark.anyio
async def test_bm25_ids_go_to_match_chunks_fused_in_rank_order():
    index = FakeIndex()
    search, oa, sb = make_bm25(FakeProvider(index))
    with search:
        results = await search.search(CLAIM, keyword_text="mismatch repair Lynch", cancer="colorectal")
    assert results == [Evidence(**ROW)]
    assert index.calls == [("mismatch repair Lynch", 40, "colorectal")]
    assert json.loads(oa.requests[0].content)["input"] == [CLAIM]
    [req] = sb.requests
    assert str(req.url) == f"{SB_URL}/rest/v1/rpc/match_chunks_fused"
    assert req.headers["apikey"] == SB_KEY
    body = json.loads(req.content)
    assert set(body) == {"query_embedding", "keyword_ids"}
    assert body["keyword_ids"] == [7, 42, 3]
    assert isinstance(body["query_embedding"], str) and body["query_embedding"].startswith("[0.25,0.0,")


@pytest.mark.anyio
@pytest.mark.parametrize("keyword_text", [None, "  "])
async def test_bm25_searches_the_claim_without_key_terms(keyword_text):
    index = FakeIndex()
    search, _, _ = make_bm25(FakeProvider(index))
    with search:
        await search.search(CLAIM, keyword_text=keyword_text)
    assert index.calls == [(CLAIM, 40, None)]


@pytest.mark.anyio
async def test_bm25_no_hits_sends_an_empty_id_list_and_overrides():
    search, _, sb = make_bm25(FakeProvider(FakeIndex(hits=())))
    with search:
        await search.search(CLAIM, 5, per_doc=1, min_similarity=0.4)
    body = json.loads(sb.requests[0].content)
    assert body["keyword_ids"] == []
    assert (body["k"], body["per_doc"], body["min_similarity"]) == (5, 1, 0.4)
    assert "cancer" not in body and "query_text" not in body


@pytest.mark.anyio
async def test_no_usable_index_falls_back_to_match_chunks():
    provider = FakeProvider(None)
    search, _, sb = make_bm25(provider)
    with search:
        await search.search(CLAIM, keyword_text="Lynch", cancer="colorectal")
    assert len(provider.stores) == 1
    [req] = sb.requests
    assert req.url.path.endswith("/rpc/match_chunks")
    assert json.loads(req.content)["query_text"] == "Lynch"


@pytest.mark.anyio
async def test_missing_fused_rpc_disables_bm25_and_falls_back_in_the_same_call(caplog):
    def handler(request):
        if request.url.path.endswith("/match_chunks_fused"):
            return httpx.Response(404, json={"code": "PGRST202", "message": "Could not find the function"})
        return httpx.Response(200, json=[ROW])

    provider = FakeProvider(FakeIndex())
    search, _, sb = make_bm25(provider, supabase=handler)
    with search, caplog.at_level(logging.WARNING, logger="pathcite"):
        assert await search.search(CLAIM, keyword_text="Lynch") == [Evidence(**ROW)]
    assert provider.disabled == ["rpc_missing"]
    assert [r.url.path.rsplit("/", 1)[1] for r in sb.requests] == ["match_chunks_fused", "match_chunks"]
    assert CLAIM not in caplog.text and "Lynch" not in caplog.text


@pytest.mark.anyio
async def test_other_fused_rpc_errors_are_upstream_errors():
    search, _, _ = make_bm25(
        FakeProvider(FakeIndex()), supabase=lambda r: httpx.Response(400, json={"code": "22P02", "message": "bad"})
    )
    with search, pytest.raises(SearchUpstreamError):
        await search.search(CLAIM)


@pytest.mark.anyio
async def test_real_provider_checks_the_fingerprint_once_per_process(tmp_path):
    from app.check.bm25 import Bm25Index, IndexRow, KeywordIndexProvider

    meta = {"chunker_config_hash": "c", "quality_filter_hash": "q"}
    Bm25Index.build([IndexRow(42, "MMR", "Lynch syndrome testing", ("colorectal",)), IndexRow(50, "", "other", ())],
                    meta).save(tmp_path / "bm25")  # fmt: skip
    seen = {"meta": 0, "count": 0}

    def handler(request):
        path = request.url.path.removeprefix("/rest/v1")
        if path == "/corpus_meta":
            seen["meta"] += 1
            return httpx.Response(200, json=[{"key": k, "value": v} for k, v in meta.items()])
        if path == "/chunks":
            seen["count"] += 1
            assert "count=exact" in request.headers["prefer"]
            return httpx.Response(200, json=[{"id": 50}], headers={"content-range": "0-0/2"})
        return httpx.Response(200, json=[ROW])

    provider = KeywordIndexProvider(tmp_path / "bm25")
    for _ in range(2):
        search, _, sb = make_bm25(provider, supabase=handler)
        with search:
            await search.search(CLAIM, keyword_text="Lynch syndrome", cancer="colorectal")
            await search.search(CLAIM, keyword_text="Lynch syndrome")
        fused = [json.loads(r.content) for r in sb.requests if r.url.path.endswith("match_chunks_fused")]
        assert [b["keyword_ids"] for b in fused] == [[42], [42]]
    assert provider.status == "ready"
    assert seen == {"meta": 1, "count": 1}


def test_store_match_chunks_fused_body_and_cold_start_retry():
    from app.corpus.supabase import SupabaseCorpus

    calls = {"n": 0}

    def cold(request):
        calls["n"] += 1
        return httpx.Response(500, json=COLD) if calls["n"] == 1 else httpx.Response(200, json=[])

    rec = Recorder(cold)
    with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(rec), sleep=lambda s: None) as store:
        assert store.match_chunks_fused(VECTOR, [9, 2, 5], k=8, per_doc=None) == []
    assert len(rec.requests) == 2
    body = json.loads(rec.requests[-1].content)
    assert body["keyword_ids"] == [9, 2, 5] and body["k"] == 8 and "per_doc" not in body
    assert body["query_embedding"] == json.dumps(VECTOR, separators=(",", ":"))


def test_store_count_and_max_id():
    from app.corpus.supabase import SupabaseCorpus

    def handler(request):
        assert request.url.params["order"] == "id.desc" and request.url.params["limit"] == "1"
        return httpx.Response(200, json=[{"id": 9876}], headers={"content-range": "0-0/9543"})

    with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(handler)) as store:
        assert store.chunk_count_and_max_id() == (9543, 9876)

    def empty(request):
        return httpx.Response(200, json=[], headers={"content-range": "*/0"})

    with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(empty)) as store:
        assert store.chunk_count_and_max_id() == (0, None)


def test_store_chunk_texts_pages_by_id():
    from app.corpus.supabase import SupabaseCorpus

    rows = [{"id": i, "section": "s", "content": f"c{i}", "cancers": []} for i in range(1, 8)]

    def handler(request):
        p = request.url.params
        assert p["select"] == "id,section,content,cancers" and p["order"] == "id.asc"
        after = int(p["id"].removeprefix("gt.")) if "id" in p else 0
        page = [r for r in rows if r["id"] > after][:2]  # a server cap lower than the page size
        return httpx.Response(200, json=page)

    with SupabaseCorpus(SB_URL, SB_KEY, transport=httpx.MockTransport(handler), page_size=3) as store:
        assert [r["id"] for r in store.chunk_texts()] == list(range(1, 8))


def test_default_search_factory_uses_the_shared_bm25_provider(tmp_path):
    from app.check import bm25
    from app.check.router import default_search_factory

    s = settings(bm25_index_dir=tmp_path / "bm25")
    with default_search_factory(s) as search:
        assert search._keywords is bm25.default_provider(tmp_path / "bm25")
