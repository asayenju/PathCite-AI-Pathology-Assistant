"""Guards backend/evals/triage/hard_cases.jsonl, the hard-case triage gold set scored by `pytest -m jev` (issue #57)."""

import json
from collections import Counter
from pathlib import Path

import pytest

from app.triage.segment import split_spans

PATH = Path(__file__).resolve().parents[1] / "evals" / "triage" / "hard_cases.jsonl"
CATEGORY_LABEL = {
    "context_reply": "skip",
    "patient_specific": "skip",
    "plan_as_fact": "skip",
    "hedged_fact": "claim",
    "short_fact": "claim",
    "fact_in_reply": "claim",
}
FIELDS = {"id", "category", "previous_doctor_turns", "turn", "span", "label", "note"}


def rows() -> list[dict]:
    return [json.loads(line) for line in PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_size_and_balance():
    counts = Counter(r["category"] for r in rows())
    assert 60 <= sum(counts.values()) <= 80
    assert set(counts) == set(CATEGORY_LABEL)
    assert all(10 <= n <= 15 for n in counts.values()), counts


def test_ids_are_unique():
    ids = [r["id"] for r in rows()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("row", rows(), ids=lambda r: r["id"])
def test_row_schema(row):
    assert set(row) == FIELDS
    assert row["label"] == CATEGORY_LABEL[row["category"]]
    prev = row["previous_doctor_turns"]
    assert isinstance(prev, list) and len(prev) <= 2 and all(isinstance(t, str) and t for t in prev)
    assert isinstance(row["note"], str)


@pytest.mark.parametrize("row", rows(), ids=lambda r: r["id"])
def test_span_is_exactly_one_triage_span_of_its_turn(row):
    spans = [s for s in split_spans(row["turn"]) if row["turn"][s.start : s.end] == row["span"]]
    assert len(spans) == 1
    assert not spans[0].filler


@pytest.mark.parametrize("row", [r for r in rows() if r["label"] == "claim"], ids=lambda r: r["id"])
def test_no_gold_claim_is_skipped_by_the_local_rule(row):
    (span,) = [s for s in split_spans(row["turn"]) if row["turn"][s.start : s.end] == row["span"]]
    assert not span.local_skip
