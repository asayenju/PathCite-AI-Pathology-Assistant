"""Evidence search for the claim checker (docs/PRD-rag-checker.md, section 1, "v3" and "v4").

`EvidenceSearch.search(claim)` embeds the claim with OpenAI (`text-embedding-3-small`, OPEN_AI_VECTOR_KEY) and runs
a hybrid search in Supabase with the secret key. The keyword half searches `keyword_text` (Luna's key terms, issue
#55) when given, else the claim itself; an optional `cancer` filters the keyword candidates (the vector list is never
filtered).

Two paths:
- **v4 (issue #56), when a `KeywordIndexProvider` is given and its local BM25 index matches the corpus:** the keyword
  search runs in the backend (app/check/bm25.py, top 40 chunk ids, cancer filter applied there), and Supabase gets
  only the embedding and those ids (`match_chunks_fused`, supabase/migrations/20261128010000_match_chunks_bm25.sql).
  If that RPC doesn't exist yet (PostgREST PGRST202), the provider is disabled for the process (logged once) and the
  same search is retried on the v3 path.
- **v3 fallback (no provider, or no usable index):** `match_chunks` with the keyword text and cancer
  (supabase/migrations/20261128000000_match_chunks_filter.sql): Postgres full-text search.

It reuses the sync clients of the corpus ingest (`OpenAIEmbedder`, `SupabaseCorpus`, with their retries and backoff)
and runs them in a worker thread, so it can be awaited from a FastAPI endpoint without blocking the event loop.

k (8), per_doc (2) and min_similarity (0.35) default in SQL and are only sent when overridden; `cancer` only when set.

Errors: everything is mapped to the three `SearchError` subclasses below, which the /check endpoint maps to
check_unavailable (503), check_rate_limited (429) and check_upstream_error (502). An empty claim or a bad override
raises ValueError before any call (so does a blank or non-string cancer).

Privacy: the claim is sent to OpenAI (to embed it) and, on the v3 path, to Supabase (as the keyword query, unless key
terms are given), and nowhere else; on the v4 path Supabase gets only the embedding and chunk ids. The
claim, the keys and request/response bodies are never logged or put in exception messages; errors are raised
`from None` so the underlying exception (and its httpx request) isn't chained.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any

import anyio.to_thread

from app.config import Settings
from app.corpus.embed import (
    EmbedAuthError,
    EmbedError,
    EmbedRateLimited,
    OpenAIEmbedder,
)
from app.check.bm25 import CANDIDATES as BM25_CANDIDATES
from app.check.bm25 import KeywordIndexProvider
from app.corpus.supabase import (
    SupabaseAuthError,
    SupabaseCorpus,
    SupabaseError,
    SupabaseRateLimited,
    SupabaseRequestError,
    SupabaseUnreachable,
)

RPC_MISSING = "PGRST202"  # PostgREST: no such function in the schema cache (the migration wasn't run)


@dataclass(frozen=True)
class Evidence:
    """One row of match_chunks. Ranks are 1-based and None when the chunk wasn't in that candidate list."""

    chunk_id: int
    doc_id: str
    section: str
    content: str
    trust: str  # high | medium | low
    source: str
    title: str | None
    url: str
    year: int | None
    vector_rank: int | None
    text_rank: int | None
    rrf_score: float
    similarity: float  # cosine similarity of the chunk to the claim


class SearchError(Exception):
    """Base class. Messages never contain the claim, keys or bodies."""


class SearchUnavailable(SearchError):
    """A key or URL is missing, or a key was rejected (check_unavailable, 503)."""


class SearchRateLimited(SearchError):
    """OpenAI or Supabase still rate-limited after retries (check_rate_limited, 429)."""


class SearchUpstreamError(SearchError):
    """Timeout, network error, 5xx, a paused project, another 4xx or a malformed response (check_upstream_error, 502)."""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_CHECKS: dict[str, Callable[[Any], bool]] = {
    "chunk_id": _is_int,
    "doc_id": lambda v: isinstance(v, str),
    "section": lambda v: isinstance(v, str),
    "content": lambda v: isinstance(v, str),
    "trust": lambda v: isinstance(v, str),
    "source": lambda v: isinstance(v, str),
    "title": lambda v: v is None or isinstance(v, str),
    "url": lambda v: isinstance(v, str),
    "year": lambda v: v is None or _is_int(v),
    "vector_rank": lambda v: v is None or _is_int(v),
    "text_rank": lambda v: v is None or _is_int(v),
    "rrf_score": _is_num,
    "similarity": _is_num,
}
assert set(_CHECKS) == {f.name for f in fields(Evidence)}


def _malformed(why: str) -> SearchUpstreamError:
    return SearchUpstreamError(f"Supabase returned a malformed match_chunks response: {why}")


def parse_rows(rows: Any) -> list[Evidence]:
    if not isinstance(rows, list):
        raise _malformed("not a list")
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise _malformed(f"row {i} is not an object")
        for name, ok in _CHECKS.items():
            if name not in row or not ok(row[name]):
                raise _malformed(f"row {i} has a missing or invalid {name}")
        values = {name: row[name] for name in _CHECKS}
        values["rrf_score"] = float(values["rrf_score"])
        values["similarity"] = float(values["similarity"])
        out.append(Evidence(**values))
    return out


def _secret(value) -> str | None:
    if value is None:
        return None
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return raw.strip() or None


class EvidenceSearch:
    def __init__(
        self, embedder: OpenAIEmbedder, store: SupabaseCorpus, keyword_index: KeywordIndexProvider | None = None
    ):
        self._embedder = embedder
        self._store = store
        self._keywords = keyword_index

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        openai_transport=None,
        supabase_transport=None,
        sleep: Callable[[float], None] | None = None,
        keyword_index: KeywordIndexProvider | None = None,
    ) -> EvidenceSearch:
        """Raises SearchUnavailable naming the first missing variable (the name only, never a value).
        `keyword_index`: the process's BM25 provider (v4 path); None keeps the v3 path."""
        openai_key = _secret(settings.openai_vector_key)
        url = (settings.supabase_url or "").strip() or None
        supabase_key = _secret(settings.supabase_secret_key)
        for name, value in (
            ("OPEN_AI_VECTOR_KEY", openai_key),
            ("SUPABASE_URL", url),
            ("SUPABASE_SECRET_KEY", supabase_key),
        ):
            if value is None:
                raise SearchUnavailable(f"Evidence search is not configured: set {name} in the repo-root .env")
        extra = {} if sleep is None else {"sleep": sleep}
        embedder = OpenAIEmbedder(
            openai_key,
            model=settings.embed_model,
            dims=settings.embed_dims,
            transport=openai_transport,
            timeout_s=30,
            max_retries=2,
            **extra,
        )
        store = SupabaseCorpus(url, supabase_key, transport=supabase_transport, timeout_s=30, max_retries=2, **extra)
        return cls(embedder, store, keyword_index)

    def __repr__(self) -> str:
        return f"EvidenceSearch({self._embedder!r})"

    # --- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._embedder.close()
        self._store.close()

    def __enter__(self) -> EvidenceSearch:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    async def __aenter__(self) -> EvidenceSearch:
        return self

    async def __aexit__(self, *exc_info) -> None:
        self.close()

    # --- search ------------------------------------------------------------

    async def search(
        self,
        claim: str,
        k: int | None = None,
        *,
        per_doc: int | None = None,
        min_similarity: float | None = None,
        keyword_text: str | None = None,
        cancer: str | None = None,
    ) -> list[Evidence]:
        """The best passages for `claim`, best first: at most k (SQL default 8), at most per_doc (2) per document,
        none below min_similarity (0.35). The claim is embedded; the keyword list searches `keyword_text` if it is
        non-blank, else the claim, restricted to chunks tagged `cancer` if given. Raises ValueError (no calls made)
        for an empty claim or a bad override, and a SearchError subclass for any upstream failure."""
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError("claim is empty")
        if k is not None and (not _is_int(k) or k < 1):
            raise ValueError("k must be a positive integer")
        if per_doc is not None and (not _is_int(per_doc) or per_doc < 1):
            raise ValueError("per_doc must be a positive integer")
        if min_similarity is not None and (not _is_num(min_similarity) or not -1 <= min_similarity <= 1):
            raise ValueError("min_similarity must be between -1 and 1")
        if cancer is not None and (not isinstance(cancer, str) or not cancer.strip()):
            raise ValueError("cancer must be a non-empty string")
        if keyword_text is not None and not isinstance(keyword_text, str):
            raise ValueError("keyword_text must be a string")
        claim = claim.strip()
        text = keyword_text.strip() if keyword_text and keyword_text.strip() else claim
        return await anyio.to_thread.run_sync(
            self._search_sync, claim, text, k, per_doc, min_similarity, cancer.strip() if cancer else None
        )

    def _search_sync(self, claim: str, text: str, k, per_doc, min_similarity, cancer) -> list[Evidence]:
        try:
            [vector] = self._embedder.embed([claim])
        except EmbedAuthError:
            raise SearchUnavailable("OpenAI rejected the embeddings key: check OPEN_AI_VECTOR_KEY") from None
        except EmbedRateLimited:
            raise SearchRateLimited("OpenAI embeddings rate limit after retries") from None
        except EmbedError as e:
            raise SearchUpstreamError(f"OpenAI embeddings failed ({type(e).__name__})") from None
        except ValueError:
            raise ValueError("claim is too long to embed") from None

        rows = None
        index = self._keywords.get(self._store) if self._keywords is not None else None
        if index is not None:
            ids = [cid for cid, _ in index.search(text, BM25_CANDIDATES, cancer)]
            try:
                rows = self._rpc(
                    "match_chunks_fused",
                    lambda: self._store.match_chunks_fused(
                        vector, ids, k=k, per_doc=per_doc, min_similarity=min_similarity
                    ),
                )
            except _RpcMissing:
                self._keywords.disable("rpc_missing")
        if rows is None:
            rows = self._rpc(
                "match_chunks",
                lambda: self._store.match_chunks(
                    vector, text, k=k, per_doc=per_doc, min_similarity=min_similarity, cancer=cancer
                ),
            )
        return parse_rows(rows)

    @staticmethod
    def _rpc(name: str, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except SupabaseAuthError:
            raise SearchUnavailable("Supabase rejected the key: check SUPABASE_SECRET_KEY") from None
        except SupabaseRateLimited:
            raise SearchRateLimited("Supabase rate limit after retries") from None
        except SupabaseUnreachable:
            raise SearchUpstreamError("Supabase project unreachable or paused") from None
        except SupabaseRequestError as e:
            if e.code == RPC_MISSING and name == "match_chunks_fused":
                raise _RpcMissing from None
            raise SearchUpstreamError(f"Supabase {name} failed ({type(e).__name__})") from None
        except SupabaseError as e:
            raise SearchUpstreamError(f"Supabase {name} failed ({type(e).__name__})") from None


class _RpcMissing(Exception):
    """match_chunks_fused isn't deployed: use match_chunks."""
