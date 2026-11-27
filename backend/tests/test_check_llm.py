import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.check.llm import (
    LLMAuthError,
    LLMError,
    LLMRateLimited,
    LLMUpstreamError,
    OpenAIResponsesClient,
    Usage,
)
from app.config import Settings

KEY = "sk-openai-test-key-do-not-leak"
MODEL = "gpt-6-luna"
SECRET_INPUT = "Breslow thickness is measured from the granular layer SECRET-CLAIM-TEXT"
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def ok_body(payload: dict | str = None, *, usage=None, extra_output=None):
    text = payload if isinstance(payload, str) else json.dumps(payload or {"answer": "SECRET-RESPONSE-TEXT"})
    output = [
        {"id": "rs_1", "type": "reasoning", "summary": []},
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        },
    ]
    if extra_output is not None:
        output = extra_output
    return {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "model": MODEL,
        "output": output,
        "usage": usage
        or {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 64},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 30},
            "total_tokens": 170,
        },
    }


class Recorder:
    def __init__(self, *responders):
        self.requests: list[httpx.Request] = []
        self.responders = list(responders)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.responders.pop(0) if len(self.responders) > 1 else self.responders[0]
        return r(request)

    @property
    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def reply(status=200, body=None, headers=None, content=None):
    def respond(request):
        if content is not None:
            return httpx.Response(status, content=content, headers=headers)
        return httpx.Response(status, json=body if body is not None else ok_body(), headers=headers)

    return respond


def make_client(recorder, sleeps=None, **kw):
    async def fake_sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    return OpenAIResponsesClient(
        KEY, model=MODEL, transport=httpx.MockTransport(recorder), sleep=fake_sleep, **kw
    )


async def call(client):
    return await client.structured(
        instructions="Fixed instructions.", input=SECRET_INPUT, schema=SCHEMA, name="answer_v1"
    )


@pytest.mark.anyio
async def test_request_shape_and_parsing():
    rec = Recorder(reply())
    async with make_client(rec) as client:
        data, usage = await call(client)
    assert data == {"answer": "SECRET-RESPONSE-TEXT"}
    assert usage == Usage(input_tokens=120, cached_tokens=64, output_tokens=50, reasoning_tokens=30)

    (req,) = rec.requests
    assert req.method == "POST"
    assert req.url == "https://api.openai.com/v1/responses"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = rec.bodies[0]
    assert body["model"] == MODEL
    assert body["instructions"] == "Fixed instructions."
    assert body["input"] == SECRET_INPUT
    fmt = body["text"]["format"]
    assert fmt == {"type": "json_schema", "name": "answer_v1", "strict": True, "schema": SCHEMA}
    assert body["reasoning"] == {"effort": "low"}
    assert body["store"] is False


@pytest.mark.anyio
async def test_reasoning_effort_can_be_omitted():
    rec = Recorder(reply())
    async with make_client(rec, reasoning_effort=None) as client:
        await call(client)
    assert "reasoning" not in rec.bodies[0]


@pytest.mark.anyio
async def test_usage_totals_accumulate():
    rec = Recorder(reply())
    async with make_client(rec) as client:
        await call(client)
        await call(client)
        assert client.total_usage == Usage(240, 128, 100, 60)
        assert client.requests == 2


@pytest.mark.anyio
async def test_missing_usage_is_zero():
    body = ok_body()
    del body["usage"]
    rec = Recorder(reply(body=body))
    async with make_client(rec) as client:
        _, usage = await call(client)
    assert usage == Usage(0, 0, 0, 0)


@pytest.mark.anyio
async def test_retries_429_honouring_retry_after_then_succeeds():
    sleeps = []
    rec = Recorder(reply(429, {"error": {}}, {"retry-after": "3"}), reply())
    async with make_client(rec, sleeps) as client:
        data, _ = await call(client)
    assert data == {"answer": "SECRET-RESPONSE-TEXT"}
    assert sleeps == [3.0]
    assert len(rec.requests) == 2


@pytest.mark.anyio
async def test_retry_after_is_capped_and_bad_values_fall_back():
    sleeps = []
    rec = Recorder(
        reply(429, {}, {"retry-after": "9999"}), reply(429, {}, {"retry-after": "soon"}), reply()
    )
    async with make_client(rec, sleeps) as client:
        await call(client)
    assert sleeps[0] == 30.0
    assert 0 < sleeps[1] <= 30.0


@pytest.mark.anyio
async def test_exhausted_429_raises_rate_limited():
    sleeps = []
    rec = Recorder(reply(429, {}))
    async with make_client(rec, sleeps, max_retries=2) as client:
        with pytest.raises(LLMRateLimited):
            await call(client)
    assert len(rec.requests) == 3
    assert len(sleeps) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_retried_with_backoff(status):
    sleeps = []
    rec = Recorder(reply(status, {}), reply(status, {}), reply())
    async with make_client(rec, sleeps) as client:
        await call(client)
    assert sleeps == [1.0, 2.0]


@pytest.mark.anyio
async def test_exhausted_5xx_raises_upstream():
    rec = Recorder(reply(500, {}))
    async with make_client(rec, max_retries=1) as client:
        with pytest.raises(LLMUpstreamError, match="HTTP 500"):
            await call(client)
    assert len(rec.requests) == 2


@pytest.mark.anyio
async def test_timeout_retried_then_upstream():
    calls = []

    def boom(request):
        calls.append(1)
        raise httpx.ReadTimeout("timed out with SECRET-CLAIM-TEXT", request=request)

    async with make_client(Recorder(boom), max_retries=2) as client:
        with pytest.raises(LLMUpstreamError) as e:
            await call(client)
    assert len(calls) == 3
    assert "ReadTimeout" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_fast(status):
    rec = Recorder(reply(status, {"error": {"message": "bad key"}}))
    async with make_client(rec) as client:
        with pytest.raises(LLMAuthError):
            await call(client)
    assert len(rec.requests) == 1


@pytest.mark.anyio
async def test_other_4xx_is_upstream_without_retry():
    rec = Recorder(reply(400, {"error": {"message": "SECRET-CLAIM-TEXT bad schema"}}))
    async with make_client(rec) as client:
        with pytest.raises(LLMUpstreamError, match="HTTP 400") as e:
            await call(client)
    assert "SECRET" not in str(e.value)
    assert len(rec.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "responder",
    [
        reply(content=b"<html>not json</html>"),
        reply(body=["not", "an", "object"]),
        reply(body=ok_body(extra_output=[{"type": "reasoning", "summary": []}])),  # no message
        reply(body=ok_body(extra_output=[{"type": "message", "content": []}])),  # no output_text
        reply(body=ok_body("not json at all")),
        reply(body=ok_body("[1, 2]")),  # JSON but not an object
        reply(
            body=ok_body(
                extra_output=[
                    {"type": "message", "content": [{"type": "refusal", "refusal": "I can't help"}]}
                ]
            )
        ),
        reply(body={**ok_body(), "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}),
        reply(body={**ok_body(), "output": "nope"}),
    ],
    ids=["non-json-body", "body-not-object", "no-message", "no-output-text", "text-not-json",
         "text-not-object", "refusal", "incomplete", "output-not-list"],
)
async def test_malformed_answers_are_upstream_errors(responder):
    async with make_client(Recorder(responder)) as client:
        with pytest.raises(LLMUpstreamError) as e:
            await call(client)
    assert "SECRET" not in str(e.value)
    assert "can't help" not in str(e.value)


@pytest.mark.anyio
async def test_nothing_sensitive_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    errors = []
    for responder in [reply(401, {}), reply(429, {}), reply(500, {}), reply(body=ok_body("bad"))]:
        async with make_client(Recorder(responder), max_retries=0) as client:
            try:
                await call(client)
            except LLMError as e:
                errors.append(e)
                text = f"{e!s} {e!r}"
                assert KEY not in text and "SECRET" not in text
    assert len(errors) == 4
    assert KEY not in caplog.text
    assert "SECRET" not in caplog.text


def test_repr_hides_key():
    client = OpenAIResponsesClient(KEY, model=MODEL)
    assert KEY not in repr(client)


def test_from_settings_requires_key():
    with pytest.raises(LLMAuthError, match="OPEN_AI_KEY"):
        OpenAIResponsesClient.from_settings(Settings(_env_file=None, openai_key=None))
    with pytest.raises(LLMAuthError):
        OpenAIResponsesClient.from_settings(Settings(_env_file=None, openai_key=SecretStr("  ")))


@pytest.mark.anyio
async def test_from_settings_uses_model_and_timeout():
    s = Settings(_env_file=None, openai_key=SecretStr(KEY), checker_model="gpt-x", checker_timeout_s=7)
    client = OpenAIResponsesClient.from_settings(s)
    assert client.model == "gpt-x"
    assert client._http.timeout.read == 7
    await client.aclose()


def test_settings_defaults_and_alias(monkeypatch):
    monkeypatch.delenv("OPEN_AI_KEY", raising=False)
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.openai_key is None
    assert s.checker_model == "gpt-6-luna"
    assert s.checker_timeout_s > 0
    monkeypatch.setenv("OPEN_AI_KEY", KEY)
    s = Settings(_env_file=None)
    assert s.openai_key.get_secret_value() == KEY
    assert KEY not in repr(s)
