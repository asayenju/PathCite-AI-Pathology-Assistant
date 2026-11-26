import json
import logging

import httpx
import pytest

from app.triage.jev import (
    JevAuthError,
    JevClient,
    JevError,
    JevRateLimited,
    JevUpstreamError,
    SpanAnswer,
    SpanInput,
)

KEY = "sk-jev-test-key-do-not-leak"
MODEL = "jev-1.14.0"
TURN = "Good morning. The growth in your sigmoid colin is moderately differentiated."
SPANS = [
    SpanInput("0", "Good morning."),
    SpanInput("1", "The growth in your sigmoid colin is moderately differentiated.", (("colin", 0.41),)),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def answer_for(body, *, p_small=0.9, noul=0.1, model=MODEL):
    """Build a well-formed Jev response for the questions in a request body."""
    answers = {}
    for qid, q in body["questions"].items():
        if q["type"] == "choice":
            answers[qid] = {
                "type": "choice",
                "choice": "small_talk" if p_small >= 0.5 else "medical_claim",
                "confidence": max(p_small, 1 - p_small),
                "probabilities": {"small_talk": p_small, "medical_claim": 1 - p_small},
            }
        else:
            answers[qid] = {"type": "noul", "noul": noul}
    return {"model": model, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}


class Recorder:
    """MockTransport handler that records requests and replies from a list of responders."""

    def __init__(self, *responders):
        self.requests: list[httpx.Request] = []
        self.responders = list(responders)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        r = self.responders.pop(0) if len(self.responders) > 1 else self.responders[0]
        return r(request, body)

    @property
    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def ok(**kw):
    return lambda req, body: httpx.Response(200, json=answer_for(body, **kw))


def status(code, headers=None, json_body=None):
    return lambda req, body: httpx.Response(code, headers=headers or {}, json=json_body or {"error": "x"})


def make_client(handler, **kw):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = JevClient(KEY, model=MODEL, transport=httpx.MockTransport(handler), sleep=fake_sleep, **kw)
    return client, sleeps


# --- request shape ---------------------------------------------------------


@pytest.mark.anyio
async def test_request_shape():
    rec = Recorder(ok())
    client, _ = make_client(rec)
    async with client:
        await client.classify_spans(TURN, ["Hello there.", "Please sit."], SPANS)

    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["content-type"].startswith("application/json")

    body = rec.bodies[0]
    assert body["model"] == MODEL
    state = body["state"]
    assert state["turn"] == TURN
    assert state["previous_doctor_turns"] == ["Hello there.", "Please sit."]
    assert state["spans"] == [
        {"id": "0", "text": "Good morning.", "low_confidence_words": []},
        {"id": "1", "text": SPANS[1].text, "low_confidence_words": [{"word": "colin", "confidence": 0.41}]},
    ]
    qs = body["questions"]
    assert set(qs) == {"kind_0", "asr_0", "kind_1", "asr_1"}
    for i, span in enumerate(SPANS):
        kind = qs[f"kind_{i}"]
        assert kind["type"] == "choice"
        assert set(kind["criteria"]) == {"small_talk", "medical_claim"}
        assert span.text in kind["instructions"]
        asr = qs[f"asr_{i}"]
        assert asr["type"] == "noul"
        assert span.text in asr["instructions"]
        assert "hint" in asr["instructions"].lower()


@pytest.mark.anyio
async def test_criteria_cover_prd_definitions():
    rec = Recorder(ok())
    client, _ = make_client(rec)
    async with client:
        await client.classify_spans(TURN, [], SPANS[:1])
    crit = rec.bodies[0]["questions"]["kind_0"]["criteria"]
    claim, small = crit["medical_claim"].lower(), crit["small_talk"].lower()
    assert "general medical fact" in claim
    assert "hedged" in claim and "myth" in claim and "justify" in claim
    assert "question" in small and "plan" in small and "recommendation" in small
    assert "patient-specific result" in small


@pytest.mark.anyio
async def test_custom_base_url_and_trailing_slash():
    rec = Recorder(ok())
    client, _ = make_client(rec, base_url="https://jev.example/")
    async with client:
        await client.classify_spans(TURN, [], SPANS[:1])
    assert str(rec.requests[0].url) == "https://jev.example/v1/systemone"


# --- parsing ---------------------------------------------------------------


@pytest.mark.anyio
async def test_parses_answers_in_span_order():
    def handler(req, body):
        resp = answer_for(body)
        resp["answers"]["kind_0"]["probabilities"] = {"small_talk": 1.0, "medical_claim": 0.0}
        resp["answers"]["kind_1"]["probabilities"] = {"small_talk": 0.15, "medical_claim": 0.85}
        resp["answers"]["asr_0"]["noul"] = 0.04
        resp["answers"]["asr_1"]["noul"] = 0.93
        return httpx.Response(200, json=resp)

    client, _ = make_client(Recorder(handler))
    async with client:
        out = await client.classify_spans(TURN, [], SPANS)
    assert out == [SpanAnswer(p_small_talk=1.0, p_asr=0.04), SpanAnswer(p_small_talk=0.15, p_asr=0.93)]
    assert client.model_used == MODEL


@pytest.mark.anyio
async def test_model_used_echoes_response():
    client, _ = make_client(Recorder(ok(model="jev-1.14.1")))
    assert client.model_used is None
    async with client:
        await client.classify_spans(TURN, [], SPANS[:1])
    assert client.model_used == "jev-1.14.1"


@pytest.mark.anyio
async def test_ask_asr_false_sends_no_noul_questions():
    rec = Recorder(ok(p_small=0.3))
    client, _ = make_client(rec)
    async with client:
        out = await client.classify_spans(TURN, [], SPANS, ask_asr=False)
    qs = rec.bodies[0]["questions"]
    assert set(qs) == {"kind_0", "kind_1"}
    assert all(q["type"] == "choice" for q in qs.values())
    assert out == [SpanAnswer(0.3, None), SpanAnswer(0.3, None)]


@pytest.mark.anyio
async def test_no_spans_makes_no_call():
    rec = Recorder(ok())
    client, _ = make_client(rec)
    async with client:
        assert await client.classify_spans(TURN, [], []) == []
    assert rec.requests == []


@pytest.mark.anyio
async def test_batches_preserve_order():
    spans = [SpanInput(str(i), f"Sentence number {i}.") for i in range(85)]

    def handler(req, body):
        # p_small_talk encodes the global span id so order can be checked.
        resp = answer_for(body)
        for i, s in enumerate(body["state"]["spans"]):
            p = int(s["id"]) / 100
            resp["answers"][f"kind_{i}"]["probabilities"] = {"small_talk": p, "medical_claim": 1 - p}
        return httpx.Response(200, json=resp)

    rec = Recorder(handler)
    client, _ = make_client(rec, max_spans_per_request=40)
    async with client:
        out = await client.classify_spans(TURN, ["prev"], spans)

    assert [len(b["state"]["spans"]) for b in rec.bodies] == [40, 40, 5]
    for b in rec.bodies:
        assert b["state"]["turn"] == TURN
        assert b["state"]["previous_doctor_turns"] == ["prev"]
    assert [s["id"] for b in rec.bodies for s in b["state"]["spans"]] == [str(i) for i in range(85)]
    assert [a.p_small_talk for a in out] == pytest.approx([i / 100 for i in range(85)])


# --- retries and errors ----------------------------------------------------


@pytest.mark.anyio
async def test_429_then_200_retries_after_header():
    rec = Recorder(status(429, {"retry-after": "3"}), ok())
    client, sleeps = make_client(rec)
    async with client:
        out = await client.classify_spans(TURN, [], SPANS[:1])
    assert len(out) == 1
    assert len(rec.requests) == 2
    assert sleeps == [3]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "header,expected",
    [({"retry-after": "0.5"}, 0.5), ({}, 1), ({"retry-after": "120"}, 10), ({"retry-after": "soon"}, 1)],
)
async def test_retry_after_parsing(header, expected):
    client, sleeps = make_client(Recorder(status(429, header), ok()))
    async with client:
        await client.classify_spans(TURN, [], SPANS[:1])
    assert sleeps == [expected]


@pytest.mark.anyio
async def test_429_three_times_raises_rate_limited():
    rec = Recorder(status(429, {"retry-after": "1"}))
    client, sleeps = make_client(rec)
    async with client:
        with pytest.raises(JevRateLimited):
            await client.classify_spans(TURN, [], SPANS)
    assert len(rec.requests) == 3
    assert sleeps == [1, 1]


@pytest.mark.anyio
@pytest.mark.parametrize("code", [401, 403])
async def test_auth_errors(code):
    rec = Recorder(status(code))
    client, sleeps = make_client(rec)
    async with client:
        with pytest.raises(JevAuthError):
            await client.classify_spans(TURN, [], SPANS)
    assert len(rec.requests) == 1 and sleeps == []


@pytest.mark.anyio
@pytest.mark.parametrize("code", [500, 502, 503, 400, 404, 422])
async def test_other_statuses_are_upstream_errors(code):
    rec = Recorder(status(code))
    client, _ = make_client(rec)
    async with client:
        with pytest.raises(JevUpstreamError):
            await client.classify_spans(TURN, [], SPANS)
    assert len(rec.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_errors_are_upstream_errors(exc):
    def handler(request):
        raise exc("boom " + TURN, request=request)

    client = JevClient(KEY, model=MODEL, transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(JevUpstreamError) as info:
            await client.classify_spans(TURN, [], SPANS)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert TURN not in str(info.value)


def test_error_hierarchy():
    for cls in (JevAuthError, JevRateLimited, JevUpstreamError):
        assert issubclass(cls, JevError)


# --- malformed answers -----------------------------------------------------


def _mutate(fn):
    def handler(req, body):
        resp = answer_for(body)
        fn(resp)
        return httpx.Response(200, json=resp)

    return handler


MALFORMED = {
    "missing_kind": lambda r: r["answers"].pop("kind_1"),
    "missing_asr": lambda r: r["answers"].pop("asr_0"),
    "no_answers": lambda r: r.pop("answers"),
    "answers_not_dict": lambda r: r.update(answers=[1, 2]),
    "kind_wrong_type": lambda r: r["answers"]["kind_0"].update(type="noul"),
    "asr_wrong_type": lambda r: r["answers"]["asr_0"].update(type="choice"),
    "missing_small_talk": lambda r: r["answers"]["kind_0"]["probabilities"].pop("small_talk"),
    "missing_medical_claim": lambda r: r["answers"]["kind_0"]["probabilities"].pop("medical_claim"),
    "no_probabilities": lambda r: r["answers"]["kind_0"].pop("probabilities"),
    "prob_above_1": lambda r: r["answers"]["kind_0"]["probabilities"].update(small_talk=1.2),
    "prob_negative": lambda r: r["answers"]["kind_0"]["probabilities"].update(medical_claim=-0.1),
    "prob_string": lambda r: r["answers"]["kind_0"]["probabilities"].update(small_talk="0.9"),
    "prob_bool": lambda r: r["answers"]["kind_0"]["probabilities"].update(small_talk=True),
    "noul_above_1": lambda r: r["answers"]["asr_0"].update(noul=1.5),
    "noul_missing": lambda r: r["answers"]["asr_0"].pop("noul"),
}


@pytest.mark.anyio
@pytest.mark.parametrize("name", list(MALFORMED))
async def test_malformed_answers_raise_upstream(name):
    client, _ = make_client(Recorder(_mutate(MALFORMED[name])))
    async with client:
        with pytest.raises(JevUpstreamError):
            await client.classify_spans(TURN, [], SPANS)


@pytest.mark.anyio
@pytest.mark.parametrize("where", ["prob", "noul"])
async def test_nan_raises_upstream(where):
    def handler(req, body):
        text = json.dumps(answer_for(body))
        target = '"small_talk": 0.9' if where == "prob" else '"noul": 0.1'
        text = text.replace(target, target.split(":")[0] + ": NaN", 1)
        return httpx.Response(200, content=text, headers={"content-type": "application/json"})

    client, _ = make_client(Recorder(handler))
    async with client:
        with pytest.raises(JevUpstreamError):
            await client.classify_spans(TURN, [], SPANS)


@pytest.mark.anyio
@pytest.mark.parametrize("content", [b"<html>oops</html>", b"", b"[1, 2]", b'"answers"'])
async def test_non_json_or_non_object_body_raises_upstream(content):
    client, _ = make_client(Recorder(lambda req, body: httpx.Response(200, content=content)))
    async with client:
        with pytest.raises(JevUpstreamError):
            await client.classify_spans(TURN, [], SPANS)


# --- privacy ---------------------------------------------------------------


def _echo(code, headers=None):
    # Error bodies that echo the request (and key) back, to prove they never reach messages.
    return lambda req, body: httpx.Response(
        code, headers=headers or {}, json={"error": f"bad key {KEY}", "echo": body}
    )


def _timeout(request):
    raise httpx.ReadTimeout(f"timed out {KEY} {TURN}", request=request)


PRIVACY_CASES = {
    "auth": (lambda: Recorder(_echo(401)), JevAuthError),
    "rate_limited": (lambda: Recorder(_echo(429, {"retry-after": "0"})), JevRateLimited),
    "server": (lambda: Recorder(_echo(500)), JevUpstreamError),
    "timeout": (lambda: _timeout, JevUpstreamError),
    "malformed": (lambda: Recorder(_mutate(lambda r: r["answers"]["kind_0"].update(type=TURN))), JevUpstreamError),
    "non_json": (lambda: Recorder(lambda req, body: httpx.Response(200, content=f"{KEY} {TURN}")), JevUpstreamError),
}


@pytest.mark.anyio
@pytest.mark.parametrize("name", list(PRIVACY_CASES))
async def test_errors_never_carry_key_or_text(name, caplog):
    make_handler, exc_type = PRIVACY_CASES[name]
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(make_handler())
    async with client:
        with pytest.raises(exc_type) as info:
            await client.classify_spans(TURN, ["previous doctor words"], SPANS)
    exc = info.value
    for secret in (KEY, TURN, SPANS[0].text, SPANS[1].text, "colin", "previous doctor words"):
        assert secret not in str(exc)
        assert secret not in repr(exc)
        assert all(secret not in str(a) for a in exc.args)
    assert exc.__cause__ is None


@pytest.mark.anyio
async def test_nothing_from_transcript_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(Recorder(status(429, {"retry-after": "0"}), ok()))
    async with client:
        await client.classify_spans(TURN, ["previous doctor words"], SPANS)
    logged = caplog.text + " ".join(str(r.args) for r in caplog.records)
    for secret in (KEY, TURN, SPANS[1].text, "colin", "previous doctor words", "sigmoid"):
        assert secret not in logged


def test_client_repr_hides_key():
    client = JevClient(KEY, model=MODEL)
    assert KEY not in repr(client)
