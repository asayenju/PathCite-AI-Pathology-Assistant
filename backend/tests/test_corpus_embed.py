import json
import logging

import httpx
import pytest

from app.config import Settings
from app.corpus.embed import (
    EMBED_DIMS,
    MAX_INPUT_TOKENS,
    EmbedAuthError,
    EmbedError,
    EmbedRateLimited,
    EmbedUpstreamError,
    OpenAIEmbedder,
    count_tokens,
)

KEY = "sk-openai-test-key-do-not-leak"
TEXT = "Sentinel lymph node biopsy is offered for stage IB melanoma."


def vec(i: int, dims: int = EMBED_DIMS) -> list[float]:
    return [float(i)] + [0.0] * (dims - 1)


def ok(request: httpx.Request, body: dict, *, dims: int = EMBED_DIMS, reverse=False) -> httpx.Response:
    data = [{"object": "embedding", "index": i, "embedding": vec(i, dims)} for i in range(len(body["input"]))]
    if reverse:
        data.reverse()
    n = sum(count_tokens(t) for t in body["input"])
    return httpx.Response(
        200,
        json={"object": "list", "data": data, "model": body["model"],
              "usage": {"prompt_tokens": n, "total_tokens": n}},
    )


def status(code: int, headers: dict | None = None):
    return lambda request, body: httpx.Response(code, headers=headers or {}, json={"error": {"message": "x"}})


class Recorder:
    def __init__(self, *responders):
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.responders = list(responders)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        r = self.responders.pop(0) if len(self.responders) > 1 else self.responders[0]
        return r(request, body)


class Sleeps:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, s: float) -> None:
        self.calls.append(s)


def make(rec, sleep=None, **kw) -> OpenAIEmbedder:
    return OpenAIEmbedder(KEY, transport=httpx.MockTransport(rec), sleep=sleep or Sleeps(), **kw)


# --- request shape ------------------------------------------------------------


def test_request_shape_and_auth_header():
    rec = Recorder(ok)
    with make(rec) as e:
        out = e.embed([TEXT, "second"])
    assert len(out) == 2 and all(len(v) == EMBED_DIMS for v in out)
    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == "https://api.openai.com/v1/embeddings"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["content-type"] == "application/json"
    assert rec.bodies[0] == {"model": "text-embedding-3-small", "input": [TEXT, "second"],
                             "encoding_format": "float"}


def test_empty_list_makes_no_call():
    rec = Recorder(ok)
    with make(rec) as e:
        assert e.embed([]) == []
    assert rec.requests == []


def test_usage_tokens_accumulate():
    rec = Recorder(ok)
    with make(rec) as e:
        e.embed([TEXT])
        e.embed(["a", "b"])
        assert e.total_tokens == count_tokens(TEXT) + count_tokens("a") + count_tokens("b")


# --- batching -----------------------------------------------------------------


def test_batch_split_by_count():
    rec = Recorder(ok)
    texts = [f"text {i}" for i in range(5)]
    with make(rec, max_batch=2) as e:
        out = e.embed(texts)
    assert [len(b["input"]) for b in rec.bodies] == [2, 2, 1]
    assert [b["input"] for b in rec.bodies] == [texts[:2], texts[2:4], texts[4:]]
    assert len(out) == 5


def test_default_batch_limit_is_2048():
    rec = Recorder(ok)
    with make(rec) as e:
        e.embed(["x"] * 2049)
    assert [len(b["input"]) for b in rec.bodies] == [2048, 1]


def test_batch_split_by_tokens():
    rec = Recorder(ok)
    t = "word " * 99  # ~99 tokens
    n = count_tokens(t)
    with make(rec, max_batch_tokens=n * 2 + 1) as e:
        e.embed([t] * 5)
    assert [len(b["input"]) for b in rec.bodies] == [2, 2, 1]
    for b in rec.bodies:
        assert sum(count_tokens(x) for x in b["input"]) <= n * 2 + 1


def test_input_over_token_limit_rejected_before_any_call():
    rec = Recorder(ok)
    huge = "token " * (MAX_INPUT_TOKENS + 10)
    with make(rec) as e, pytest.raises(ValueError, match="8191"):
        e.embed(["fine", huge])
    assert rec.requests == []


# --- order and validation -----------------------------------------------------


def test_order_preserved_by_index():
    rec = Recorder(lambda r, b: ok(r, b, reverse=True))
    with make(rec) as e:
        out = e.embed(["a", "b", "c"])
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]


def test_order_preserved_across_batches():
    rec = Recorder(lambda r, b: ok(r, b, reverse=True))
    with make(rec, max_batch=2) as e:
        out = e.embed(["a", "b", "c", "d", "e"])
    assert [v[0] for v in out] == [0.0, 1.0, 0.0, 1.0, 0.0]  # index is per batch
    assert len(out) == 5


def test_wrong_vector_length_rejected():
    rec = Recorder(lambda r, b: ok(r, b, dims=768))
    with make(rec) as e, pytest.raises(EmbedUpstreamError, match="1536"):
        e.embed(["a"])


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": vec(0)}, {"index": 0, "embedding": vec(0)}]},
        {"data": [{"index": 5, "embedding": vec(0)}, {"index": 1, "embedding": vec(0)}]},
        {"data": [{"index": 0, "embedding": "nope"}, {"index": 1, "embedding": vec(0)}]},
        {"data": [{"index": 0, "embedding": [None] * EMBED_DIMS}, {"index": 1, "embedding": vec(0)}]},
        {"nothing": 1},
        [],
    ],
)
def test_malformed_response_rejected(payload):
    rec = Recorder(lambda r, b: httpx.Response(200, json=payload))
    with make(rec) as e, pytest.raises(EmbedUpstreamError):
        e.embed(["a", "b"])


def test_non_json_body_rejected():
    rec = Recorder(lambda r, b: httpx.Response(200, content=b"<html>"))
    with make(rec) as e, pytest.raises(EmbedUpstreamError):
        e.embed(["a"])


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_empty_or_whitespace_input_rejected_before_any_call(bad):
    rec = Recorder(ok)
    with make(rec) as e, pytest.raises(ValueError, match="index 1"):
        e.embed(["fine", bad])
    assert rec.requests == []


def test_non_string_input_rejected():
    rec = Recorder(ok)
    with make(rec) as e, pytest.raises(ValueError):
        e.embed(["fine", None])  # type: ignore[list-item]
    assert rec.requests == []


# --- retries ------------------------------------------------------------------


def test_429_then_success_honours_retry_after():
    rec = Recorder(status(429, {"retry-after": "3"}), ok)
    sleeps = Sleeps()
    with make(rec, sleep=sleeps) as e:
        out = e.embed(["a"])
    assert len(out) == 1
    assert len(rec.requests) == 2
    assert sleeps.calls == [3.0]


def test_429_retry_after_is_capped():
    rec = Recorder(status(429, {"retry-after": "9999"}), ok)
    sleeps = Sleeps()
    with make(rec, sleep=sleeps) as e:
        e.embed(["a"])
    assert sleeps.calls == [pytest.approx(60.0)]


def test_429_without_retry_after_uses_exponential_backoff():
    rec = Recorder(status(429), status(429), ok)
    sleeps = Sleeps()
    with make(rec, sleep=sleeps) as e:
        e.embed(["a"])
    assert sleeps.calls == [1.0, 2.0]


def test_429_persistent_raises_rate_limited():
    rec = Recorder(status(429, {"retry-after": "1"}))
    sleeps = Sleeps()
    with make(rec, sleep=sleeps, max_retries=3) as e, pytest.raises(EmbedRateLimited):
        e.embed(["a"])
    assert len(rec.requests) == 4
    assert len(sleeps.calls) == 3


def test_5xx_then_success():
    rec = Recorder(status(503), ok)
    with make(rec) as e:
        assert len(e.embed(["a"])) == 1
    assert len(rec.requests) == 2


def test_repeated_5xx_raises_after_bounded_retries():
    rec = Recorder(status(500))
    sleeps = Sleeps()
    with make(rec, sleep=sleeps, max_retries=3) as e, pytest.raises(EmbedUpstreamError, match="500"):
        e.embed(["a"])
    assert len(rec.requests) == 4
    assert sleeps.calls == [1.0, 2.0, 4.0]


def test_backoff_is_capped():
    rec = Recorder(status(502))
    sleeps = Sleeps()
    with make(rec, sleep=sleeps, max_retries=8) as e, pytest.raises(EmbedUpstreamError):
        e.embed(["a"])
    assert max(sleeps.calls) <= 60.0


def test_timeout_retried_then_raises():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    sleeps = Sleeps()
    e = OpenAIEmbedder(KEY, transport=httpx.MockTransport(handler), sleep=sleeps, max_retries=2)
    with e, pytest.raises(EmbedUpstreamError, match="ReadTimeout") as info:
        e.embed(["a"])
    assert len(calls) == 3
    assert info.value.__cause__ is None


def test_timeout_then_success():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectTimeout("slow", request=request)
        return ok(request, json.loads(request.content))

    e = OpenAIEmbedder(KEY, transport=httpx.MockTransport(handler), sleep=Sleeps())
    with e:
        assert len(e.embed(["a"])) == 1


@pytest.mark.parametrize("code", [401, 403])
def test_auth_error_names_env_var_and_is_not_retried(code):
    rec = Recorder(status(code))
    sleeps = Sleeps()
    with make(rec, sleep=sleeps) as e, pytest.raises(EmbedAuthError, match="OPEN_AI_VECTOR_KEY"):
        e.embed(["a"])
    assert len(rec.requests) == 1
    assert sleeps.calls == []


def test_other_4xx_not_retried():
    rec = Recorder(status(400))
    with make(rec) as e, pytest.raises(EmbedUpstreamError, match="400"):
        e.embed(["a"])
    assert len(rec.requests) == 1


def test_error_hierarchy():
    for cls in (EmbedAuthError, EmbedRateLimited, EmbedUpstreamError):
        assert issubclass(cls, EmbedError)


# --- privacy ------------------------------------------------------------------


def test_key_and_text_never_logged_or_in_errors(caplog):
    caplog.set_level(logging.DEBUG)  # includes httpx/httpcore debug loggers
    errors = []
    for responders in [(ok,), (status(429), ok), (status(500),), (status(401),),
                       (lambda r, b: ok(r, b, dims=3),)]:
        with make(Recorder(*responders)) as e:
            try:
                e.embed([TEXT])
            except EmbedError as exc:
                errors.append(exc)
    assert len(errors) == 3
    text = caplog.text + " ".join(f"{e!r} {e}" for e in errors)
    assert KEY not in text
    assert TEXT not in text
    with make(Recorder(ok)) as e:
        assert KEY not in repr(e)


# --- settings -----------------------------------------------------------------


def test_settings_defaults(monkeypatch):
    for k in ("OPEN_AI_VECTOR_KEY", "EMBED_MODEL", "EMBED_DIMS"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.openai_vector_key is None
    assert s.embed_model == "text-embedding-3-small"
    assert s.embed_dims == 1536


def test_settings_reads_key_and_hides_it(monkeypatch):
    monkeypatch.setenv("OPEN_AI_VECTOR_KEY", KEY)
    s = Settings(_env_file=None)
    assert s.openai_vector_key.get_secret_value() == KEY
    assert KEY not in repr(s) and KEY not in str(s)


def test_settings_reads_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_AI_VECTOR_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text(f"OPEN_AI_VECTOR_KEY={KEY}\n")
    assert Settings(_env_file=f).openai_vector_key.get_secret_value() == KEY


def test_from_settings_without_key_raises(monkeypatch):
    monkeypatch.delenv("OPEN_AI_VECTOR_KEY", raising=False)
    with pytest.raises(EmbedAuthError, match="OPEN_AI_VECTOR_KEY"):
        OpenAIEmbedder.from_settings(Settings(_env_file=None))


def test_from_settings_uses_key_and_model(monkeypatch):
    monkeypatch.setenv("OPEN_AI_VECTOR_KEY", KEY)
    rec = Recorder(ok)
    with OpenAIEmbedder.from_settings(Settings(_env_file=None), transport=httpx.MockTransport(rec)) as e:
        e.embed(["a"])
    assert rec.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert rec.bodies[0]["model"] == "text-embedding-3-small"
