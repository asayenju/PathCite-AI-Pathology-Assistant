"""Offline tests for Jev's single confidence question (app/check/jev_check.py) and JevClient.ask, with MockTransport."""

import json
import logging

import httpx
import pytest

from app.check.jev_check import LABELS, VERDICT_CRITERIA, JevVerdict, jev_confidence
from app.check.verify import ChunkIn
from app.triage.jev import JevAuthError, JevClient, JevRateLimited, JevUpstreamError

KEY = "sk-jev-test-key-do-not-leak"
MODEL = "jev-1.14.0"
CLAIM = "Zorblaxian carcinoma always spreads to the spleen."
CONTEXT = ["Older turn one.", "Turn two.", "Turn three."]


CHUNKS = [
    ChunkIn(11, "Spread", "Zorblaxian carcinoma spreads to the spleen in most cases.", "Zorblax guideline"),
    ChunkIn(22, "Overview", "Zorblaxian carcinoma rarely spreads.", None),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def choice(probs):
    return {"type": "choice", "choice": "supported", "probabilities": probs}


def ok(probs, model="jev-1.14.2"):
    def handler(request, body):
        return httpx.Response(200, json={"model": model, "answers": {"verdict": choice(probs)}})

    return handler


DEFAULT = {"supported": 0.8, "contradicted": 0.05, "insufficient": 0.15}


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request):
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        return self.handler(request, body)


def client(handler):
    rec = Recorder(handler)
    return JevClient(KEY, model=MODEL, transport=httpx.MockTransport(rec), sleep=_nosleep), rec


async def _nosleep(_):
    return None


@pytest.mark.anyio
async def test_one_request_with_one_choice_question():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        out = await jev_confidence(jev, CLAIM, CONTEXT, CHUNKS)

    assert len(rec.requests) == 1
    assert rec.requests[0].url.path == "/v1/systemone"
    body = rec.bodies[0]
    assert body["model"] == MODEL
    assert set(body["questions"]) == {"verdict"}
    q = body["questions"]["verdict"]
    assert q["type"] == "choice"
    assert q["criteria"] == VERDICT_CRITERIA
    assert set(q["criteria"]) == {"supported", "contradicted", "insufficient"}
    state = body["state"]
    assert set(state) == {"claim", "context", "passages"}
    assert state["claim"] == CLAIM
    assert state["context"] == CONTEXT[-2:]
    assert [p["passage"] for p in state["passages"]] == [1, 2]
    first = state["passages"][0]
    assert first["text"] == CHUNKS[0].content
    assert first["title"] == "Zorblax guideline" and first["section"] == "Spread"
    assert "trust" in first and "year" in first

    assert out == JevVerdict(probs=DEFAULT, label="supported", p=0.8)
    assert jev.model_used == "jev-1.14.2"


@pytest.mark.anyio
async def test_trust_and_year_come_from_the_chunk_when_present():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Ev:
        chunk_id: int
        section: str
        content: str
        title: str
        trust: str
        year: int

    jev, rec = client(ok(DEFAULT))
    async with jev:
        await jev_confidence(jev, CLAIM, [], [Ev(1, "S", "text", "T", "high", 2021)])
    assert rec.bodies[0]["state"]["passages"][0] | {} == {
        "passage": 1, "title": "T", "section": "S", "trust": "high", "year": 2021, "text": "text",
    }


@pytest.mark.anyio
async def test_no_luna_verdict_is_shown_to_jev():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        await jev_confidence(jev, CLAIM, CONTEXT, CHUNKS)
    raw = json.dumps(rec.bodies[0]).lower()
    for word in ("luna", "accurate", "inaccurate", "disputed", "unverified", "verdict_hint", "stance"):
        assert word not in raw


@pytest.mark.anyio
@pytest.mark.parametrize(
    "probs, label, p",
    [
        ({"supported": 0.1, "contradicted": 0.7, "insufficient": 0.2}, "contradicted", 0.7),
        ({"supported": 0.1, "contradicted": 0.1, "insufficient": 0.8}, "insufficient", 0.8),
        ({"supported": 0.4, "contradicted": 0.4, "insufficient": 0.2}, "supported", 0.4),  # tie -> LABELS order
    ],
)
async def test_label_is_the_argmax(probs, label, p):
    jev, _ = client(ok(probs))
    async with jev:
        out = await jev_confidence(jev, CLAIM, [], CHUNKS)
    assert (out.label, out.p) == (label, p)
    assert out.probs == probs


def test_labels_and_criteria():
    assert LABELS == ("supported", "contradicted", "insufficient")
    text = " ".join(VERDICT_CRITERIA.values()).lower()
    assert "passages" in text and "general medical" in text


@pytest.mark.anyio
async def test_no_chunks_is_a_value_error_without_a_request():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        with pytest.raises(ValueError):
            await jev_confidence(jev, CLAIM, [], [])
    assert rec.requests == []


def malformed_cases():
    yield "missing answer", lambda r, b: httpx.Response(200, json={"answers": {}})
    yield "missing label", ok({"supported": 0.5, "contradicted": 0.5})
    yield "prob out of range", ok({"supported": 1.2, "contradicted": 0.0, "insufficient": 0.0})
    yield "prob is bool", ok({"supported": True, "contradicted": 0.0, "insufficient": 0.0})
    yield "prob is str", ok({"supported": "0.9", "contradicted": 0.0, "insufficient": 0.0})
    yield "wrong type", lambda r, b: httpx.Response(200, json={"answers": {"verdict": {"type": "noul", "noul": 0.5}}})
    yield "answers not a dict", lambda r, b: httpx.Response(200, json={"answers": []})
    yield "body not a dict", lambda r, b: httpx.Response(200, json=[1, 2])
    yield "not json", lambda r, b: httpx.Response(200, text="nope")


@pytest.mark.anyio
@pytest.mark.parametrize("name, handler", list(malformed_cases()))
async def test_malformed_answer_is_upstream_error(name, handler):
    jev, _ = client(handler)
    async with jev:
        with pytest.raises(JevUpstreamError):
            await jev_confidence(jev, CLAIM, CONTEXT, CHUNKS)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status, error", [(401, JevAuthError), (403, JevAuthError), (429, JevRateLimited), (500, JevUpstreamError)]
)
async def test_http_errors_map_to_jev_errors_without_leaking(status, error, caplog):
    caplog.set_level(logging.DEBUG)
    jev, _ = client(lambda r, b: httpx.Response(status, json={"echo": CLAIM}))
    async with jev:
        with pytest.raises(error) as e:
            await jev_confidence(jev, CLAIM, CONTEXT, CHUNKS)
    for secret in (CLAIM, KEY):
        assert secret not in str(e.value)
        assert secret not in caplog.text


@pytest.mark.anyio
async def test_ask_returns_the_answers_dict():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        answers = await jev.ask({"x": 1}, {"stance_0": {"type": "choice", "criteria": {"a": "b"}}})
    assert set(answers) == {"verdict"}
    assert rec.bodies[0] == {
        "model": MODEL,
        "state": {"x": 1},
        "questions": {"stance_0": {"type": "choice", "criteria": {"a": "b"}}},
    }
