"""End-to-end tests for POST /check with fake search, Luna (OpenAI) and Jev clients. No network."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.check.aggregate import JEV_UNAVAILABLE, MODELS_DISAGREE, SOURCES_DISAGREE
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
    def __init__(self, results=CHUNKS, error=None):
        self.results, self.error = list(results), error
        self.calls, self.closed = [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def search(self, claim, k=None, **kw):
        self.calls.append((claim, k))
        if self.error:
            raise self.error
        return self.results


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


class FakeLLM:
    def __init__(self, answer=DEFAULT_LUNA, error=None, before=None):
        self.answer, self.error, self.before = answer, error, before
        self.model = "gpt-6-luna"
        self.calls, self.closed = [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def structured(self, *, instructions, input, schema, name):
        self.calls.append(input)
        if self.before:
            await self.before()
        if self.error:
            raise self.error
        from app.check.llm import Usage

        return self.answer, Usage(10, 0, 5, 2)


def jev_probs(*labels, p=0.9):
    rest = (1 - p) / 2
    return {
        f"stance_{i}": {
            "type": "choice",
            "probabilities": {x: (p if x == label else rest) for x in ("supports", "contradicts", "irrelevant")},
        }
        for i, label in enumerate(labels)
    }


class FakeJev:
    def __init__(self, answers=None, error=None, before=None, model_used="jev-1.14.2"):
        self.answers = answers if answers is not None else jev_probs("supports", "supports", "irrelevant")
        self.error, self.before = error, before
        self.model_used = None
        self._model_used = model_used
        self.calls, self.closed = [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def ask(self, state, questions):
        self.calls.append((state, questions))
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
    }
    assert body["status"] == "checked"
    assert body["verdict"] == "incomplete"
    assert body["issue"] == "It does not always spread."
    assert body["suggestion"] == "It usually spreads to the spleen."
    assert body["needs_review"] is False
    assert body["review_reasons"] == []
    assert body["model"] == "gpt-6-luna"
    assert body["jev_model"] == "jev-1.14.2"
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
        "jev_stance": "supports",
        "jev_p": pytest.approx(0.9),
    }
    third = body["sources"][2]
    assert third["title"] is None and third["year"] is None and third["trust"] == "low"
    assert third["stance"] == "irrelevant" and third["quote"] == ""

    assert search.calls == [(CLAIM, 8)]
    assert len(llm.calls) == 1 and CLAIM in llm.calls[0] and CONTEXT_TURN in llm.calls[0]
    assert len(jev.calls) == 1 and jev.calls[0][0]["claim"] == CLAIM
    assert search.closed and llm.closed and jev.closed


def test_top_k_comes_from_settings(client):
    search, _, _ = configure(client, check_top_k=3)
    post(client)
    assert search.calls[0][1] == 3


def test_only_last_two_context_turns_are_used(client):
    _, llm, jev = configure(client)
    post(client, context=["first turn", "second turn", "third turn"])
    assert "first turn" not in llm.calls[0]
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


def test_inaccurate_passes_issue_through(client):
    luna = luna_answer(
        [(1, "contradicts", "nearly all cases"), (2, "irrelevant", ""), (3, "irrelevant", "")],
        issue="Wrong.",
        suggestion="Fixed.",
    )
    jev = FakeJev(answers=jev_probs("contradicts", "irrelevant", "irrelevant"))
    configure(client, llm=FakeLLM(answer=luna), jev=jev)
    body = post(client).json()
    assert body["verdict"] == "inaccurate"
    assert (body["issue"], body["suggestion"]) == ("Wrong.", "Fixed.")


def test_disputed_and_models_disagree(client):
    luna = luna_answer(
        [(1, "supports", "nearly all cases"), (2, "contradicts", "usually spreads"), (3, "supports", "is rare")],
        issue="Mixed.",
        suggestion="Check.",
    )
    jev = FakeJev(answers=jev_probs("supports", "contradicts", "irrelevant"))
    configure(client, llm=FakeLLM(answer=luna), jev=jev)
    body = post(client).json()
    assert body["verdict"] == "disputed"
    assert body["needs_review"] is True
    assert body["review_reasons"] == [SOURCES_DISAGREE, MODELS_DISAGREE]
    assert body["issue"] == "Mixed."
    third = body["sources"][2]
    assert (third["stance"], third["luna_stance"], third["jev_stance"]) == ("unclear", "supports", "irrelevant")
    assert third["quote"] == "is rare"  # grounded Luna quote is kept so the reviewer can see it


def test_ungrounded_quote_is_dropped_and_unclear(client):
    luna = luna_answer([(1, "supports", "made up words"), (2, "irrelevant", ""), (3, "irrelevant", "")])
    configure(client, llm=FakeLLM(answer=luna), jev=FakeJev(answers=jev_probs("supports", "irrelevant", "irrelevant")))
    body = post(client).json()
    s = body["sources"][0]
    assert (s["stance"], s["luna_stance"], s["quote"]) == ("unclear", "unclear", "")
    assert body["verdict"] == "unverified"
    assert body["review_reasons"] == [MODELS_DISAGREE]


def test_zero_results_is_unverified_without_model_calls(client):
    search, llm, jev = configure(client, search=FakeSearch(results=[]))
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "unverified"
    assert body["needs_review"] is False
    assert body["review_reasons"] == []
    assert body["sources"] == []
    assert body["issue"] is None and body["suggestion"] is None
    assert llm.calls == [] and jev.calls == []
    assert body["jev_model"] is None


# --- concurrency -----------------------------------------------------------------------------------------------------


def test_luna_and_jev_run_concurrently(client):
    """Each fake waits for an event only the other sets: sequential calls would deadlock (and time out)."""
    events = {}

    def barrier(mine, other):
        async def before():
            if not events:
                events.update(luna=asyncio.Event(), jev=asyncio.Event())
            events[mine].set()
            await asyncio.wait_for(events[other].wait(), timeout=2)

        return before

    configure(client, llm=FakeLLM(before=barrier("luna", "jev")), jev=FakeJev(before=barrier("jev", "luna")))
    r = post(client)
    assert r.status_code == 200, r.text
    assert r.json()["jev_model"] == "jev-1.14.2"


# --- Jev failure is not fatal ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [JevAuthError("x"), JevRateLimited("x"), JevUpstreamError("x")])
def test_jev_failure_still_returns_a_verdict(client, error, caplog):
    caplog.set_level(logging.DEBUG)
    configure(client, jev=FakeJev(error=error))
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "incomplete"
    assert body["needs_review"] is True
    assert body["review_reasons"] == [JEV_UNAVAILABLE]
    assert body["jev_model"] is None
    assert all(s["jev_stance"] is None and s["jev_p"] is None for s in body["sources"])
    assert [s["stance"] for s in body["sources"]] == ["supports", "supports", "irrelevant"]
    assert type(error).__name__ in caplog.text
    assert CLAIM not in caplog.text


def test_malformed_jev_answer_still_returns_a_verdict(client):
    configure(client, jev=FakeJev(answers={"stance_0": {"type": "noul"}}))
    body = post(client).json()
    assert body["review_reasons"] == [JEV_UNAVAILABLE]


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
