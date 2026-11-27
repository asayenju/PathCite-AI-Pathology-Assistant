"""Sync PostgREST client for the corpus tables and RPCs (supabase/migrations/20261127000000_corpus.sql).

Uses the secret key (`sb_secret_...`, role service_role, which bypasses RLS). New Supabase keys are not JWTs, so the
key goes in the `apikey` header only (see scripts/verify_supabase_schema.py and supabase/README.md).

- Retries: 429, 502/503/504, Cloudflare-style 52x/540, timeouts and network errors back off exponentially
  (injectable sleep), bounded by `max_retries`.
- Errors: a project that is paused or unreachable (network errors, gateway 5xx, HTML instead of JSON) raises
  `SupabaseUnreachable` with a "resume it in the dashboard" message; 401/403 raises `SupabaseAuthError`; anything else
  `SupabaseRequestError` with the status and PostgREST's error code/message (truncated).

Privacy: the key, request bodies (chunk text, embeddings) and response bodies are never logged or put in exception
messages; httpx errors are raised `from None` so their request isn't chained.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0
PAGE_SIZE = 1000
"""PostgREST caps a response at `max-rows` (1000 on Supabase); pages are read until one comes back empty, so a lower
server cap still reads everything."""
RETRY_STATUSES = frozenset({429, 502, 503, 504, 520, 521, 522, 523, 524, 530, 540, 544, 546})
UNREACHABLE = "Supabase project unreachable or paused — open the dashboard to resume it"
KEY_HINT = "check SUPABASE_SECRET_KEY in the repo-root .env (the secret key, not the publishable one)"
MAX_DETAIL = 200


class SupabaseError(Exception):
    """Base class. Messages never contain the key, chunk text or embeddings."""


class SupabaseUnreachable(SupabaseError):
    pass


class SupabaseAuthError(SupabaseError):
    pass


class SupabaseRequestError(SupabaseError):
    pass


class SupabaseRateLimited(SupabaseRequestError):
    """429 still after retries (a SupabaseRequestError, so existing callers are unaffected)."""


def _backoff(attempt: int) -> float:
    return min(BACKOFF_BASE_S * 2**attempt, BACKOFF_MAX_S)


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if isinstance(body, dict) and ("code" in body or "message" in body):
        msg = str(body.get("message") or "")[:MAX_DETAIL]
        return f"HTTP {r.status_code} {body.get('code')}: {msg}"
    return f"HTTP {r.status_code}"


def _is_html(r: httpx.Response) -> bool:
    return "html" in r.headers.get("content-type", "").lower()


class SupabaseCorpus:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        timeout_s: float = 120,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
        page_size: int = PAGE_SIZE,
    ):
        self._sleep = sleep
        self._max_retries = max_retries
        self._page_size = page_size
        self._http = httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers={"apikey": key, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
        )

    def __repr__(self) -> str:
        return "SupabaseCorpus()"

    def __enter__(self) -> SupabaseCorpus:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # --- RPCs --------------------------------------------------------------

    def ingest_document(self, doc: dict, chunks: list[dict]) -> int:
        """Upserts the document and replaces all its chunks in one transaction. Returns the chunks written."""
        return int(self._json("POST", "/rpc/ingest_document", json={"doc": doc, "chunks": chunks}))

    def corpus_stats(self) -> dict:
        return self._json("POST", "/rpc/corpus_stats", json={})

    def match_chunks(self, query_embedding: list[float], query_text: str, **overrides: Any) -> Any:
        """Hybrid search (supabase/migrations/20261127010000_match_chunks.sql); returns the raw rows.

        The embedding goes as a JSON string ("[0.1,...]"), which PostgREST casts to vector(1536). Only the overrides
        given (k, candidates, per_doc, rrf_k, min_similarity) are sent; the rest take the SQL defaults.

        Cold start: the first call after the project has been idle sometimes gets a JSON HTTP 500 from PostgREST
        (likely a statement timeout, 57014) and then succeeds. It's a read, so one such 5xx is retried here; other
        calls don't retry a JSON 5xx because it is usually a real error."""
        body = {"query_embedding": json.dumps(query_embedding, separators=(",", ":")), "query_text": query_text}
        body.update({name: value for name, value in overrides.items() if value is not None})
        return self._json("POST", "/rpc/match_chunks", json=body, retry_json_5xx=1)

    # --- corpus_meta -------------------------------------------------------

    def read_meta(self) -> dict[str, Any]:
        rows = self._json("GET", "/corpus_meta", params={"select": "key,value"})
        return {row["key"]: row["value"] for row in rows}

    def write_meta(self, values: dict[str, Any]) -> None:
        self._request(
            "POST",
            "/corpus_meta",
            json=[{"key": k, "value": v} for k, v in values.items()],
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    # --- reads -------------------------------------------------------------

    def existing_hashes(self) -> dict[str, list[str]]:
        """doc_id -> its stored chunks' content_hash, in `ord` order."""
        out: dict[str, list[str]] = {}
        for row in self._paged("/chunks", "doc_id,ord,content_hash", "doc_id.asc,ord.asc"):
            out.setdefault(row["doc_id"], []).append(row["content_hash"])
        return out

    def document_ids(self) -> list[str]:
        return [row["id"] for row in self._paged("/documents", "id", "id.asc")]

    def chunk_keys(self) -> list[tuple[str, int]]:
        """Every stored (doc_id, ord), for duplicate and orphan checks."""
        return [(r["doc_id"], r["ord"]) for r in self._paged("/chunks", "doc_id,ord", "doc_id.asc,ord.asc")]

    def _paged(self, path: str, select: str, order: str) -> list[dict]:
        rows: list[dict] = []
        while True:
            page = self._json(
                "GET", path, params={"select": select, "order": order, "limit": self._page_size, "offset": len(rows)}
            )
            if not page:
                return rows
            rows.extend(page)

    # --- deletes -----------------------------------------------------------

    def delete_documents(self, ids: list[str]) -> None:
        """Their chunks go too (on delete cascade)."""
        for i in range(0, len(ids), 100):
            quoted = ",".join('"' + d.replace('"', '\\"') + '"' for d in ids[i : i + 100])
            self._request("DELETE", "/documents", params={"id": f"in.({quoted})"})

    def delete_all(self) -> None:
        """For --rebuild: every document (chunks cascade) and corpus_meta. PostgREST refuses an unfiltered delete,
        so the filter matches every row."""
        self._request("DELETE", "/chunks", params={"id": "gte.0"})
        self._request("DELETE", "/documents", params={"id": "not.is.null"})
        self._request("DELETE", "/corpus_meta", params={"key": "not.is.null"})

    # --- transport ---------------------------------------------------------

    def _json(self, method: str, path: str, *, retry_json_5xx: int = 0, **kwargs) -> Any:
        r = self._request(method, path, retry_json_5xx=retry_json_5xx, **kwargs)
        try:
            return r.json()
        except ValueError:
            raise SupabaseRequestError(f"Supabase returned a non-JSON body for {path}") from None

    def _request(self, method: str, path: str, *, retry_json_5xx: int = 0, **kwargs) -> httpx.Response:
        """`retry_json_5xx`: how many JSON (PostgREST) 5xx answers to retry, on top of the gateway statuses that
        are always retried; used only by match_chunks (see there)."""
        json_5xx_left = retry_json_5xx
        attempt = 0  # retries used by the always-retried failures; the JSON-5xx retries have their own budget
        while True:
            last = attempt >= self._max_retries
            try:
                r = self._http.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if not last:
                    self._sleep(_backoff(attempt))
                    attempt += 1
                    continue
                raise SupabaseUnreachable(f"{UNREACHABLE} ({type(e).__name__})") from None
            except httpx.HTTPError as e:
                raise SupabaseUnreachable(f"{UNREACHABLE} ({type(e).__name__})") from None

            code = r.status_code
            if code in RETRY_STATUSES or (code >= 500 and _is_html(r)):
                if not last:
                    self._sleep(_backoff(attempt))
                    attempt += 1
                    continue
                if code == 429:
                    raise SupabaseRateLimited(f"Supabase rate limit (HTTP 429) after retries on {path}")
                raise SupabaseUnreachable(f"{UNREACHABLE} (HTTP {code})")
            if code >= 500 and json_5xx_left > 0:
                json_5xx_left -= 1
                self._sleep(_backoff(0))
                continue
            if code in (401, 403):
                raise SupabaseAuthError(f"Supabase rejected the key ({_detail(r)}): {KEY_HINT}")
            if 200 <= code < 300:
                if _is_html(r):  # a proxy or the paused-project page, not PostgREST
                    raise SupabaseUnreachable(f"{UNREACHABLE} (got an HTML page)")
                return r
            raise SupabaseRequestError(f"{method} {path} failed: {_detail(r)}")
