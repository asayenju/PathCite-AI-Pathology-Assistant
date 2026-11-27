"""Thin async client for Jev, TypeSafe AI's decision model (POST /v1/systemone).

Jev returns typed answers only (choice probabilities, noul = P(yes)), never text. For each span
we ask a Choice (`kind_<i>`: small_talk vs medical_claim) and, optionally, a Noul (`asr_<i>`:
does the span contain a speech-to-text error?).

Privacy: request bodies, response bodies, transcript text and the key are never logged or put in
exception messages. Messages carry only a status code or an exception class name, and httpx
errors are raised `from None` so their request repr isn't chained.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

SMALL_TALK = "small_talk"
MEDICAL_CLAIM = "medical_claim"

KIND_CRITERIA = {
    SMALL_TALK: (
        "Not a general medical assertion: a greeting, thanks, empathy or reassurance; a question "
        "to the patient; scheduling or logistics; a plan or recommendation for this patient (a "
        "test, treatment, referral or follow-up, e.g. 'we'll do a CT next week'); or a "
        "patient-specific result or guess with no general assertion (e.g. 'your biopsy shows "
        "cancer cells', 'it's probably early')."
    ),
    MEDICAL_CLAIM: (
        "Asserts a general medical fact that published literature could confirm or refute. This "
        "includes a general fact used to justify a conclusion about the patient (e.g. 'because "
        "it's moderately differentiated, that tells us it hasn't spread'), a hedged general fact "
        "(e.g. 'this usually means...'), a rhetorical question that asserts a fact, and a myth "
        "or misconception mentioned in order to correct it."
    ),
}

LOW_CONFIDENCE_NOTE = (
    "Each span's low_confidence_words lists words the speech-to-text engine was unsure of. "
    "Treat them as a hint, not proof: a correct word can have low confidence."
)

RETRY_AFTER_DEFAULT_S = 1.0
RETRY_AFTER_MAX_S = 10.0


@dataclass(frozen=True)
class SpanInput:
    id: str
    text: str
    low_confidence_words: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class SpanAnswer:
    p_small_talk: float
    p_asr: float | None  # None when ask_asr=False


class JevError(Exception):
    """Base class. Messages never contain transcript text, bodies or the key."""


class JevAuthError(JevError):
    """401/403: the key was rejected (API code triage_unavailable)."""


class JevRateLimited(JevError):
    """429 still after retries (API code triage_rate_limited)."""


class JevUpstreamError(JevError):
    """Timeout, network error, 5xx, other 4xx or a malformed answer (triage_upstream_error)."""


def _malformed() -> JevUpstreamError:
    return JevUpstreamError("Jev returned a malformed answer")


def _prob(value: Any) -> float:
    # bool is an int subclass; reject it along with strings, None, NaN and out-of-range numbers.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _malformed()
    p = float(value)
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise _malformed()
    return p


prob = _prob  # public alias for app/check/jev_check.py


def _retry_after(response: httpx.Response) -> float:
    try:
        wait = float(response.headers.get("retry-after", RETRY_AFTER_DEFAULT_S))
    except ValueError:
        wait = RETRY_AFTER_DEFAULT_S
    if not math.isfinite(wait) or wait < 0:
        wait = RETRY_AFTER_DEFAULT_S
    return min(wait, RETRY_AFTER_MAX_S)


class JevClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str = "https://api.typesafe.ai",
        timeout_s: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
        max_retries: int = 2,
        max_spans_per_request: int = 40,
    ):
        if max_spans_per_request < 1:
            raise ValueError("max_spans_per_request must be >= 1")
        self.model = model
        self.model_used: str | None = None
        self._sleep = sleep
        self._max_retries = max_retries
        self._max_spans = max_spans_per_request
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def __repr__(self) -> str:
        return f"JevClient(model={self.model!r})"

    async def __aenter__(self) -> JevClient:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def classify_spans(
        self,
        turn_text: str,
        previous_doctor_turns: Sequence[str],
        spans: Sequence[SpanInput],
        *,
        ask_asr: bool = True,
    ) -> list[SpanAnswer]:
        """Classify each span of one doctor turn. Answers come back in the order of `spans`."""
        out: list[SpanAnswer] = []
        for start in range(0, len(spans), self._max_spans):
            batch = spans[start : start + self._max_spans]
            body = self._build_request(turn_text, previous_doctor_turns, batch, ask_asr)
            data = await self._post(body)
            out.extend(self._parse(data, len(batch), ask_asr))
        return out

    async def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, Any]:
        """Send one raw Jev request (same transport, retries and errors as classify_spans) and return its `answers`
        dict, unvalidated beyond being a dict. Records the answering model in `model_used`. Used by the claim
        checker (app/check/jev_check.py); the caller validates each answer."""
        data = await self._post({"model": self.model, "state": state, "questions": questions})
        if not isinstance(data, dict):
            raise _malformed()
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise _malformed()
        model = data.get("model")
        if isinstance(model, str):
            self.model_used = model
        return answers

    # --- request -----------------------------------------------------------

    def _build_request(
        self,
        turn_text: str,
        previous_doctor_turns: Sequence[str],
        spans: Sequence[SpanInput],
        ask_asr: bool,
    ) -> dict:
        state = {
            "turn": turn_text,
            "previous_doctor_turns": list(previous_doctor_turns),
            "spans": [
                {
                    "id": s.id,
                    "text": s.text,
                    "low_confidence_words": [
                        {"word": w, "confidence": c} for w, c in s.low_confidence_words
                    ],
                }
                for s in spans
            ],
        }
        questions: dict[str, dict] = {}
        for i, s in enumerate(spans):
            questions[f"kind_{i}"] = {
                "type": "choice",
                "instructions": (
                    f"Classify span {s.id} of the doctor's turn: '{s.text}'. Judge only what this "
                    "span asserts; the turn and previous doctor turns are context."
                ),
                "criteria": KIND_CRITERIA,
            }
            if ask_asr:
                questions[f"asr_{i}"] = {
                    "type": "noul",
                    "instructions": (
                        f"Does span {s.id} ('{s.text}') contain a word that speech-to-text likely "
                        "got wrong: a non-word, a misspelled or out-of-place medical term, or a "
                        "homophone that makes no sense here? " + LOW_CONFIDENCE_NOTE
                    ),
                }
        return {"model": self.model, "state": state, "questions": questions}

    async def _post(self, body: dict) -> Any:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.post("/v1/systemone", json=body)
            except httpx.HTTPError as e:
                raise JevUpstreamError(f"Jev request failed: {type(e).__name__}") from None

            code = response.status_code
            if code == 429:
                if attempt < self._max_retries:
                    await self._sleep(_retry_after(response))
                    continue
                raise JevRateLimited("Jev rate limit (HTTP 429)")
            if code in (401, 403):
                raise JevAuthError(f"Jev rejected the key (HTTP {code})")
            if code != 200:
                raise JevUpstreamError(f"Jev returned HTTP {code}")
            try:
                return response.json()
            except ValueError:
                raise JevUpstreamError("Jev returned a non-JSON body") from None
        raise AssertionError("unreachable")  # pragma: no cover

    # --- response ----------------------------------------------------------

    def _parse(self, data: Any, n: int, ask_asr: bool) -> list[SpanAnswer]:
        if not isinstance(data, dict):
            raise _malformed()
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise _malformed()
        model = data.get("model")
        if isinstance(model, str):
            self.model_used = model

        out = []
        for i in range(n):
            kind = answers.get(f"kind_{i}")
            if not isinstance(kind, dict) or kind.get("type") != "choice":
                raise _malformed()
            probs = kind.get("probabilities")
            if not isinstance(probs, dict) or SMALL_TALK not in probs or MEDICAL_CLAIM not in probs:
                raise _malformed()
            p_small = _prob(probs[SMALL_TALK])
            _prob(probs[MEDICAL_CLAIM])

            p_asr = None
            if ask_asr:
                asr = answers.get(f"asr_{i}")
                if not isinstance(asr, dict) or asr.get("type") != "noul" or "noul" not in asr:
                    raise _malformed()
                p_asr = _prob(asr["noul"])
            out.append(SpanAnswer(p_small_talk=p_small, p_asr=p_asr))
        return out
