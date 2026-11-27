"""Sync client for OpenAI embeddings (POST /v1/embeddings) with text-embedding-3-small.

Structurally implements the corpus `Embedder` protocol: `embed(texts) -> list[list[float]]`,
one 1536-float vector per input, in input order.

- Batching: at most `max_batch` (2048, the API limit) inputs per request, and at most
  `max_batch_tokens` tokens per request (counted with tiktoken cl100k_base, the tokenizer the
  text-embedding-3 models use). The API caps a request at 300k tokens; 250k leaves headroom.
- Validation before any call: every input must be a non-blank string of at most 8191 tokens.
- Retries: 429 honours `retry-after` (capped), else capped exponential backoff; 5xx, timeouts
  and network errors back off exponentially. Both are bounded by `max_retries`.
- `dimensions` is not sent: 1536 is the model's native size, so nothing is truncated, and the
  length check below catches a model/config mismatch instead of hiding it.

Privacy (same discipline as app/triage/jev.py): the key, input text and bodies are never logged
or put in exception messages; httpx errors are raised `from None` so their request isn't chained.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator, Sequence
from functools import lru_cache
from typing import Any

import httpx

from app.config import Settings

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536
MAX_INPUT_TOKENS = 8191  # per input, OpenAI limit for text-embedding-3-*
MAX_BATCH = 2048  # inputs per request, OpenAI limit
MAX_BATCH_TOKENS = 250_000  # per request; the API limit is 300k
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0

KEY_HINT = "set OPEN_AI_VECTOR_KEY in the repo-root .env"


class EmbedError(Exception):
    """Base class. Messages never contain input text, bodies or the key."""


class EmbedAuthError(EmbedError):
    """Missing key, or 401/403: the key was rejected."""


class EmbedRateLimited(EmbedError):
    """429 still after retries."""


class EmbedUpstreamError(EmbedError):
    """Timeout, network error, 5xx after retries, other 4xx, or a malformed response."""


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


def _malformed(why: str = "") -> EmbedUpstreamError:
    return EmbedUpstreamError("OpenAI returned a malformed embeddings response" + (f": {why}" if why else ""))


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


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = EMBED_MODEL,
        dims: int = EMBED_DIMS,
        base_url: str = "https://api.openai.com",
        timeout_s: float = 60,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 5,
        max_batch: int = MAX_BATCH,
        max_batch_tokens: int = MAX_BATCH_TOKENS,
    ):
        if not 1 <= max_batch <= MAX_BATCH:
            raise ValueError(f"max_batch must be between 1 and {MAX_BATCH}")
        if max_batch_tokens < 1:
            raise ValueError("max_batch_tokens must be >= 1")
        self.model = model
        self.dims = dims
        self.total_tokens = 0  # from response `usage`, for cost reporting
        self._sleep = sleep
        self._max_retries = max_retries
        self._max_batch = max_batch
        self._max_batch_tokens = max_batch_tokens
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> OpenAIEmbedder:
        key = settings.openai_vector_key
        if key is None or not key.get_secret_value().strip():
            raise EmbedAuthError(f"No OpenAI embeddings key: {KEY_HINT}")
        return cls(key.get_secret_value(), model=settings.embed_model, dims=settings.embed_dims, **kwargs)

    def __repr__(self) -> str:
        return f"OpenAIEmbedder(model={self.model!r}, dims={self.dims})"

    def __enter__(self) -> OpenAIEmbedder:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # --- public ------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input, in input order. Validates every input before any call."""
        counts = self._validate(texts)
        out: list[list[float]] = []
        for start, end in self._batches(counts):
            out.extend(self._embed_batch(list(texts[start:end])))
        return out

    # --- batching ----------------------------------------------------------

    def _validate(self, texts: Sequence[str]) -> list[int]:
        counts = []
        for i, t in enumerate(texts):
            if not isinstance(t, str) or not t.strip():
                raise ValueError(f"embedding input at index {i} is empty or not a string")
            n = count_tokens(t)
            if n > MAX_INPUT_TOKENS:
                raise ValueError(
                    f"embedding input at index {i} has {n} tokens; the limit is {MAX_INPUT_TOKENS}"
                )
            counts.append(n)
        return counts

    def _batches(self, counts: list[int]) -> Iterator[tuple[int, int]]:
        start, tokens = 0, 0
        for i, n in enumerate(counts):
            if i > start and (i - start >= self._max_batch or tokens + n > self._max_batch_tokens):
                yield start, i
                start, tokens = i, 0
            tokens += n
        if start < len(counts):
            yield start, len(counts)

    # --- request -----------------------------------------------------------

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        body = {"model": self.model, "input": batch, "encoding_format": "float"}
        return self._parse(self._post(body), len(batch))

    def _post(self, body: dict) -> Any:
        for attempt in range(self._max_retries + 1):
            last = attempt >= self._max_retries
            try:
                response = self._http.post("/v1/embeddings", json=body)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if not last:
                    self._sleep(_backoff(attempt))
                    continue
                raise EmbedUpstreamError(f"OpenAI request failed: {type(e).__name__}") from None
            except httpx.HTTPError as e:
                raise EmbedUpstreamError(f"OpenAI request failed: {type(e).__name__}") from None

            code = response.status_code
            if code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise EmbedUpstreamError("OpenAI returned a non-JSON body") from None
            if code in (401, 403):
                raise EmbedAuthError(f"OpenAI rejected the embeddings key (HTTP {code}): check OPEN_AI_VECTOR_KEY")
            if code == 429:
                if not last:
                    self._sleep(_retry_after(response, attempt))
                    continue
                raise EmbedRateLimited("OpenAI rate limit (HTTP 429) after retries")
            if code >= 500:
                if not last:
                    self._sleep(_backoff(attempt))
                    continue
                raise EmbedUpstreamError(f"OpenAI returned HTTP {code} after retries")
            raise EmbedUpstreamError(f"OpenAI returned HTTP {code}")
        raise AssertionError("unreachable")  # pragma: no cover

    # --- response ----------------------------------------------------------

    def _parse(self, data: Any, n: int) -> list[list[float]]:
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise _malformed()
        items = data["data"]
        if len(items) != n:
            raise _malformed(f"expected {n} embeddings, got {len(items)}")
        out: list[list[float] | None] = [None] * n
        for item in items:
            if not isinstance(item, dict):
                raise _malformed()
            idx = item.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < n or out[idx] is not None:
                raise _malformed("bad or duplicate index")
            emb = item.get("embedding")
            if not isinstance(emb, list):
                raise _malformed("embedding is not a list")
            if len(emb) != self.dims:
                raise EmbedUpstreamError(
                    f"OpenAI returned a {len(emb)}-dimension embedding; expected {self.dims}"
                )
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in emb):
                raise _malformed("embedding has non-numeric values")
            out[idx] = [float(x) for x in emb]

        usage = data.get("usage")
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens", usage.get("prompt_tokens"))
            if isinstance(tokens, int) and not isinstance(tokens, bool):
                self.total_tokens += tokens
        return out  # type: ignore[return-value]  # every slot filled: n unique indices in range
