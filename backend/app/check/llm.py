"""Thin async client for the OpenAI Responses API (POST /v1/responses) with strict JSON output.

`structured(instructions=, input=, schema=, name=)` sends one request with
`text.format = {"type": "json_schema", "strict": true, ...}` and returns the parsed JSON object
plus the request's token usage. The fixed `instructions` are a separate field so the prompt
prefix is identical across calls and OpenAI's prompt caching applies.

Response shape (reasoning models such as gpt-6-luna): `output` holds a "reasoning" item and then
a "message" item whose `content` has `{"type": "output_text", "text": "<json>"}`. A missing
message, a refusal, an incomplete response or text that isn't a JSON object is an
`LLMUpstreamError`. Schema conformance beyond "is a JSON object" is the caller's job (see
`app/check/verify.py`), because strict mode is a server promise we still don't trust blindly.

Retries: 429 honours `retry-after` (capped), else capped exponential backoff; 5xx, timeouts and
network errors back off exponentially. Both are bounded by `max_retries`. 401/403 fail fast.

Privacy (same discipline as app/triage/jev.py): the key, the input text and request/response
bodies are never logged or put in exception messages. Messages carry only a status code or an
exception class name, and httpx errors are raised `from None` so their request isn't chained.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0
KEY_HINT = "set OPEN_AI_KEY in the repo-root .env"


class LLMError(Exception):
    """Base class. Messages never contain input text, bodies or the key."""


class LLMAuthError(LLMError):
    """Missing key, or 401/403: the key was rejected (API code check_unavailable)."""


class LLMRateLimited(LLMError):
    """429 still after retries (check_rate_limited)."""


class LLMUpstreamError(LLMError):
    """Timeout, network error, 5xx after retries, other 4xx or a malformed answer (check_upstream_error)."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0  # part of input_tokens served from the prompt cache
    output_tokens: int = 0
    reasoning_tokens: int = 0  # part of output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cached_tokens + other.cached_tokens,
            self.output_tokens + other.output_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
        )


def _malformed(why: str) -> LLMUpstreamError:
    return LLMUpstreamError(f"OpenAI returned a malformed answer: {why}")


def _backoff(attempt: int) -> float:
    return min(BACKOFF_BASE_S * 2**attempt, BACKOFF_MAX_S)


def _retry_after(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("retry-after")
    if raw is None:
        return _backoff(attempt)
    try:
        wait = float(raw)
    except ValueError:
        return _backoff(attempt)
    if not math.isfinite(wait) or wait < 0:
        return _backoff(attempt)
    return min(wait, BACKOFF_MAX_S)


def _count(obj: Any, key: str) -> int:
    value = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _usage(data: dict) -> Usage:
    u = data.get("usage")
    if not isinstance(u, dict):
        return Usage()
    return Usage(
        input_tokens=_count(u, "input_tokens"),
        cached_tokens=_count(u.get("input_tokens_details"), "cached_tokens"),
        output_tokens=_count(u, "output_tokens"),
        reasoning_tokens=_count(u.get("output_tokens_details"), "reasoning_tokens"),
    )


class OpenAIResponsesClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str = "https://api.openai.com",
        timeout_s: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
        max_retries: int = 2,
        reasoning_effort: str | None = "low",
    ):
        self.model = model
        self.total_usage = Usage()  # summed over successful requests, for cost reporting
        self.requests = 0
        self._sleep = sleep
        self._max_retries = max_retries
        self._reasoning_effort = reasoning_effort
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> OpenAIResponsesClient:
        key = settings.openai_key
        if key is None or not key.get_secret_value().strip():
            raise LLMAuthError(f"No OpenAI key for the checker: {KEY_HINT}")
        return cls(
            key.get_secret_value(),
            model=settings.checker_model,
            timeout_s=settings.checker_timeout_s,
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"OpenAIResponsesClient(model={self.model!r})"

    async def __aenter__(self) -> OpenAIResponsesClient:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- public ------------------------------------------------------------

    async def structured(
        self, *, instructions: str, input: str, schema: dict, name: str
    ) -> tuple[dict, Usage]:
        """One strict-JSON request. Returns the parsed object and this request's usage."""
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input,
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
            "store": False,  # nothing kept on OpenAI's side for later retrieval
        }
        if self._reasoning_effort is not None:
            body["reasoning"] = {"effort": self._reasoning_effort}
        data = await self._post(body)
        result = self._parse(data)
        usage = _usage(data)
        self.total_usage = self.total_usage + usage
        self.requests += 1
        return result, usage

    # --- transport ---------------------------------------------------------

    async def _post(self, body: dict) -> dict:
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                response = await self._http.post("/v1/responses", json=body)
            except httpx.HTTPError as e:
                if not last:
                    await self._sleep(_backoff(attempt))
                    continue
                raise LLMUpstreamError(f"OpenAI request failed: {type(e).__name__}") from None

            code = response.status_code
            if code == 429:
                if not last:
                    await self._sleep(_retry_after(response, attempt))
                    continue
                raise LLMRateLimited("OpenAI rate limit (HTTP 429)")
            if code in (401, 403):
                raise LLMAuthError(f"OpenAI rejected the key (HTTP {code})")
            if code >= 500:
                if not last:
                    await self._sleep(_backoff(attempt))
                    continue
                raise LLMUpstreamError(f"OpenAI returned HTTP {code}")
            if code != 200:
                raise LLMUpstreamError(f"OpenAI returned HTTP {code}")
            try:
                data = response.json()
            except ValueError:
                raise LLMUpstreamError("OpenAI returned a non-JSON body") from None
            if not isinstance(data, dict):
                raise _malformed("body is not an object")
            return data
        raise AssertionError("unreachable")  # pragma: no cover

    # --- response ----------------------------------------------------------

    @staticmethod
    def _parse(data: dict) -> dict:
        status = data.get("status")
        if status is not None and status != "completed":
            raise _malformed(f"status {status!s:.20}")
        output = data.get("output")
        if not isinstance(output, list):
            raise _malformed("no output list")
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    raise LLMUpstreamError("OpenAI refused to answer")
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        if not texts:
            raise _malformed("no message text")
        try:
            result = json.loads("".join(texts))
        except ValueError:
            raise _malformed("message text is not JSON") from None
        if not isinstance(result, dict):
            raise _malformed("message JSON is not an object")
        return result
