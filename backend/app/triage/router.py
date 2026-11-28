"""POST /triage (NDJSON stream of span labels per doctor turn) and POST /triage/span (one typed correction).

Errors before the stream starts are the usual JSON {detail, code}; once it has started, an error arrives as a final
{"type": "error", "code"} line. Transcript text and the key are never logged: only exception type names are.
"""

import json
import logging
import re
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

from app.config import Settings, get_settings
from app.errors import ApiError
from app.triage.decide import decide
from app.triage.jev import JevAuthError, JevClient, JevRateLimited, JevUpstreamError, SpanInput
from app.triage.schemas import TriageRequest, TriageSpanRequest, TriageSpanResponse, TriageTurn, TriageWord
from app.triage.segment import split_spans

logger = logging.getLogger("pathcite")

INVALID = "invalid_triage_request"
UNAVAILABLE = "triage_unavailable"
RATE_LIMITED = "triage_rate_limited"
UPSTREAM = "triage_upstream_error"

PREVIOUS_TURNS = 2

# (status, code, detail) for each Jev failure; anything else is treated as an upstream error.
_JEV_ERRORS: list[tuple[type[Exception], int, str, str]] = [
    (JevAuthError, 503, UNAVAILABLE, "Jev rejected the key: check JEV_KEY"),
    (JevRateLimited, 429, RATE_LIMITED, "Jev is rate-limiting requests: try again shortly"),
    (JevUpstreamError, 502, UPSTREAM, "Jev did not return a usable answer"),
]
_UNEXPECTED = (502, UPSTREAM, "Jev did not return a usable answer")

_EDGE_PUNCT = re.compile(r"^[\W_]+|[\W_]+$")


def default_jev_client_factory(settings: Settings) -> JevClient:
    return JevClient(
        settings.jev_key.get_secret_value(),
        model=settings.jev_model,
        base_url=settings.jev_base_url,
        timeout_s=settings.jev_timeout_s,
    )


class _TriageRoute(APIRoute):
    """Report body validation failures as invalid_triage_request instead of the generic invalid_request."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Field locations only: pydantic's error entries also carry the input, which may be transcript text.
                fields = ", ".join(".".join(str(x) for x in e["loc"][1:]) or "body" for e in exc.errors())
                raise ApiError(422, INVALID, f"Invalid triage request: {fields}") from None

        return wrapped


router = APIRouter(route_class=_TriageRoute)


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _factory(request: Request) -> Callable[[Settings], JevClient]:
    return getattr(request.app.state, "jev_client_factory", None) or default_jev_client_factory


def _require_key(settings: Settings) -> None:
    if settings.jev_key is None or not settings.jev_key.get_secret_value():
        raise ApiError(503, UNAVAILABLE, "Set JEV_KEY in .env")


def _error_info(exc: Exception) -> tuple[int, str, str]:
    for cls, status, code, detail in _JEV_ERRORS:
        if isinstance(exc, cls):
            logger.warning("Jev triage failed: %s", type(exc).__name__)
            return status, code, detail
    logger.error("Unexpected triage error: %s", type(exc).__name__)
    return _UNEXPECTED


def _low_confidence_words(words: list[TriageWord], span_text: str, threshold: float) -> tuple[tuple[str, float], ...]:
    """Words below `threshold` whose text (edge punctuation stripped, case-insensitive) occurs as a whole word in the span."""
    lowered = span_text.lower()
    out: list[tuple[str, float]] = []
    for w in words:
        if w.confidence is None or w.confidence >= threshold:
            continue
        word = _EDGE_PUNCT.sub("", w.text)
        if not word:
            continue
        if re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", lowered) and (word, w.confidence) not in out:
            out.append((word, w.confidence))
    return tuple(out)


def _line(event: dict) -> str:
    return json.dumps(event) + "\n"


async def _triage_turn(client, turn: TriageTurn, previous: list[str], settings: Settings) -> list[dict]:
    spans = split_spans(turn.text)
    to_send = [
        (i, SpanInput(str(i), turn.text[s.start : s.end], _low_confidence_words(turn.words, turn.text[s.start : s.end], settings.asr_low_confidence)))
        for i, s in enumerate(spans)
        if not s.local_skip
    ]
    decisions = {}
    if to_send:
        answers = await client.classify_spans(turn.text, previous, [inp for _, inp in to_send])
        if len(answers) != len(to_send):
            raise JevUpstreamError("Jev answered a different number of spans")
        for (i, _), a in zip(to_send, answers):
            decisions[i] = decide(a.p_small_talk, a.p_asr, asr_flag=settings.triage_asr_flag, skip_min=settings.triage_skip_min)

    events = []
    for i, s in enumerate(spans):
        d = decisions.get(i)
        events.append(
            {
                "type": "span",
                "turn_id": turn.id,
                "start": s.start,
                "end": s.end,
                "quote": turn.text[s.start : s.end],
                "kind": d.kind if d else "skip",
                "base_kind": d.base_kind if d else "skip",
                "p_claim": d.p_claim if d else 0.0,
                "p_asr": d.p_asr if d else None,
            }
        )
    return events


async def _triage_stream(request: Request, body: TriageRequest, settings: Settings) -> AsyncIterator[str]:
    factory = _factory(request)
    try:
        async with factory(settings) as client:
            for n, turn in enumerate(body.turns):
                if await request.is_disconnected():
                    return
                yield _line({"type": "turn_started", "turn_id": turn.id})
                previous = [t.text for t in body.turns[max(0, n - PREVIOUS_TURNS) : n]]
                for event in await _triage_turn(client, turn, previous, settings):
                    yield _line(event)
                yield _line({"type": "turn_done", "turn_id": turn.id})
            model = getattr(client, "model_used", None) or settings.jev_model
        yield _line({"type": "done", "model": model})
    except Exception as exc:  # noqa: BLE001 - every failure ends the stream with an error event
        _, code, _ = _error_info(exc)
        yield _line({"type": "error", "code": code})


@router.post(
    "/triage",
    summary="Triage doctor turns into small talk, medical claims and possible transcription errors",
    description=(
        "Splits each doctor turn into sentence spans and labels each one with Jev (TypeSafe AI): `skip`, `claim` "
        "or `transcription`. Streams NDJSON, per turn in request order: `turn_started`, one `span` per span, "
        "`turn_done`; then `done` with the Jev model. Filler spans and short content-free replies (\"Nobody "
        "does.\") are skipped locally and never sent. An error after the stream has started arrives as a final `{\"type\": \"error\", \"code\"}` line. Send doctor turns "
        "only: their text is sent to TypeSafe. Nothing is stored or logged. Research use only: not a medical device."
    ),
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {}}, "description": "NDJSON event stream"}},
)
async def triage(request: Request, body: TriageRequest) -> StreamingResponse:
    settings = _settings(request)
    if not body.turns:
        raise ApiError(422, INVALID, "Invalid triage request: no turns")
    if len(body.turns) > settings.triage_max_turns:
        raise ApiError(422, INVALID, f"Invalid triage request: more than {settings.triage_max_turns} turns")
    if any(len(t.text) > settings.triage_max_turn_chars for t in body.turns):
        raise ApiError(
            422, INVALID, f"Invalid triage request: a turn is over {settings.triage_max_turn_chars} characters"
        )
    if len({t.id for t in body.turns}) != len(body.turns):
        raise ApiError(422, INVALID, "Invalid triage request: duplicate turn ids")
    _require_key(settings)
    return StreamingResponse(_triage_stream(request, body, settings), media_type="application/x-ndjson")


@router.post(
    "/triage/span",
    response_model=TriageSpanResponse,
    summary="Classify one typed correction as small talk or a medical claim",
    description=(
        "For a doctor's typed correction of a possible transcription error. The whole text is one span; only the "
        "small-talk/claim choice is asked. `kind` is `skip` or `claim`. Nothing is stored or logged. Research use "
        "only: not a medical device."
    ),
)
async def triage_span(request: Request, body: TriageSpanRequest) -> TriageSpanResponse:
    settings = _settings(request)
    if not body.text.strip():
        raise ApiError(422, INVALID, "Invalid triage request: text is blank")
    if len(body.text) > settings.triage_max_turn_chars:
        raise ApiError(422, INVALID, f"Invalid triage request: text is over {settings.triage_max_turn_chars} characters")
    _require_key(settings)
    try:
        async with _factory(request)(settings) as client:
            answers = await client.classify_spans(
                body.text, body.previous_doctor_turns[-PREVIOUS_TURNS:], [SpanInput("0", body.text)], ask_asr=False
            )
        if len(answers) != 1:
            raise JevUpstreamError("Jev answered a different number of spans")
        a = answers[0]
        d = decide(a.p_small_talk, None, asr_flag=settings.triage_asr_flag, skip_min=settings.triage_skip_min)
    except Exception as exc:  # noqa: BLE001 - mapped to {detail, code}; only the type is logged
        status, code, detail = _error_info(exc)
        raise ApiError(status, code, detail) from None
    return TriageSpanResponse(kind=d.base_kind, p_claim=d.p_claim)
