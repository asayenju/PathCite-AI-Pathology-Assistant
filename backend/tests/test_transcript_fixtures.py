"""Guards the synthetic transcript fixtures (see tests/fixtures/transcripts/README.md)."""

import json
from pathlib import Path

import pytest

from tests.fixtures.transcripts import generate

FIXTURES = Path(generate.__file__).parent


@pytest.mark.parametrize("name", sorted(generate.build()))
def test_fixture_is_up_to_date(name):
    on_disk = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(generate.build()[name])), (
        f"{name} is stale: run python tests/fixtures/transcripts/generate.py"
    )


def test_truncated_fixture_is_invalid_json():
    with pytest.raises(json.JSONDecodeError):
        json.loads((FIXTURES / "truncated.json").read_text(encoding="utf-8"))


def test_generation_is_deterministic():
    assert generate.build() == generate.build()


def test_dialogue_round_trips_through_timed_words():
    for (spk, text), (tspk, ttext, start, end, words) in zip(generate.DIALOGUE, generate.timed_dialogue()):
        assert (spk, text) == (tspk, ttext)
        assert " ".join(w + p for w, p, *_ in words) == text
        assert start < end

