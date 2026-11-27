"""POST /check: fact-check one doctor claim against the evidence corpus (docs/PRD-rag-checker.md, section 5).

Flow (v2, issue #51), sequential: Luna writes 1-3 search queries (queries.plan_queries; the raw claim is always
searched too, and a failure falls back to it alone) -> every query searched concurrently and merged by reciprocal
rank fusion (queries.multi_search) -> Luna judges each passage (verify.luna_stances, groundedness guard) -> Jev gives
one independent confidence over all passages (jev_check.jev_confidence; it never sees Luna's answer) -> code rules
(aggregate.py). Zero search results short-circuit to `unverified` with no judging or Jev call.

Jev is a confidence check, not a dependency: a missing JEV_KEY or any JevError keeps Luna's verdict with review
reason `jev_unavailable` and `confidence: null`. Search and Luna-judging failures are errors ({detail, code}).

Clients are built per request by factories on app.state (tests swap in fakes) and closed after use.

Privacy: the claim and context go to OpenAI (embedding + Luna), Supabase (keyword query) and TypeSafe (Jev). They,
the keys and bodies are never logged; only exception type names are.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AsyncExitStack

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.check.aggregate import aggregate
from app.check.jev_check import jev_confidence
from app.check.llm import LLMAuthError, LLMRateLimited, LLMUpstreamError, OpenAIResponsesClient
from app.check.queries import multi_search, plan_queries
from app.check.schemas import CheckConfidence, CheckRequest, CheckResponse, CheckSource
from app.check.search import EvidenceSearch, SearchRateLimited, SearchUnavailable, SearchUpstreamError
from app.check.verify import luna_stances
from app.config import Settings, get_settings
from app.errors import ApiError
from app.triage.jev import JevError
from app.triage.router import default_jev_client_factory

logger = logging.getLogger("pathcite")

INVALID = "invalid_check_request"
UNAVAILABLE = "check_unavailable"
RATE_LIMITED = "check_rate_limited"
UPSTREAM = "check_upstream_error"

# Verdicts whose Luna issue/suggestion are shown; accurate and unverified have nothing to correct.
_WITH_ISSUE = frozenset({"inaccurate", "incomplete", "disputed"})

_ERRORS: list[tuple[tuple[type[Exception], ...], int, str, str]] = [
    ((SearchUnavailable, LLMAuthError), 503, UNAVAILABLE, "The claim checker is not configured or a key was rejected"),
    ((SearchRateLimited, LLMRateLimited), 429, RATE_LIMITED, "The claim checker is rate-limited: try again shortly"),
    ((SearchUpstreamError, LLMUpstreamError), 502, UPSTREAM, "The claim checker did not get a usable answer"),
]


def default_search_factory(settings: Settings) -> EvidenceSearch:
    return EvidenceSearch.from_settings(settings)


def default_llm_factory(settings: Settings) -> OpenAIResponsesClient:
    return OpenAIResponsesClient.from_settings(settings)


class _CheckRoute(APIRoute):
    """Report body validation failures as invalid_check_request instead of the generic invalid_request."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Field locations only: pydantic's error entries also carry the input (the claim).
                fields = ", ".join(".".join(str(x) for x in e["loc"][1:]) or "body" for e in exc.errors())
                raise ApiError(422, INVALID, f"Invalid check request: {fields}") from None

        return wrapped


router = APIRouter(route_class=_CheckRoute)


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _factory(request: Request, name: str, default: Callable) -> Callable:
    return getattr(request.app.state, name, None) or default


def _has_jev_key(settings: Settings) -> bool:
    return settings.jev_key is not None and bool(settings.jev_key.get_secret_value().strip())


def _api_error(exc: Exception) -> ApiError:
    for classes, status, code, detail in _ERRORS:
        if isinstance(exc, classes):
            logger.warning("Claim check failed: %s", type(exc).__name__)
            return ApiError(status, code, detail)
    logger.error("Unexpected claim check error: %s", type(exc).__name__)
    return ApiError(502, UPSTREAM, "The claim checker did not get a usable answer")


async def _jev_or_none(jev, claim, context, chunks):
    if jev is None:
        return None
    try:
        return await jev_confidence(jev, claim, context, chunks)
    except JevError as exc:
        logger.warning("Jev check failed, using Luna alone: %s", type(exc).__name__)
        return None


async def _check(request: Request, body: CheckRequest, settings: Settings) -> CheckResponse:
    claim = body.quote.strip()
    context = body.context[-2:]
    async with AsyncExitStack() as stack:
        # Built up front so a missing OpenAI/Supabase key is a 503 whatever the search returns.
        search = await stack.enter_async_context(
            _factory(request, "check_search_factory", default_search_factory)(settings)
        )
        llm = await stack.enter_async_context(_factory(request, "check_llm_factory", default_llm_factory)(settings))
        jev = None
        if _has_jev_key(settings):
            try:
                jev = _factory(request, "check_jev_factory", default_jev_client_factory)(settings)
            except Exception as exc:  # noqa: BLE001 - Jev is optional
                logger.warning("Jev client unavailable, using Luna alone: %s", type(exc).__name__)
            else:
                jev = await stack.enter_async_context(jev)

        queries = await plan_queries(llm, claim, context)
        try:
            chunks = await multi_search(search, queries, settings.check_top_k)
        except ValueError:  # raised before any call, e.g. a claim too long to embed
            raise ApiError(422, INVALID, "Invalid check request: quote") from None
        model = getattr(llm, "model", None) or settings.checker_model
        if not chunks:
            return CheckResponse(
                verdict="unverified",
                issue=None,
                suggestion=None,
                needs_review=False,
                review_reasons=[],
                sources=[],
                search_queries=queries,
                confidence=None,
                model=model,
                jev_model=None,
            )

        luna = await luna_stances(llm, claim, context, chunks)
        jev_verdict = await _jev_or_none(jev, claim, context, chunks)
        jev_model = (getattr(jev, "model_used", None) or settings.jev_model) if jev_verdict is not None else None

    result = aggregate(luna.stances, luna.missing_qualifier, jev_verdict, settings.check_jev_min)
    by_id = {s.chunk_id: s for s in luna.stances}
    sources = []
    for chunk in chunks:
        s = by_id[chunk.chunk_id]
        grounded_quote = s.quote if s.grounded and s.stance in ("supports", "contradicts") else ""
        sources.append(
            CheckSource(
                chunk_id=chunk.chunk_id,
                title=chunk.title,
                section=chunk.section,
                url=chunk.url,
                trust=chunk.trust,
                year=chunk.year,
                quote=grounded_quote,
                stance=s.stance,
                luna_stance=s.stance,
            )
        )
    c = result.confidence
    show_issue = result.verdict in _WITH_ISSUE
    return CheckResponse(
        verdict=result.verdict,
        issue=luna.issue if show_issue else None,
        suggestion=luna.suggestion if show_issue else None,
        needs_review=result.needs_review,
        review_reasons=result.review_reasons,
        sources=sources,
        search_queries=queries,
        confidence=None if c is None else CheckConfidence(label=c.label, p=c.p, p_verdict=c.p_verdict, agrees=c.agrees),
        model=model,
        jev_model=jev_model,
    )


@router.post(
    "/check",
    response_model=CheckResponse,
    summary="Fact-check one doctor claim against the evidence corpus",
    description=(
        "GPT-6 Luna (OpenAI) writes 1-3 search queries; the claim and those queries search the NICE/PubMed/WikiDoc "
        "corpus and the results are merged. Luna judges every passage as supports / contradicts / irrelevant (with "
        "word-for-word quotes) and the verdict follows fixed rules: a mix of support and contradiction is "
        "`disputed`. Jev (TypeSafe) then gives one independent confidence over the passages; disagreement or low "
        "confidence sends the card to review. The claim and up to 2 previous doctor turns are sent to OpenAI, "
        "Supabase and TypeSafe; nothing is stored or logged. Research use only: not a medical device."
    ),
)
async def check(request: Request, body: CheckRequest) -> CheckResponse:
    if not body.quote.strip():
        raise ApiError(422, INVALID, "Invalid check request: quote is blank")
    settings = _settings(request)
    try:
        return await _check(request, body, settings)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to {detail, code}; only the type is logged
        raise _api_error(exc) from None
