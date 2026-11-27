"""Opt-in live test of the Luna verifier: `pytest -m check -s` (costs well under a cent).

Keys come from Settings with the env file at CHECK_ENV (default: the repo-root .env). Only the
stances, the model's verdict fields and token counts are printed, never the key.
"""

import os
from pathlib import Path

import pytest

from app.check.llm import OpenAIResponsesClient
from app.check.verify import ChunkIn, luna_stances
from app.config import REPO_ROOT, Settings

pytestmark = pytest.mark.check

ENV = Path(os.environ.get("CHECK_ENV", REPO_ROOT / ".env"))
# gpt-6-luna list price, USD per 1M tokens (docs/PRD-rag-checker.md).
PRICE_IN, PRICE_OUT = 0.10, 0.50

CLAIM = (
    "Breslow thickness is measured from the top of the granular layer of the epidermis to the "
    "deepest point of invasion."
)
CONTEXT = [
    "The biopsy showed a melanoma on your back.",
    "We describe how deep it goes with a number called the Breslow thickness.",
]
CHUNKS = [
    ChunkIn(
        chunk_id=101,
        title="Cutaneous melanoma: pathology reporting",
        section="Breslow thickness",
        content=(
            "Tumour thickness is the most important prognostic factor in primary cutaneous melanoma. "
            "Breslow thickness is measured with an ocular micrometer from the top of the granular layer "
            "of the epidermis to the deepest invasive tumour cell. It is recorded in millimetres to one "
            "decimal place."
        ),
    ),
    ChunkIn(
        chunk_id=202,
        title="Melanoma staging notes (hand-written test passage)",
        section="Measurement",
        content=(
            "In this protocol, Breslow thickness is not measured from the granular layer. It is measured "
            "from the dermo-epidermal junction to the deepest invasive cell, and ulcerated tumours are "
            "measured from the ulcer base."
        ),
    ),
    ChunkIn(
        chunk_id=303,
        title="Anaplastic thyroid cancer",
        section="Overview",
        content="Anaplastic thyroid carcinoma is a rare, aggressive undifferentiated tumour of the thyroid gland.",
    ),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_luna_live_stances_are_grounded():
    settings = Settings(_env_file=ENV)
    if settings.openai_key is None:
        pytest.skip(f"OPEN_AI_KEY is needed in {ENV}")
    async with OpenAIResponsesClient.from_settings(settings) as client:
        result = await luna_stances(client, CLAIM, CONTEXT, CHUNKS)
        total = client.total_usage

    for s in result.stances:
        print(f"chunk {s.chunk_id}: {s.stance} (model: {s.model_stance}, grounded: {s.grounded}) quote={s.quote!r}")
    print(f"missing_qualifier={result.missing_qualifier} issue={result.issue!r} suggestion={result.suggestion!r}")
    cost = (total.input_tokens * PRICE_IN + total.output_tokens * PRICE_OUT) / 1e6
    print(
        f"usage: input {total.input_tokens} (cached {total.cached_tokens}), output {total.output_tokens} "
        f"(reasoning {total.reasoning_tokens}); ~${cost:.6f} at list price"
    )

    by_id = {s.chunk_id: s for s in result.stances}
    assert [s.chunk_id for s in result.stances] == [101, 202, 303]
    assert by_id[101].stance == "supports" and by_id[101].grounded
    assert by_id[202].stance == "contradicts" and by_id[202].grounded
    assert by_id[303].stance == "irrelevant"
    assert total.input_tokens > 0 and total.output_tokens > 0
