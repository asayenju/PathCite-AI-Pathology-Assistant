"""Luna's search queries and the multi-query search (docs/PRD-rag-checker.md, "v2 (issue #51)").

A doctor's sentence is a poor search query ("Because it's HER2 negative, hormone-blocking pills won't work for you"
shares few words with guideline text about endocrine therapy). So before searching:

1. `generate_queries` asks the checker model (gpt-6-luna, strict JSON, the client's low reasoning effort) for 1-3
   short queries that would find guideline text confirming or refuting the general medical fact behind the claim.
   The answer is cleaned (whitespace folded, blanks, over-long and duplicate queries dropped) and capped at 3.
2. `plan_queries` puts the raw claim first, always, then the generated queries (a query equal to the claim is
   dropped). If generation fails for any `LLMError` (schema violations included) it falls back to the claim alone
   and logs only the exception type: a weaker search beats a failed check.
3. `multi_search` runs every query through `EvidenceSearch.search` concurrently and `merge_results` fuses them:
   reciprocal rank fusion over the per-query lists (score = sum of 1/(60 + rank), rank 1-based), deduplicated by
   chunk_id (the first object seen is kept), at most 2 chunks per doc_id, top k. Ties keep first appearance
   (query order, then rank). Each per-query list is the RPC's own result, with per_doc and min_similarity already
   applied there.

Privacy: the claim and context go to OpenAI (query generation) and, as the queries, to OpenAI embeddings and
Supabase. Nothing is logged but exception type names.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from app.check.llm import LLMError, LLMUpstreamError, OpenAIResponsesClient, Usage
from app.check.search import Evidence
from app.check.verify import MAX_CONTEXT_TURNS

logger = logging.getLogger("pathcite")

MAX_QUERIES = 3
MAX_QUERY_CHARS = 200
RRF_K = 60
PER_DOC = 2
SCHEMA_NAME = "search_queries"

QUERY_INSTRUCTIONS = """\
You write search queries for a medical evidence search engine (guidelines and paper abstracts about cancer).

What you get:
- CLAIM: one sentence a doctor said to a patient. It relies on a general medical fact.
- CONTEXT: up to two of the doctor's previous turns, oldest first. Use them only to work out what the claim refers \
to (for example, which cancer "it" means).

Write 1 to 3 short search queries (3 to 10 words each) that would find guideline text confirming or refuting the \
general medical fact the claim relies on.
- Name the disease, test, marker or treatment explicitly, using standard medical terms (for example "endocrine \
therapy" for "hormone-blocking pills", "Breslow thickness" rather than "how deep the spot was").
- Leave out patient-specific details (this patient's results, plans, feelings) and conversational words.
- Do not answer or judge the claim, and do not assume it is right or wrong: write neutral queries that would \
find either kind of evidence.
- Make each query cover a different angle (for example the marker itself, and the treatment it predicts).

Example: "Because it's HER2 negative, hormone-blocking pills won't work for you." -> \
["HER2 status and endocrine therapy response", "hormone receptor positive breast cancer endocrine therapy"]
"""

QUERY_SCHEMA: dict = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
    "additionalProperties": False,
}


class _QueriesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[StrictStr]


class Searcher(Protocol):
    async def search(self, claim: str, k: int | None = None) -> list[Evidence]: ...


_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _build_input(claim: str, context: Sequence[str]) -> str:
    recent = list(context)[-MAX_CONTEXT_TURNS:]
    parts = [f"CLAIM:\n{claim}", "CONTEXT:"]
    parts += [f"- {turn}" for turn in recent] if recent else ["(none)"]
    return "\n\n".join(parts)


async def generate_queries(
    client: OpenAIResponsesClient, claim: str, context: Sequence[str]
) -> tuple[list[str], Usage]:
    """Luna's cleaned queries (0-3) and the request's usage. Raises an LLMError subclass on any failure."""
    data, usage = await client.structured(
        instructions=QUERY_INSTRUCTIONS, input=_build_input(claim, context), schema=QUERY_SCHEMA, name=SCHEMA_NAME
    )
    try:
        answer = _QueriesOut.model_validate(data)
    except ValidationError:
        raise LLMUpstreamError("OpenAI returned a malformed answer: schema violation") from None
    out: list[str] = []
    seen: set[str] = set()
    for q in answer.queries:
        q = _clean(q)
        key = q.casefold()
        if not q or len(q) > MAX_QUERY_CHARS or key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) == MAX_QUERIES:
            break
    return out, usage


async def plan_queries(client: OpenAIResponsesClient, claim: str, context: Sequence[str]) -> list[str]:
    """The queries to search, raw claim first. Never raises an LLMError: falls back to [claim]."""
    try:
        generated, _ = await generate_queries(client, claim, context)
    except LLMError as exc:
        logger.warning("Search query generation failed, searching the claim alone: %s", type(exc).__name__)
        return [claim]
    claim_key = _clean(claim).casefold()
    return [claim, *(q for q in generated if q.casefold() != claim_key)]


def merge_results(
    per_query: Sequence[Sequence[Evidence]], k: int, *, per_doc: int = PER_DOC, rrf_k: int = RRF_K
) -> list[Evidence]:
    """Fuse per-query result lists (see module doc). Pure."""
    scores: dict[int, float] = {}
    first: dict[int, Evidence] = {}
    order: dict[int, int] = {}
    for results in per_query:
        for rank, e in enumerate(results, 1):
            scores[e.chunk_id] = scores.get(e.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            if e.chunk_id not in first:
                first[e.chunk_id] = e
                order[e.chunk_id] = len(order)
    ranked = sorted(scores, key=lambda cid: (-scores[cid], order[cid]))
    out: list[Evidence] = []
    per: dict[str, int] = {}
    for cid in ranked:
        e = first[cid]
        if per.get(e.doc_id, 0) >= per_doc:
            continue
        per[e.doc_id] = per.get(e.doc_id, 0) + 1
        out.append(e)
        if len(out) == k:
            break
    return out


async def multi_search(search: Searcher, queries: Sequence[str], k: int) -> list[Evidence]:
    """Search every query concurrently and merge. The first search error is raised (mapped by the endpoint)."""
    # return_exceptions so every search is awaited (no "exception was never retrieved" logging), then re-raise.
    results = await asyncio.gather(*(search.search(q, k) for q in queries), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return merge_results(results, k)
