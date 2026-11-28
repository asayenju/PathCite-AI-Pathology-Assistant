"""Luna's search plan and the multi-query search (docs/PRD-rag-checker.md, "v2 (issue #51)" and "v3 search speed
(issue #55)").

A doctor's sentence is a poor search query ("Because it's HER2 negative, hormone-blocking pills won't work for you"
shares few words with guideline text about endocrine therapy). So before searching:

1. `generate_plan` asks the checker model (gpt-6-luna, strict JSON, the client's low reasoning effort) for 1-3
   short queries that would find guideline text confirming or refuting the general medical fact behind the claim,
   2-5 distinctive `key_terms` for the full-text half of the search, and the `cancer` the claim is about (one of
   CANCERS, "unknown" when unsure or general). Queries are cleaned (whitespace folded, blanks, over-long and
   duplicate queries dropped) and capped at 3; key terms likewise (`clean_key_terms`: over 40 chars or a lone
   generic word like "cancer" dropped, cap 5). `generate_queries` is the queries alone.
2. `plan_search` builds the `SearchPlan`: the raw claim first, always, then the generated queries (a query equal to
   the claim is dropped); keyword text = the key terms joined with spaces, the same for every query; the cancer
   filter. Why: the claim's common words ("cancer", "spread") made the RPC's keyword list score thousands of chunks
   (1-9 s, statement timeouts on the free tier); a few distinctive terms and a cancer filter are 2-3x faster.
   Fallbacks: no key terms -> keyword text = the raw claim (the old behaviour); cancer "unknown" -> no filter;
   generation fails for any `LLMError` (schema violations included) -> the claim alone, its own keywords, no
   filter, logging only the exception type: a weaker search beats a failed check.
3. `multi_search` runs every query through `EvidenceSearch.search` concurrently (vector = that query's embedding,
   keyword text and cancer from the plan) and `merge_results` fuses them:
   reciprocal rank fusion over the per-query lists (score = sum of 1/(60 + rank), rank 1-based), deduplicated by
   chunk_id (the first object seen is kept), at most 2 chunks per doc_id, top k. Ties keep first appearance
   (query order, then rank). Each per-query list is the RPC's own result, with per_doc and min_similarity already
   applied there.

Privacy: the claim and context go to OpenAI (query generation) and, as the queries and key terms, to OpenAI
embeddings and Supabase. Nothing is logged but exception type names.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from app.check.llm import LLMError, LLMUpstreamError, OpenAIResponsesClient, Usage
from app.check.search import Evidence
from app.check.verify import MAX_CONTEXT_TURNS

logger = logging.getLogger("pathcite")

MAX_QUERIES = 3
MAX_QUERY_CHARS = 200
MAX_KEY_TERMS = 5
MAX_KEY_TERM_CHARS = 40
# The labels of `chunks.cancers` in the corpus, plus "unknown" (no filter).
CANCERS = ("colorectal", "breast", "melanoma", "lymphoma", "prostate", "thyroid", "unknown")
# A key term that is only one of these matches thousands of chunks, which is what key terms are meant to avoid.
GENERIC_TERMS = frozenset(
    "cancer cancers tumour tumours tumor tumors patient patients treatment treatments spread risk risks disease "
    "cell cells carcinoma".split()
)
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

Also give key_terms: 2 to 5 short, distinctive terms or phrases (1 to 4 words each) for a full-text keyword \
search: named tests, scores, staging terms, markers, drug names and abbreviations, for example "Breslow", "IPI", \
"PSA nadir", "Dukes C", "HER2", "pembrolizumab". NOT generic words such as cancer, tumour, patient, treatment, \
spread or risk: they match almost every passage.

And give cancer: the cancer the claim is about, from the claim and its context: one of "colorectal", "breast", \
"melanoma", "lymphoma", "prostate", "thyroid". Use "unknown" when you are unsure or the claim is about cancer in \
general.

Example: "Because it's HER2 negative, hormone-blocking pills won't work for you." -> \
queries ["HER2 status and endocrine therapy response", "hormone receptor positive breast cancer endocrine therapy"], \
key_terms ["HER2", "endocrine therapy", "hormone receptor"], cancer "breast"
"""

QUERY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"}},
        "key_terms": {"type": "array", "items": {"type": "string"}},
        "cancer": {"type": "string", "enum": list(CANCERS)},
    },
    "required": ["queries", "key_terms", "cancer"],
    "additionalProperties": False,
}


class _QueriesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[StrictStr]
    key_terms: list[StrictStr]
    cancer: Literal[CANCERS]  # type: ignore[valid-type]


@dataclass(frozen=True)
class SearchPlan:
    """What /check searches: every query (raw claim first) is embedded for the vector list; `keyword_text` (the key
    terms, or the raw claim) is the keyword query of every search; `cancer` filters the keyword list (None: none)."""

    queries: list[str]
    keyword_text: str
    cancer: str | None = None
    key_terms: list[str] = field(default_factory=list, compare=False)


class Searcher(Protocol):
    async def search(
        self, claim: str, k: int | None = None, *, keyword_text: str | None = None, cancer: str | None = None
    ) -> list[Evidence]: ...


_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _build_input(claim: str, context: Sequence[str]) -> str:
    recent = list(context)[-MAX_CONTEXT_TURNS:]
    parts = [f"CLAIM:\n{claim}", "CONTEXT:"]
    parts += [f"- {turn}" for turn in recent] if recent else ["(none)"]
    return "\n\n".join(parts)


def _dedupe(items: Sequence[str], max_chars: int, cap: int, skip: frozenset[str] = frozenset()) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = _clean(item)
        key = item.casefold()
        if not item or len(item) > max_chars or key in seen or key in skip:
            continue
        seen.add(key)
        out.append(item)
        if len(out) == cap:
            break
    return out


def clean_key_terms(terms: Sequence[str]) -> list[str]:
    """Whitespace folded; blank, duplicate (case-insensitive), over-40-char and lone generic terms dropped; at most 5."""
    return _dedupe(terms, MAX_KEY_TERM_CHARS, MAX_KEY_TERMS, GENERIC_TERMS)


async def generate_plan(
    client: OpenAIResponsesClient, claim: str, context: Sequence[str]
) -> tuple[tuple[list[str], list[str], str | None], Usage]:
    """((queries 0-3, key terms 0-5, cancer or None for "unknown"), usage), all cleaned. Raises an LLMError subclass
    on any failure."""
    data, usage = await client.structured(
        instructions=QUERY_INSTRUCTIONS, input=_build_input(claim, context), schema=QUERY_SCHEMA, name=SCHEMA_NAME
    )
    try:
        answer = _QueriesOut.model_validate(data)
    except ValidationError:
        raise LLMUpstreamError("OpenAI returned a malformed answer: schema violation") from None
    queries = _dedupe(answer.queries, MAX_QUERY_CHARS, MAX_QUERIES)
    cancer = None if answer.cancer == "unknown" else answer.cancer
    return (queries, clean_key_terms(answer.key_terms), cancer), usage


async def generate_queries(
    client: OpenAIResponsesClient, claim: str, context: Sequence[str]
) -> tuple[list[str], Usage]:
    """Luna's cleaned queries (0-3) and the request's usage. Raises an LLMError subclass on any failure."""
    (queries, _, _), usage = await generate_plan(client, claim, context)
    return queries, usage


def build_plan(claim: str, queries: Sequence[str], key_terms: Sequence[str], cancer: str | None) -> SearchPlan:
    """Raw claim first, then the queries that differ from it; keyword text = the key terms, or the claim if none."""
    claim_key = _clean(claim).casefold()
    terms = list(key_terms)
    return SearchPlan(
        queries=[claim, *(q for q in queries if q.casefold() != claim_key)],
        keyword_text=" ".join(terms) if terms else claim,
        cancer=cancer,
        key_terms=terms,
    )


async def plan_search(client: OpenAIResponsesClient, claim: str, context: Sequence[str]) -> SearchPlan:
    """The search plan (see module doc). Never raises an LLMError: falls back to the claim alone, no filter."""
    try:
        (queries, key_terms, cancer), _ = await generate_plan(client, claim, context)
    except LLMError as exc:
        logger.warning("Search query generation failed, searching the claim alone: %s", type(exc).__name__)
        return build_plan(claim, [], [], None)
    return build_plan(claim, queries, key_terms, cancer)


async def plan_queries(client: OpenAIResponsesClient, claim: str, context: Sequence[str]) -> list[str]:
    """The queries of `plan_search`, raw claim first. Never raises an LLMError: falls back to [claim]."""
    return (await plan_search(client, claim, context)).queries


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


async def multi_search(
    search: Searcher,
    queries: Sequence[str],
    k: int,
    *,
    keyword_text: str | None = None,
    cancer: str | None = None,
) -> list[Evidence]:
    """Search every query concurrently and merge. `keyword_text` and `cancer` (each sent only when set) are the same
    for every query. The first search error is raised (mapped by the endpoint)."""
    options = {name: v for name, v in (("keyword_text", keyword_text), ("cancer", cancer)) if v is not None}
    # return_exceptions so every search is awaited (no "exception was never retrieved" logging), then re-raise.
    results = await asyncio.gather(*(search.search(q, k, **options) for q in queries), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return merge_results(results, k)
