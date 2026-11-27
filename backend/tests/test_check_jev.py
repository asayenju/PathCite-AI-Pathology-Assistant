"""Offline tests for the Jev per-source stance (app/check/jev_check.py) and JevClient.ask, with httpx.MockTransport."""

import json
import logging

import httpx
import pytest

from app.check.jev_check import STANCE_CRITERIA, JevStance, judge_sources
from app.check.verify import ChunkIn
from app.triage.jev import JevAuthError, JevClient, JevRateLimited, JevUpstreamError

KEY = "sk-jev-test-key-do-not-leak"
MODEL = "jev-1.14.0"
CLAIM = "Zorblaxian carcinoma always spreads to the spleen."
CONTEXT = ["Older turn one.", "Turn two.", "Turn three."]
CHUNKS = [
    ChunkIn(11, "Spread", "Zorblaxian carcinoma spreads to the spleen in most cases.", "Zorblax guideline"),
    ChunkIn(22, "Overview", "Zorblaxian carcinoma rarely spreads.", None),
    ChunkIn(33, None, "Anaplastic thyroid carcinoma is rare.", "Thyroid"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def choice(probs):
    return {"type": "choice", "choice": "supports", "probabilities": probs}


def ok(probs_by_index, model="jev-1.14.2"):
    def handler(request, body):
        answers = {f"stance_{i}": choice(p) for i, p in probs_by_index.items()}
        return httpx.Response(200, json={"model": model, "answers": answers, "usage": {"input_tokens": 5}})

    return handler


DEFAULT = {
    0: {"supports": 0.8, "contradicts": 0.1, "irrelevant": 0.1},
    1: {"supports": 0.05, "contradicts": 0.7, "irrelevant": 0.25},
    2: {"supports": 0.0, "contradicts": 0.02, "irrelevant": 0.98},
}


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
async def test_one_request_with_one_choice_per_chunk():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        out = await judge_sources(jev, CLAIM, CONTEXT, CHUNKS)

    assert len(rec.requests) == 1
    assert rec.requests[0].url.path == "/v1/systemone"
    body = rec.bodies[0]
    assert body["model"] == MODEL
    assert set(body["questions"]) == {"stance_0", "stance_1", "stance_2"}
    for q in body["questions"].values():
        assert q["type"] == "choice"
        assert set(q["criteria"]) == {"supports", "contradicts", "irrelevant"}
        assert q["criteria"] == STANCE_CRITERIA
    assert "Passage 2" in body["questions"]["stance_1"]["instructions"]
    state = body["state"]
    assert state["claim"] == CLAIM
    assert state["context"] == CONTEXT[-2:]
    assert [p["chunk_id"] for p in state["passages"]] == [11, 22, 33]
    assert state["passages"][0]["text"] == CHUNKS[0].content
    assert state["passages"][0]["title"] == "Zorblax guideline"

    assert out == [
        JevStance(11, "supports", 0.8),
        JevStance(22, "contradicts", 0.7),
        JevStance(33, "irrelevant", 0.98),
    ]
    assert jev.model_used == "jev-1.14.2"


def test_criteria_carry_the_luna_rules():
    text = " ".join(STANCE_CRITERIA.values()).lower()
    assert "only" in text and "passage" in text
    assert "patient-specific" in text
    assert "different subtype" in text


@pytest.mark.anyio
async def test_no_chunks_makes_no_request():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        assert await judge_sources(jev, CLAIM, [], []) == []
    assert rec.requests == []


@pytest.mark.anyio
async def test_too_many_chunks_is_a_value_error():
    jev, rec = client(ok(DEFAULT))
    chunks = [ChunkIn(i, None, "x") for i in range(41)]
    async with jev:
        with pytest.raises(ValueError):
            await judge_sources(jev, CLAIM, [], chunks)
    assert rec.requests == []


def malformed_cases():
    good = DEFAULT
    yield "missing answer", ok({0: good[0], 1: good[1]})
    yield "missing label", ok({**good, 1: {"supports": 0.5, "contradicts": 0.5}})
    yield "prob out of range", ok({**good, 0: {"supports": 1.2, "contradicts": 0.0, "irrelevant": 0.0}})
    yield "prob is bool", ok({**good, 0: {"supports": True, "contradicts": 0.0, "irrelevant": 0.0}})
    yield "prob is str", ok({**good, 0: {"supports": "0.9", "contradicts": 0.0, "irrelevant": 0.0}})
    yield "wrong type", lambda r, b: httpx.Response(
        200, json={"answers": {f"stance_{i}": {"type": "noul", "noul": 0.5} for i in range(3)}}
    )
    yield "answers not a dict", lambda r, b: httpx.Response(200, json={"answers": []})
    yield "body not a dict", lambda r, b: httpx.Response(200, json=[1, 2])
    yield "not json", lambda r, b: httpx.Response(200, text="nope")


@pytest.mark.anyio
@pytest.mark.parametrize("name, handler", list(malformed_cases()))
async def test_malformed_answer_is_upstream_error(name, handler):
    jev, _ = client(handler)
    async with jev:
        with pytest.raises(JevUpstreamError):
            await judge_sources(jev, CLAIM, CONTEXT, CHUNKS)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status, error", [(401, JevAuthError), (403, JevAuthError), (429, JevRateLimited), (500, JevUpstreamError)]
)
async def test_http_errors_map_to_jev_errors_without_leaking(status, error, caplog):
    caplog.set_level(logging.DEBUG)
    jev, _ = client(lambda r, b: httpx.Response(status, json={"echo": CLAIM}))
    async with jev:
        with pytest.raises(error) as e:
            await judge_sources(jev, CLAIM, CONTEXT, CHUNKS)
    for secret in (CLAIM, KEY):
        assert secret not in str(e.value)
        assert secret not in caplog.text


@pytest.mark.anyio
async def test_ask_returns_the_answers_dict():
    jev, rec = client(ok(DEFAULT))
    async with jev:
        answers = await jev.ask({"x": 1}, {"stance_0": {"type": "choice", "criteria": {"a": "b"}}})
    assert set(answers) == {"stance_0", "stance_1", "stance_2"}
    assert rec.bodies[0] == {
        "model": MODEL,
        "state": {"x": 1},
        "questions": {"stance_0": {"type": "choice", "criteria": {"a": "b"}}},
    }
