"""End-to-end tests for POST /triage (NDJSON stream) and POST /triage/span, with a fake Jev client."""

import builtins
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.main import app
from app.triage.jev import JevAuthError, JevClient, JevRateLimited, JevUpstreamError, SpanAnswer

KEY = "sk-test-SECRET-KEY-0042"
SECRET_TEXT = "Zorblaxian carcinoma always spreads to the spleen."  # must never be logged


class FakeJev:
    """Scripted stand-in for JevClient. Records every call; can raise on the n-th call (1-based)."""

    def __init__(self, answer=None, fail_on_call=None, error=None, model_used="jev-1.14.0"):
        self.answer = answer or default_answer
        self.fail_on_call = fail_on_call
        self.error = error
        self.model_used = model_used
        self.calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def classify_spans(self, turn_text, previous_doctor_turns, spans, *, ask_asr=True):
        self.calls.append(
            {"turn": turn_text, "previous": list(previous_doctor_turns), "spans": list(spans), "ask_asr": ask_asr}
        )
        if self.fail_on_call == len(self.calls):
            raise self.error
        return [self.answer(s.text, ask_asr) for s in spans]


def default_answer(text, ask_asr=True):
    if "colin" in text:
        p_small, p_asr = 0.1, 0.9
    elif text.startswith(("Good morning", "Please")):
        p_small, p_asr = 0.95, 0.02
    else:
        p_small, p_asr = 0.2, 0.05
    return SpanAnswer(p_small_talk=p_small, p_asr=p_asr if ask_asr else None)


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Triage never needs the image model; running without it also proves the endpoints work degraded.
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.pt"))
    with TestClient(app) as c:
        yield c


def configure(c, fake=None, key=KEY, **settings):
    state = c.app.state
    update = {"jev_key": SecretStr(key) if key else None, **settings}
    state.settings = state.settings.model_copy(update=update)
    if fake is not None:
        state.jev_client_factory = lambda s: fake
    return fake


def events(r):
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.text.endswith("\n")
    return [json.loads(line) for line in r.text.splitlines()]


def turn(id, text, words=None):
    t = {"id": id, "text": text}
    if words is not None:
        t["words"] = words
    return t


# ---- happy path: event order and content ----


def test_event_order_and_content(client):
    fake = configure(client, FakeJev(model_used="jev-1.14.0-served"))
    t1 = "Good morning, please have a seat. Moderately differentiated tumours rarely spread."
    t2 = "The growth in your sigmoid colin is small."
    r = client.post("/triage", json={"turns": [turn("t1", t1), turn("t2", t2)]})
    assert r.status_code == 200
    ev = events(r)
    assert [e["type"] for e in ev] == [
        "turn_started", "span", "span", "turn_done", "turn_started", "span", "turn_done", "done",
    ]  # fmt: skip
    assert ev[0] == {"type": "turn_started", "turn_id": "t1"}
    s1, s2 = ev[1], ev[2]
    assert s1 == {
        "type": "span", "turn_id": "t1", "start": 0, "end": 33, "quote": "Good morning, please have a seat.",
        "kind": "skip", "base_kind": "skip", "p_claim": pytest.approx(0.05), "p_asr": 0.02,
    }  # fmt: skip
    assert s2["kind"] == "claim" and s2["base_kind"] == "claim" and s2["p_claim"] == pytest.approx(0.8)
    assert t1[s2["start"] : s2["end"]] == s2["quote"] == "Moderately differentiated tumours rarely spread."
    assert ev[3] == {"type": "turn_done", "turn_id": "t1"}
    s3 = ev[5]
    assert s3["turn_id"] == "t2" and s3["kind"] == "transcription" and s3["base_kind"] == "claim"
    assert s3["p_asr"] == 0.9 and s3["quote"] == t2
    assert ev[-1] == {"type": "done", "model": "jev-1.14.0-served"}
    assert len(fake.calls) == 2 and fake.closed
    assert all(c["ask_asr"] for c in fake.calls)


def test_done_model_falls_back_to_configured_model(client):
    configure(client, FakeJev(model_used=None), jev_model="jev-pinned")
    ev = events(client.post("/triage", json={"turns": [turn("t1", "Polyps can become cancer.")]}))
    assert ev[-1] == {"type": "done", "model": "jev-pinned"}


def test_settings_thresholds_are_used(client):
    configure(client, FakeJev(), triage_skip_min=0.99, triage_asr_flag=0.95)
    ev = events(client.post("/triage", json={"turns": [turn("t1", "Good morning. Your sigmoid colin.")]}))
    spans = [e for e in ev if e["type"] == "span"]
    assert [s["kind"] for s in spans] == ["claim", "claim"]  # 0.95 < skip_min, 0.9 < asr_flag


# ---- turns that make no Jev call ----


def test_filler_spans_are_skipped_locally(client):
    fake = configure(client, FakeJev())
    text = "Um, okay. Polyps can become cancer. Mm-hmm."
    ev = events(client.post("/triage", json={"turns": [turn("t1", text)]}))
    spans = [e for e in ev if e["type"] == "span"]
    assert [s["quote"] for s in spans] == ["Um, okay.", "Polyps can become cancer.", "Mm-hmm."]
    for s in (spans[0], spans[2]):
        assert (s["kind"], s["base_kind"], s["p_claim"], s["p_asr"]) == ("skip", "skip", 0.0, None)
    assert spans[1]["kind"] == "claim"
    assert len(fake.calls) == 1
    assert [s.text for s in fake.calls[0]["spans"]] == ["Polyps can become cancer."]


@pytest.mark.parametrize("text", ["", "   ", "Um. Okay.", "Mm-hmm... uh-huh!"])
def test_empty_or_all_filler_turn_makes_no_call(client, text):
    fake = configure(client, FakeJev())
    ev = events(client.post("/triage", json={"turns": [turn("t1", text)]}))
    assert [e["type"] for e in ev if e["type"] != "span"] == ["turn_started", "turn_done", "done"]
    assert all(e["kind"] == "skip" for e in ev if e["type"] == "span")
    assert fake.calls == []


# ---- what is sent to Jev ----


def test_span_ids_and_turn_text_sent(client):
    fake = configure(client, FakeJev())
    text = "Um. Polyps can become cancer. Fibre lowers the risk."
    client.post("/triage", json={"turns": [turn("t1", text)]})
    call = fake.calls[0]
    assert call["turn"] == text
    assert [(s.id, s.text) for s in call["spans"]] == [("1", "Polyps can become cancer."), ("2", "Fibre lowers the risk.")]


def test_previous_doctor_turns_are_up_to_two_earlier_turns(client):
    fake = configure(client, FakeJev())
    texts = ["Polyps can grow.", "Fibre helps.", "Smoking raises risk.", "Age matters."]
    client.post("/triage", json={"turns": [turn(f"t{i}", t) for i, t in enumerate(texts)]})
    assert [c["previous"] for c in fake.calls] == [
        [], ["Polyps can grow."], ["Polyps can grow.", "Fibre helps."], ["Fibre helps.", "Smoking raises risk."],
    ]  # fmt: skip


def test_low_confidence_word_selection(client):
    fake = configure(client, FakeJev(), asr_low_confidence=0.6)
    text = "The growth in your sigmoid colin is small. Colon cancer is common."
    words = [
        {"text": "sigmoid", "confidence": 0.55},
        {"text": "colin", "confidence": 0.31},
        {"text": "Colon", "confidence": 0.59},
        {"text": "small.", "confidence": 0.61},  # not below the threshold
        {"text": "common.", "confidence": None},  # no confidence from the vendor
        {"text": "spleen", "confidence": 0.1},  # not in either span
        {"text": "...", "confidence": 0.2},  # nothing left after stripping punctuation
    ]
    client.post("/triage", json={"turns": [turn("t1", text, words)]})
    s1, s2 = fake.calls[0]["spans"]
    assert s1.low_confidence_words == (("sigmoid", 0.55), ("colin", 0.31))
    # Punctuation stripped and compared case-insensitively: "Colon" matches; "colin" isn't in span 2.
    assert s2.low_confidence_words == (("Colon", 0.59),)


def test_low_confidence_match_is_whole_word(client):
    fake = configure(client, FakeJev())
    client.post("/triage", json={"turns": [turn("t1", "Colonoscopy finds polyps.", [{"text": "colon,", "confidence": 0.2}])]})
    assert fake.calls[0]["spans"][0].low_confidence_words == ()


def test_many_spans_go_in_one_classify_call(client):
    fake = configure(client, FakeJev())
    text = " ".join(f"Fact number {i} is true." for i in range(50))
    ev = events(client.post("/triage", json={"turns": [turn("t1", text)]}))
    assert len(fake.calls) == 1 and len(fake.calls[0]["spans"]) == 50  # the client does the batching
    assert len([e for e in ev if e["type"] == "span"]) == 50


def test_batching_passthrough_with_real_client(client):
    """The real JevClient splits 50 spans into 40 + 10; the stream still has 50 spans in order."""
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        n = len(body["state"]["spans"])
        answers = {}
        for i in range(n):
            answers[f"kind_{i}"] = {"type": "choice", "probabilities": {"small_talk": 0.1, "medical_claim": 0.9}}
            answers[f"asr_{i}"] = {"type": "noul", "noul": 0.0}
        return httpx.Response(200, json={"model": "jev-1.14.0", "answers": answers})

    configure(client)
    client.app.state.jev_client_factory = lambda s: JevClient(
        s.jev_key.get_secret_value(), model=s.jev_model, transport=httpx.MockTransport(handler)
    )
    text = " ".join(f"Fact number {i} is true." for i in range(50))
    ev = events(client.post("/triage", json={"turns": [turn("t1", text)]}))
    assert [len(b["state"]["spans"]) for b in bodies] == [40, 10]
    spans = [e for e in ev if e["type"] == "span"]
    assert [s["quote"] for s in spans] == [f"Fact number {i} is true." for i in range(50)]
    assert ev[-1] == {"type": "done", "model": "jev-1.14.0"}


def test_answer_count_mismatch_is_upstream_error(client):
    class Short(FakeJev):
        async def classify_spans(self, *a, **k):
            return (await super().classify_spans(*a, **k))[:-1]

    configure(client, Short())
    ev = events(client.post("/triage", json={"turns": [turn("t1", "Polyps grow. Fibre helps.")]}))
    assert ev[-1] == {"type": "error", "code": "triage_upstream_error"}


# ---- errors before the stream ----


@pytest.mark.parametrize(
    "body",
    [
        {"turns": []},
        {},
        {"turns": "nope"},
        {"turns": [{"id": "t1"}]},
        {"turns": [{"id": "t1", "text": 5}]},
        {"turns": [{"id": "t1", "text": "x", "words": [{"text": "x", "confidence": "high"}]}]},
        {"turns": [turn("t1", "a"), turn("t1", "b")]},
        [1, 2],
    ],
)
def test_invalid_body_is_422(client, body):
    fake = configure(client, FakeJev())
    r = client.post("/triage", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_triage_request" and set(r.json()) == {"detail", "code"}
    assert fake.calls == []


def test_malformed_json_is_422(client):
    configure(client, FakeJev())
    r = client.post("/triage", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_triage_request"


def test_too_many_turns_is_422(client):
    configure(client, FakeJev(), triage_max_turns=3)
    r = client.post("/triage", json={"turns": [turn(f"t{i}", "Hi.") for i in range(4)]})
    assert r.status_code == 422 and r.json()["code"] == "invalid_triage_request"
    assert client.post("/triage", json={"turns": [turn(f"t{i}", "Hi.") for i in range(3)]}).status_code == 200


def test_turn_over_char_cap_is_422(client):
    configure(client, FakeJev(), triage_max_turn_chars=20)
    r = client.post("/triage", json={"turns": [turn("t1", "x" * 21)]})
    assert r.status_code == 422 and r.json()["code"] == "invalid_triage_request"
    assert client.post("/triage", json={"turns": [turn("t1", "x" * 20)]}).status_code == 200


def test_other_routes_keep_generic_validation_code(client):
    r = client.post("/predict")
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"


def test_no_key_is_503_before_stream(client):
    fake = configure(client, FakeJev(), key=None)
    r = client.post("/triage", json={"turns": [turn("t1", "Polyps can become cancer.")]})
    assert r.status_code == 503
    assert r.json() == {"detail": "Set JEV_KEY in .env", "code": "triage_unavailable"}
    assert fake.calls == []


def test_default_factory_builds_jev_client(client):
    configure(client, jev_model="jev-x", jev_base_url="https://jev.invalid", jev_timeout_s=3)
    jev = client.app.state.jev_client_factory(client.app.state.settings)
    assert isinstance(jev, JevClient) and jev.model == "jev-x"
    assert KEY not in repr(jev)


# ---- errors mid-stream ----


@pytest.mark.parametrize(
    "error,code",
    [
        (JevAuthError("HTTP 401"), "triage_unavailable"),
        (JevRateLimited("HTTP 429"), "triage_rate_limited"),
        (JevUpstreamError("timeout"), "triage_upstream_error"),
        (RuntimeError(SECRET_TEXT), "triage_upstream_error"),
    ],
)
def test_error_mid_stream_keeps_earlier_events(client, caplog, error, code):
    caplog.set_level("DEBUG")
    fake = configure(client, FakeJev(fail_on_call=2, error=error))
    turns = [turn("t1", "Polyps can become cancer."), turn("t2", SECRET_TEXT), turn("t3", "Fibre helps.")]
    r = client.post("/triage", json={"turns": turns})
    assert r.status_code == 200
    ev = events(r)
    assert [e["type"] for e in ev] == ["turn_started", "span", "turn_done", "turn_started", "error"]
    assert ev[-1] == {"type": "error", "code": code}
    assert len(fake.calls) == 2 and fake.closed
    assert SECRET_TEXT not in caplog.text and KEY not in caplog.text


def test_unexpected_error_logs_only_type(client, caplog):
    configure(client, FakeJev(fail_on_call=1, error=ValueError(SECRET_TEXT)))
    events(client.post("/triage", json={"turns": [turn("t1", SECRET_TEXT)]}))
    assert "ValueError" in caplog.text
    assert SECRET_TEXT not in caplog.text and "Zorblaxian" not in caplog.text


def test_factory_error_becomes_error_event(client):
    configure(client)

    def broken(settings):
        raise RuntimeError(KEY)

    client.app.state.jev_client_factory = broken
    ev = events(client.post("/triage", json={"turns": [turn("t1", "Polyps grow.")]}))
    assert ev == [{"type": "error", "code": "triage_upstream_error"}]


# ---- disconnect ----


def test_disconnect_stops_further_turns(client, monkeypatch):
    checks = []

    async def disconnected_after_first_turn(self):
        checks.append(1)
        return len(checks) > 1

    monkeypatch.setattr(Request, "is_disconnected", disconnected_after_first_turn)
    fake = configure(client, FakeJev())
    turns = [turn(f"t{i}", "Polyps can become cancer.") for i in range(3)]
    ev = events(client.post("/triage", json={"turns": turns}))
    assert [e["type"] for e in ev] == ["turn_started", "span", "turn_done"]  # no further turns, no done
    assert len(fake.calls) == 1 and fake.closed
    assert len(checks) == 2


# ---- privacy ----


def test_successful_run_logs_no_content(client, caplog):
    caplog.set_level("DEBUG")
    configure(client, FakeJev())
    words = [{"text": "Zorblaxian", "confidence": 0.2}]
    r = client.post("/triage", json={"turns": [turn("t1", SECRET_TEXT, words)]})
    assert r.status_code == 200
    r = client.post("/triage/span", json={"text": SECRET_TEXT, "previous_doctor_turns": [SECRET_TEXT]})
    assert r.status_code == 200
    assert "Zorblaxian" not in caplog.text and KEY not in caplog.text


def test_pre_stream_errors_log_no_content(client, caplog):
    caplog.set_level("DEBUG")
    configure(client, FakeJev(), triage_max_turn_chars=10)
    client.post("/triage", json={"turns": [turn("t1", SECRET_TEXT)]})
    client.post("/triage", json={"turns": [{"id": "t1", "text": SECRET_TEXT, "words": SECRET_TEXT}]})
    configure(client, FakeJev(), key=None)
    client.post("/triage", json={"turns": [turn("t1", SECRET_TEXT)]})
    assert "Zorblaxian" not in caplog.text and KEY not in caplog.text


def test_nothing_written_to_disk(client, monkeypatch):
    configure(client, FakeJev())
    real_open = builtins.open

    def no_writes(file, mode="r", *a, **k):
        assert not any(c in mode for c in "wax+"), f"write to {file}"
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", no_writes)
    assert client.post("/triage", json={"turns": [turn("t1", SECRET_TEXT)]}).status_code == 200
    assert client.post("/triage/span", json={"text": SECRET_TEXT}).status_code == 200


# ---- /triage/span ----


def test_span_happy_path(client):
    fake = configure(client, FakeJev())
    prev = ["First turn.", "Second turn.", "Third turn."]
    r = client.post("/triage/span", json={"text": "The sigmoid colon is part of the large bowel.", "previous_doctor_turns": prev})
    assert r.status_code == 200
    assert r.json() == {"kind": "claim", "p_claim": pytest.approx(0.8)}
    (call,) = fake.calls
    assert call["turn"] == "The sigmoid colon is part of the large bowel."
    assert call["previous"] == ["Second turn.", "Third turn."]
    assert [(s.id, s.text) for s in call["spans"]] == [("0", "The sigmoid colon is part of the large bowel.")]
    assert call["ask_asr"] is False
    assert fake.closed


def test_span_skip_and_default_previous(client):
    fake = configure(client, FakeJev())
    r = client.post("/triage/span", json={"text": "Please have a seat."})
    assert r.json() == {"kind": "skip", "p_claim": pytest.approx(0.05)}
    assert fake.calls[0]["previous"] == []


def test_span_kind_is_base_kind_even_if_asr_answered(client):
    configure(client, FakeJev(answer=lambda text, ask_asr: SpanAnswer(0.1, 0.99)))
    assert client.post("/triage/span", json={"text": "Sigmoid colin."}).json()["kind"] == "claim"


@pytest.mark.parametrize(
    "body", [{"text": ""}, {"text": "   \n"}, {}, {"text": 3}, {"text": "ok", "previous_doctor_turns": "x"}, [1]]
)
def test_span_invalid_body_is_422(client, body):
    fake = configure(client, FakeJev())
    r = client.post("/triage/span", json=body)
    assert r.status_code == 422 and r.json()["code"] == "invalid_triage_request"
    assert fake.calls == []


def test_span_over_char_cap_is_422(client):
    configure(client, FakeJev(), triage_max_turn_chars=10)
    r = client.post("/triage/span", json={"text": "x" * 11})
    assert r.status_code == 422 and r.json()["code"] == "invalid_triage_request"


def test_span_no_key_is_503(client):
    configure(client, FakeJev(), key=None)
    r = client.post("/triage/span", json={"text": "Polyps grow."})
    assert r.status_code == 503
    assert r.json() == {"detail": "Set JEV_KEY in .env", "code": "triage_unavailable"}


@pytest.mark.parametrize(
    "error,status,code",
    [
        (JevAuthError("HTTP 401"), 503, "triage_unavailable"),
        (JevRateLimited("HTTP 429"), 429, "triage_rate_limited"),
        (JevUpstreamError("timeout"), 502, "triage_upstream_error"),
        (RuntimeError(SECRET_TEXT), 502, "triage_upstream_error"),
    ],
)
def test_span_jev_errors(client, caplog, error, status, code):
    caplog.set_level("DEBUG")
    fake = configure(client, FakeJev(fail_on_call=1, error=error))
    r = client.post("/triage/span", json={"text": SECRET_TEXT})
    assert r.status_code == status
    assert r.json()["code"] == code and set(r.json()) == {"detail", "code"}
    assert SECRET_TEXT not in r.text
    assert fake.closed
    assert "Zorblaxian" not in caplog.text and KEY not in caplog.text


# ---- OpenAPI and degraded app ----


def test_routes_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/triage"] and "post" in paths["/triage/span"]
    assert "requestBody" in paths["/triage"]["post"] and "requestBody" in paths["/triage/span"]["post"]


def test_triage_works_without_image_model(client):
    assert client.get("/health").json()["model_loaded"] is False
    configure(client, FakeJev())
    assert events(client.post("/triage", json={"turns": [turn("t1", "Polyps grow.")]}))[-1]["type"] == "done"
    assert client.post("/triage/span", json={"text": "Polyps grow."}).status_code == 200
