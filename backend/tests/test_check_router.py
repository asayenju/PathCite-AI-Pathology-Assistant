"""End-to-end tests for POST /check with fake search, Luna (OpenAI) and Jev clients. No network."""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.check.aggregate import JEV_DISAGREES, JEV_UNAVAILABLE, LOW_CONFIDENCE, SOURCES_DISAGREE
from app.check.llm import LLMAuthError, LLMRateLimited, LLMUpstreamError
from app.check.search import Evidence, SearchRateLimited, SearchUnavailable, SearchUpstreamError
from app.main import app
from app.triage.jev import JevAuthError, JevRateLimited, JevUpstreamError

CLAIM = "Zorblaxian carcinoma always spreads to the spleen."  # must never be logged
CONTEXT_TURN = "We found a Zorblaxian growth last week."


def ev(cid, content, **kw):
    values = dict(
        chunk_id=cid,
        doc_id=f"doc-{cid}",
        section=f"Section {cid}",
        content=content,
        trust="high",
        source="nice",
        title=f"Title {cid}",
        url=f"https://example.org/{cid}",
        year=2020,
        vector_rank=1,
        text_rank=None,
        rrf_score=0.03,
        similarity=0.6,
    )
    values.update(kw)
    return Evidence(**values)


CHUNKS = [
    ev(1, "Zorblaxian carcinoma spreads to the spleen in nearly all cases."),
    ev(2, "Zorblaxian carcinoma usually spreads to the spleen."),
    ev(3, "Anaplastic thyroid carcinoma is rare.", trust="low", title=None, year=None),
]


class FakeSearch:
    """Every query returns `results` unless `by_query` names it."""

    def __init__(self, results=CHUNKS, error=None, by_query=None):
        self.results, self.error, self.by_query = list(results), error, by_query or {}
        self.calls, self.kwargs, self.closed = [], [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def search(self, claim, k=None, **kw):
        self.calls.append((claim, k))
        self.kwargs.append(kw)
        if self.error:
            raise self.error
        return self.by_query.get(claim, self.results)


def luna_answer(stances, missing_qualifier=False, issue=None, suggestion=None):
    return {
        "stances": [{"chunk_id": c, "stance": s, "quote": q} for c, s, q in stances],
        "missing_qualifier": missing_qualifier,
        "issue": issue,
        "suggestion": suggestion,
    }


DEFAULT_LUNA = luna_answer(
    [
        (1, "supports", "spreads to the spleen in nearly all cases"),
        (2, "supports", "usually spreads to the spleen"),
        (3, "irrelevant", ""),
    ],
    missing_qualifier=True,
    issue="It does not always spread.",
    suggestion="It usually spreads to the spleen.",
)


QUERIES = ["zorblaxian carcinoma spleen metastasis", "zorblaxian carcinoma spread pattern"]
KEY_TERMS = ["zorblaxian carcinoma", "splenic metastasis"]


class FakeLLM:
    """Answers the query call (name search_queries) and the stance call (name source_stances).

    `error` / `query_error` fail the stance / query call; `order` (shared with FakeJev) records call order."""

    def __init__(
        self, answer=DEFAULT_LUNA, error=None, before=None, queries=QUERIES, query_error=None, order=None,
        key_terms=KEY_TERMS, cancer="unknown",
    ):
        self.answer, self.error, self.before = answer, error, before
        self.queries, self.query_error = queries, query_error
        self.key_terms, self.cancer = key_terms, cancer
        self.order = order if order is not None else []
        self.model = "gpt-6-luna"
        self.calls, self.query_calls, self.closed = [], [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def structured(self, *, instructions, input, schema, name):
        from app.check.llm import Usage

        if name == "search_queries":
            self.query_calls.append(input)
            self.order.append("queries")
            if self.query_error:
                raise self.query_error
            answer = {"queries": list(self.queries), "key_terms": list(self.key_terms), "cancer": self.cancer}
            return answer, Usage(5, 0, 3, 1)
        self.calls.append(input)
        self.order.append("luna")
        if self.before:
            await self.before()
        if self.error:
            raise self.error
        return self.answer, Usage(10, 0, 5, 2)


def jev_probs(label="supported", p=0.9):
    rest = (1 - p) / 2
    probs = {x: (p if x == label else rest) for x in ("supported", "contradicted", "insufficient")}
    return {"verdict": {"type": "choice", "probabilities": probs}}


class FakeJev:
    def __init__(self, answers=None, error=None, before=None, model_used="jev-1.14.2", order=None):
        self.answers = answers if answers is not None else jev_probs("supported", 0.9)
        self.error, self.before = error, before
        self.order = order if order is not None else []
        self.model_used = None
        self._model_used = model_used
        self.calls, self.closed = [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def ask(self, state, questions):
        self.calls.append((state, questions))
        self.order.append("jev")
        if self.before:
            await self.before()
        if self.error:
            raise self.error
        self.model_used = self._model_used
        return self.answers


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.pt"))
    with TestClient(app) as c:
        yield c


def configure(c, search=None, llm=None, jev=None, jev_key="jev-key", **settings):
    state = c.app.state
    update = {"jev_key": SecretStr(jev_key) if jev_key else None, **settings}
    state.settings = state.settings.model_copy(update=update)
    search = search or FakeSearch()
    llm = llm or FakeLLM()
    jev = jev or FakeJev()
    state.check_search_factory = lambda s: search
    state.check_llm_factory = lambda s: llm
    state.check_jev_factory = lambda s: jev
    return search, llm, jev


def post(c, quote=CLAIM, context=(CONTEXT_TURN,), turn_id="t0001"):
    return c.post("/check", json={"turn_id": turn_id, "quote": quote, "context": list(context)})


# --- happy path ----------------------------------------------------------------------------------------------------


def test_response_shape(client):
    search, llm, jev = configure(client)
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "status", "verdict", "issue", "suggestion", "needs_review", "review_reasons", "sources", "model", "jev_model",
        "search_queries", "confidence",
    }
    assert body["status"] == "checked"
    assert body["verdict"] == "incomplete"
    assert body["issue"] == "It does not always spread."
    assert body["suggestion"] == "It usually spreads to the spleen."
    assert body["needs_review"] is False
    assert body["review_reasons"] == []
    assert body["model"] == "gpt-6-luna"
    assert body["jev_model"] == "jev-1.14.2"
    assert body["search_queries"] == [CLAIM, *QUERIES]
    assert body["confidence"] == {
        "label": "supported", "p": pytest.approx(0.9), "p_verdict": pytest.approx(0.9), "agrees": True,
    }
    assert [s["chunk_id"] for s in body["sources"]] == [1, 2, 3]
    first = body["sources"][0]
    assert first == {
        "chunk_id": 1,
        "title": "Title 1",
        "section": "Section 1",
        "url": "https://example.org/1",
        "trust": "high",
        "year": 2020,
        "quote": "spreads to the spleen in nearly all cases",
        "stance": "supports",
        "luna_stance": "supports",
    }
    third = body["sources"][2]
    assert third["title"] is None and third["year"] is None and third["trust"] == "low"
    assert third["stance"] == "irrelevant" and third["quote"] == ""

    assert sorted(search.calls) == sorted([(CLAIM, 8), *[(q, 8) for q in QUERIES]])
    assert len(llm.query_calls) == 1 and CLAIM in llm.query_calls[0]
    assert len(llm.calls) == 1 and CLAIM in llm.calls[0] and CONTEXT_TURN in llm.calls[0]
    assert len(jev.calls) == 1 and jev.calls[0][0]["claim"] == CLAIM
    assert search.closed and llm.closed and jev.closed


def test_luna_queries_then_luna_judges_then_jev(client):
    order = []
    configure(client, llm=FakeLLM(order=order), jev=FakeJev(order=order))
    assert post(client).status_code == 200
    assert order == ["queries", "luna", "jev"]


def test_jev_never_sees_lunas_verdict(client):
    _, _, jev = configure(client)
    post(client)
    state, questions = jev.calls[0]
    assert set(state) == {"claim", "context", "passages"}
    assert set(questions) == {"verdict"}
    raw = str(state).lower() + str(questions).lower()
    for word in ("incomplete", "accurate", "luna", "does not always spread"):
        assert word not in raw


def test_results_from_several_queries_are_merged(client):
    extra = ev(9, "Zorblaxian carcinoma metastasises to the spleen.")
    search = FakeSearch(results=CHUNKS, by_query={QUERIES[0]: [extra]})
    luna = luna_answer(
        [(1, "supports", "nearly all cases"), (2, "irrelevant", ""), (3, "irrelevant", ""), (9, "supports", "to the spleen")]
    )
    configure(client, search=search, llm=FakeLLM(answer=luna))
    body = post(client).json()
    assert {s["chunk_id"] for s in body["sources"]} == {1, 2, 3, 9}
    assert body["sources"][0]["chunk_id"] == 1  # in two lists at rank 1


def test_query_generation_failure_falls_back_to_the_claim(client, caplog):
    caplog.set_level(logging.DEBUG)
    search, llm, _ = configure(client, llm=FakeLLM(query_error=LLMUpstreamError("x")))
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["search_queries"] == [CLAIM]
    assert search.calls == [(CLAIM, 8)]
    assert search.kwargs == [{"keyword_text": CLAIM}]  # the old behaviour: keywords of the claim, no filter
    assert body["verdict"] == "incomplete"
    assert "LLMUpstreamError" in caplog.text
    assert CLAIM not in caplog.text


def test_every_search_gets_the_key_terms_and_the_cancer_filter(client):
    search, _, _ = configure(client, llm=FakeLLM(cancer="thyroid"))
    body = post(client).json()
    assert search.kwargs == [{"keyword_text": "zorblaxian carcinoma splenic metastasis", "cancer": "thyroid"}] * 3
    assert body["search_queries"] == [CLAIM, *QUERIES]  # unchanged: key terms and cancer aren't in the response


def test_unknown_cancer_sends_no_filter_and_no_key_terms_searches_the_claim(client):
    search, _, _ = configure(client, llm=FakeLLM(key_terms=[]))
    post(client)
    assert search.kwargs == [{"keyword_text": CLAIM}] * 3


def test_top_k_comes_from_settings(client):
    search, _, _ = configure(client, check_top_k=3)
    post(client)
    assert {k for _, k in search.calls} == {3}


def test_at_most_top_k_sources(client):
    many = [ev(i, f"content {i}") for i in range(1, 7)]
    more = [ev(i, f"content {i}") for i in range(11, 17)]
    luna = luna_answer([(i, "irrelevant", "") for i in [*range(1, 7), *range(11, 17)]])
    search = FakeSearch(results=many, by_query={QUERIES[0]: more})
    configure(client, search=search, llm=FakeLLM(answer=luna), check_top_k=4)
    assert len(post(client).json()["sources"]) == 4


def test_only_last_two_context_turns_are_used(client):
    _, llm, jev = configure(client)
    post(client, context=["first turn", "second turn", "third turn"])
    assert "first turn" not in llm.calls[0]
    assert "first turn" not in llm.query_calls[0]
    assert jev.calls[0][0]["context"] == ["second turn", "third turn"]


def test_accurate_has_no_issue_or_suggestion(client):
    luna = luna_answer(
        [(1, "supports", "nearly all cases"), (2, "supports", "usually spreads"), (3, "irrelevant", "")],
        issue="should be dropped",
        suggestion="should be dropped",
    )
    configure(client, llm=FakeLLM(answer=luna))
    body = post(client).json()
    assert body["verdict"] == "accurate"
    assert body["issue"] is None and body["suggestion"] is None
    assert body["needs_review"] is False


def test_inaccurate_passes_issue_through(client):
    luna = luna_answer(
        [(1, "contradicts", "nearly all cases"), (2, "irrelevant", ""), (3, "irrelevant", "")],
        issue="Wrong.",
        suggestion="Fixed.",
    )
    configure(client, llm=FakeLLM(answer=luna), jev=FakeJev(answers=jev_probs("contradicted", 0.8)))
    body = post(client).json()
    assert body["verdict"] == "inaccurate"
    assert (body["issue"], body["suggestion"]) == ("Wrong.", "Fixed.")
    assert body["needs_review"] is False
    assert body["confidence"]["label"] == "contradicted"


def test_disputed_is_sources_disagree(client):
    luna = luna_answer(
        [(1, "supports", "nearly all cases"), (2, "contradicts", "usually spreads"), (3, "irrelevant", "")],
        issue="Mixed.",
        suggestion="Check.",
    )
    configure(client, llm=FakeLLM(answer=luna))
    body = post(client).json()
    assert body["verdict"] == "disputed"
    assert body["needs_review"] is True
    assert body["review_reasons"] == [SOURCES_DISAGREE]
    assert body["issue"] == "Mixed."
    assert body["confidence"]["p_verdict"] is None and body["confidence"]["agrees"] is None
    assert [s["stance"] for s in body["sources"]] == ["supports", "contradicts", "irrelevant"]


def test_jev_disagrees(client):
    configure(client, jev=FakeJev(answers=jev_probs("insufficient", 0.8)))
    body = post(client).json()
    assert body["verdict"] == "incomplete"
    assert body["needs_review"] is True
    assert body["review_reasons"] == [JEV_DISAGREES]
    assert body["confidence"]["agrees"] is False
    assert body["confidence"]["label"] == "insufficient"
    assert body["confidence"]["p_verdict"] == pytest.approx(0.1)


def test_low_confidence(client):
    configure(client, jev=FakeJev(answers=jev_probs("supported", 0.6)))
    body = post(client).json()
    assert body["review_reasons"] == [LOW_CONFIDENCE]
    assert body["confidence"]["agrees"] is True


def test_jev_min_comes_from_settings(client):
    configure(client, jev=FakeJev(answers=jev_probs("supported", 0.6)), check_jev_min=0.5)
    assert post(client).json()["needs_review"] is False


def test_ungrounded_quote_is_dropped_and_not_relevant(client):
    luna = luna_answer([(1, "supports", "made up words"), (2, "irrelevant", ""), (3, "irrelevant", "")])
    configure(client, llm=FakeLLM(answer=luna), jev=FakeJev(answers=jev_probs("insufficient", 0.9)))
    body = post(client).json()
    s = body["sources"][0]
    assert (s["stance"], s["luna_stance"], s["quote"]) == ("unclear", "unclear", "")
    assert body["verdict"] == "unverified"
    assert body["needs_review"] is False


def test_zero_results_is_unverified_without_judging_calls(client):
    search, llm, jev = configure(client, search=FakeSearch(results=[]))
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "unverified"
    assert body["needs_review"] is False
    assert body["review_reasons"] == []
    assert body["sources"] == []
    assert body["issue"] is None and body["suggestion"] is None
    assert body["search_queries"] == [CLAIM, *QUERIES]
    assert body["confidence"] is None
    assert llm.calls == [] and jev.calls == []
    assert body["jev_model"] is None


# --- Jev failure is not fatal ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [JevAuthError("x"), JevRateLimited("x"), JevUpstreamError("x")])
def test_jev_failure_still_returns_a_verdict(client, error, caplog):
    caplog.set_level(logging.DEBUG)
    configure(client, jev=FakeJev(error=error))
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "incomplete"
    assert body["issue"] == "It does not always spread."
    assert body["needs_review"] is True
    assert body["review_reasons"] == [JEV_UNAVAILABLE]
    assert body["jev_model"] is None
    assert body["confidence"] is None
    assert [s["stance"] for s in body["sources"]] == ["supports", "supports", "irrelevant"]
    assert type(error).__name__ in caplog.text
    assert CLAIM not in caplog.text


def test_malformed_jev_answer_still_returns_a_verdict(client):
    configure(client, jev=FakeJev(answers={"verdict": {"type": "noul"}}))
    body = post(client).json()
    assert body["review_reasons"] == [JEV_UNAVAILABLE]
    assert body["confidence"] is None


def test_missing_jev_key_is_not_fatal(client):
    _, _, jev = configure(client, jev_key=None)
    r = post(client)
    assert r.status_code == 200
    assert r.json()["review_reasons"] == [JEV_UNAVAILABLE]
    assert jev.calls == []


# --- errors ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where, error, status, code",
    [
        ("search", SearchUnavailable("x"), 503, "check_unavailable"),
        ("search", SearchRateLimited("x"), 429, "check_rate_limited"),
        ("search", SearchUpstreamError("x"), 502, "check_upstream_error"),
        ("llm", LLMAuthError("x"), 503, "check_unavailable"),
        ("llm", LLMRateLimited("x"), 429, "check_rate_limited"),
        ("llm", LLMUpstreamError("x"), 502, "check_upstream_error"),
        ("llm", RuntimeError(CLAIM), 502, "check_upstream_error"),
    ],
)
def test_error_codes(client, where, error, status, code, caplog):
    caplog.set_level(logging.DEBUG)
    if where == "search":
        configure(client, search=FakeSearch(error=error))
    else:
        configure(client, llm=FakeLLM(error=error))
    r = post(client)
    assert r.status_code == status
    assert r.json()["code"] == code
    assert set(r.json()) == {"detail", "code"}
    assert CLAIM not in caplog.text and CLAIM not in r.text


@pytest.mark.parametrize(
    "factory, error",
    [("check_search_factory", SearchUnavailable("no key")), ("check_llm_factory", LLMAuthError("no key"))],
)
def test_missing_keys_are_503(client, factory, error):
    configure(client)

    def boom(settings):
        raise error

    setattr(client.app.state, factory, boom)
    r = post(client)
    assert r.status_code == 503
    assert r.json()["code"] == "check_unavailable"


def test_default_factories_without_keys_are_503(client):
    state = client.app.state
    state.settings = state.settings.model_copy(
        update={"openai_key": None, "openai_vector_key": None, "supabase_url": None, "supabase_secret_key": None}
    )
    for name in ("check_search_factory", "check_llm_factory", "check_jev_factory"):
        if hasattr(state, name):
            delattr(state, name)
    r = post(client)
    assert r.status_code == 503
    assert r.json()["code"] == "check_unavailable"


@pytest.mark.parametrize(
    "body",
    [
        {"turn_id": "t1", "quote": "", "context": []},
        {"turn_id": "t1", "quote": "   \n ", "context": []},
        {"turn_id": "t1", "quote": "x" * 2001, "context": []},
        {"turn_id": "t1", "quote": "fine", "context": ["x" * 5001]},
        {"turn_id": "t1", "context": []},
        {"quote": "fine", "context": []},
        {"turn_id": "t1", "quote": 5},
        {"turn_id": "t1", "quote": "fine", "context": "not a list"},
    ],
)
def test_validation_is_invalid_check_request(client, body):
    _, llm, _ = configure(client)
    r = client.post("/check", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_check_request"
    assert llm.calls == []


def test_context_defaults_to_empty(client):
    configure(client)
    r = client.post("/check", json={"turn_id": "t1", "quote": CLAIM})
    assert r.status_code == 200


def test_validation_error_does_not_echo_input(client):
    configure(client)
    r = client.post("/check", json={"turn_id": "t1", "quote": CLAIM * 100})
    assert r.status_code == 422
    assert CLAIM not in r.text


def test_nothing_about_the_claim_is_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    configure(client)
    assert post(client).status_code == 200
    for secret in (CLAIM, CONTEXT_TURN, "spleen", "jev-key"):
        assert secret not in caplog.text
