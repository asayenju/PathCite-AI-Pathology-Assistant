import json
import logging
from dataclasses import dataclass

import httpx
import pytest

from app.check.llm import LLMUpstreamError, OpenAIResponsesClient, Usage
from app.check.verify import (
    LUNA_INSTRUCTIONS,
    LUNA_SCHEMA,
    ChunkIn,
    LunaResult,
    SourceStance,
    ground,
    luna_stances,
    normalise_for_match,
)

KEY = "sk-openai-test-key-do-not-leak"
CLAIM = "Breslow thickness is measured from the top of the granular layer SECRET-CLAIM."
CHUNKS = [
    ChunkIn(
        chunk_id=11,
        section="Staging",
        title="Melanoma guideline",
        content=(
            "Breslow thickness is measured from the top of the granular layer of the epidermis "
            "to the deepest invasive cell. It is reported in millimetres."
        ),
    ),
    ChunkIn(
        chunk_id=22,
        section="Pathology",
        title=None,
        content="Breslow depth is measured from the base of the tumour, not from the granular layer.",
    ),
    ChunkIn(chunk_id=33, section="Thyroid", title="Thyroid", content="Anaplastic thyroid cancer is aggressive."),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def luna_json(stances=None, **kw):
    out = {
        "stances": stances
        if stances is not None
        else [
            {"chunk_id": 11, "stance": "supports", "quote": "Breslow thickness is measured from the top of the granular layer of the epidermis to the deepest invasive cell."},
            {"chunk_id": 22, "stance": "contradicts", "quote": "Breslow depth is measured from the base of the tumour, not from the granular layer."},
            {"chunk_id": 33, "stance": "irrelevant", "quote": ""},
        ],
        "missing_qualifier": False,
        "issue": None,
        "suggestion": None,
    }
    out.update(kw)
    return out


def responses_body(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 500, "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens": 80, "output_tokens_details": {"reasoning_tokens": 20}},
    }


def client_for(payload, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json=responses_body(payload))

    async def no_sleep(_):
        pass

    return OpenAIResponsesClient(KEY, model="gpt-6-luna", transport=httpx.MockTransport(handler), sleep=no_sleep)


# --- luna_stances -----------------------------------------------------------


@pytest.mark.anyio
async def test_request_carries_instructions_schema_claim_context_and_passages():
    seen = []
    async with client_for(luna_json(), seen) as client:
        await luna_stances(client, CLAIM, ["turn one", "turn two", "turn three"], CHUNKS)
    (body,) = seen
    assert body["instructions"] == LUNA_INSTRUCTIONS
    assert body["text"]["format"]["schema"] == LUNA_SCHEMA
    assert body["text"]["format"]["strict"] is True
    text = body["input"]
    assert CLAIM in text
    # Only the 2 most recent previous doctor turns are sent.
    assert "turn one" not in text and "turn two" in text and "turn three" in text
    for c in CHUNKS:
        assert f"chunk_id={c.chunk_id}" in text and c.content in text
    assert text.index("chunk_id=11") < text.index("chunk_id=22") < text.index("chunk_id=33")


def test_schema_is_strict_compatible():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(LUNA_SCHEMA)
    props = LUNA_SCHEMA["properties"]
    assert props["issue"]["type"] == ["string", "null"]
    assert props["suggestion"]["type"] == ["string", "null"]
    assert props["stances"]["items"]["properties"]["stance"]["enum"] == ["supports", "contradicts", "irrelevant"]


def test_instructions_state_the_rules():
    text = LUNA_INSTRUCTIONS.lower()
    for phrase in ["only", "word for word", "patient", "subtype", "irrelevant", "missing_qualifier"]:
        assert phrase in text


@pytest.mark.anyio
async def test_parses_and_grounds_a_valid_answer():
    async with client_for(luna_json(missing_qualifier=True, issue="Omits X.", suggestion="Say X.")) as client:
        result = await luna_stances(client, CLAIM, [], CHUNKS)
    assert isinstance(result, LunaResult)
    assert [s.stance for s in result.stances] == ["supports", "contradicts", "irrelevant"]
    assert [s.chunk_id for s in result.stances] == [11, 22, 33]
    assert result.stances[0].quote.startswith("Breslow thickness")
    assert result.missing_qualifier is True
    assert result.issue == "Omits X." and result.suggestion == "Say X."
    assert result.usage == Usage(500, 0, 80, 20)


@pytest.mark.anyio
async def test_fabricated_quote_becomes_unclear():
    stances = luna_json()["stances"]
    stances[0]["quote"] = "Breslow thickness is always measured in centimetres."
    async with client_for(luna_json(stances)) as client:
        result = await luna_stances(client, CLAIM, [], CHUNKS)
    s = result.stances[0]
    assert s.stance == "unclear" and s.model_stance == "supports" and s.grounded is False


@pytest.mark.anyio
async def test_empty_chunks_make_no_request():
    seen = []
    async with client_for(luna_json(), seen) as client:
        result = await luna_stances(client, CLAIM, [], [])
    assert seen == [] and result.stances == [] and result.usage == Usage()


@pytest.mark.anyio
async def test_accepts_any_object_with_chunk_attributes():
    @dataclass
    class Evidence:  # stand-in for app.check.search.Evidence (#47)
        chunk_id: int
        title: str | None
        section: str
        content: str
        url: str = "https://example.org"

    ev = [Evidence(c.chunk_id, c.title, c.section, c.content) for c in CHUNKS]
    async with client_for(luna_json()) as client:
        result = await luna_stances(client, CLAIM, [], ev)
    assert [s.stance for s in result.stances] == ["supports", "contradicts", "irrelevant"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"stances": [], "missing_qualifier": False, "issue": None},  # missing field
        luna_json([{"chunk_id": 11, "stance": "maybe", "quote": ""}]),  # bad enum
        luna_json([{"chunk_id": "11", "stance": "supports", "quote": "x"}]),  # wrong type
        luna_json([{"chunk_id": 11, "stance": "supports"}]),  # missing quote
        luna_json(missing_qualifier="yes"),
        luna_json(issue=5),
        luna_json(stances="nope"),
        luna_json(extra="field"),
    ],
    ids=["missing-field", "bad-enum", "id-type", "missing-quote", "bool-type", "issue-type",
         "stances-type", "extra-field"],
)
async def test_schema_violations_are_upstream_errors(payload):
    async with client_for(payload) as client:
        with pytest.raises(LLMUpstreamError) as e:
            await luna_stances(client, CLAIM, [], CHUNKS)
    assert "SECRET" not in str(e.value)
    assert e.value.__cause__ is None


@pytest.mark.anyio
async def test_nothing_sensitive_logged(caplog):
    caplog.set_level(logging.DEBUG)
    async with client_for(luna_json()) as client:
        await luna_stances(client, CLAIM, ["SECRET-CONTEXT"], CHUNKS)
    async with client_for({"bad": 1}) as client:
        with pytest.raises(LLMUpstreamError):
            await luna_stances(client, CLAIM, [], CHUNKS)
    assert "SECRET" not in caplog.text and KEY not in caplog.text


# --- ground ------------------------------------------------------------------

CONTENT = "Breslow thickness is measured from the top of the granular layer. It is reported in mm."
CHUNK = ChunkIn(chunk_id=1, section="s", content=CONTENT)


def g(stance, quote, chunks=(CHUNK,), chunk_id=1):
    return ground([{"chunk_id": chunk_id, "stance": stance, "quote": quote}], list(chunks))


def test_exact_quote_is_kept():
    (s,) = g("supports", "Breslow thickness is measured from the top of the granular layer.")
    assert s == SourceStance(1, "supports", "Breslow thickness is measured from the top of the granular layer.", "supports", True)


def test_quote_matches_despite_curly_quotes_dashes_whitespace_and_case():
    chunk = ChunkIn(chunk_id=1, section="s", content="The so-called “Breslow” depth – in mm – isn't   used\nhere.")
    (s,) = g("contradicts", 'the SO‑called "breslow" depth - in mm - isn’t used here.', [chunk])
    assert s.stance == "contradicts" and s.grounded


def test_nfc_normalisation():
    chunk = ChunkIn(chunk_id=1, section="s", content="Mélanome cutané.")  # decomposed
    (s,) = g("supports", "Mélanome cutané", [chunk])
    assert s.stance == "supports"


def test_fabricated_quote_is_unclear():
    (s,) = g("supports", "Breslow thickness is measured in centimetres.")
    assert s.stance == "unclear" and s.model_stance == "supports" and not s.grounded


def test_empty_quote_on_supports_or_contradicts_is_unclear():
    for stance in ("supports", "contradicts"):
        for quote in ("", "   ", '""'):
            (s,) = g(stance, quote)
            assert s.stance == "unclear", (stance, quote)


def test_irrelevant_keeps_empty_quote():
    (s,) = g("irrelevant", "")
    assert s == SourceStance(1, "irrelevant", "", "irrelevant", True)


def test_irrelevant_quote_is_dropped():
    (s,) = g("irrelevant", "whatever the model said")
    assert s.stance == "irrelevant" and s.quote == ""


def test_missing_chunk_is_unclear_and_order_follows_input():
    chunks = [ChunkIn(chunk_id=i, section="s", content=CONTENT) for i in (5, 6, 7)]
    out = ground([{"chunk_id": 7, "stance": "irrelevant", "quote": ""},
                  {"chunk_id": 5, "stance": "supports", "quote": "It is reported in mm."}], chunks)
    assert [(s.chunk_id, s.stance) for s in out] == [(5, "supports"), (6, "unclear"), (7, "irrelevant")]
    assert out[1].model_stance is None and out[1].quote == ""


def test_unknown_chunk_id_is_dropped():
    out = ground([{"chunk_id": 999, "stance": "supports", "quote": CONTENT},
                  {"chunk_id": 1, "stance": "irrelevant", "quote": ""}], [CHUNK])
    assert [s.chunk_id for s in out] == [1]


def test_duplicate_judgements_keep_the_first():
    out = ground([{"chunk_id": 1, "stance": "irrelevant", "quote": ""},
                  {"chunk_id": 1, "stance": "supports", "quote": "It is reported in mm."}], [CHUNK])
    assert [s.stance for s in out] == ["irrelevant"]


def test_quote_must_come_from_its_own_chunk():
    other = ChunkIn(chunk_id=2, section="s", content="Something else entirely.")
    out = ground([{"chunk_id": 2, "stance": "supports", "quote": "It is reported in mm."}], [CHUNK, other])
    assert out[1].stance == "unclear"


def test_normalise_for_match():
    assert normalise_for_match("  “A” —  b’S\n") == '"a" - b\'s'
