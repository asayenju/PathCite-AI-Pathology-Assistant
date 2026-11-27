"""Opt-in live smoke test of POST /check end to end: `pytest -m check -s` (search + Luna + Jev, about a cent).

Keys come from Settings with the env file at CHECK_ENV (default: the repo-root .env). Asserts the response shape
only; prints verdicts, reasons, per-source stances, latency and Luna token usage. Never prints a key.
"""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.check.llm import OpenAIResponsesClient, Usage
from app.config import REPO_ROOT, Settings
from app.main import app

pytestmark = pytest.mark.check

ENV = Path(os.environ.get("CHECK_ENV", REPO_ROOT / ".env"))
PRICE_IN, PRICE_OUT = 0.10, 0.50  # gpt-6-luna, USD per 1M tokens

CLAIMS = [
    ("Breslow thickness is how wide the melanoma spot was.", ["The biopsy showed a melanoma on your back."]),
    ("Colon cancers are tested for mismatch repair proteins.", ["We'll look at your colon biopsy together."]),
    ("Because it's HER2 negative, hormone-blocking pills won't work for you.", ["Your breast cancer is HER2 negative."]),
]

VERDICTS = {"accurate", "incomplete", "inaccurate", "disputed", "unverified"}
STANCES = {"supports", "contradicts", "irrelevant", "unclear"}
REASONS = {"sources_disagree", "models_disagree", "jev_unavailable"}


def test_check_live_three_claims(monkeypatch, tmp_path):
    settings = Settings(_env_file=ENV)
    for name in ("openai_key", "openai_vector_key", "supabase_url", "supabase_secret_key"):
        if not getattr(settings, name):
            pytest.skip(f"{name.upper()} is needed in {ENV}")
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.pt"))

    clients: list[OpenAIResponsesClient] = []

    def llm_factory(s):
        c = OpenAIResponsesClient.from_settings(s)
        clients.append(c)
        return c

    total = Usage()
    with TestClient(app) as c:
        c.app.state.settings = settings
        c.app.state.check_llm_factory = llm_factory
        for claim, context in CLAIMS:
            t0 = time.perf_counter()
            r = c.post("/check", json={"turn_id": "t0001", "quote": claim, "context": context})
            elapsed = time.perf_counter() - t0
            assert r.status_code == 200, r.json()
            body = r.json()
            usage = clients[-1].total_usage
            total = total + usage

            print(f"\nCLAIM: {claim}")
            print(
                f"  verdict={body['verdict']} needs_review={body['needs_review']} reasons={body['review_reasons']} "
                f"model={body['model']} jev_model={body['jev_model']} latency={elapsed:.1f}s "
                f"luna_tokens=in {usage.input_tokens} (cached {usage.cached_tokens}) / out {usage.output_tokens}"
            )
            if body["issue"]:
                print(f"  issue: {body['issue']}\n  suggestion: {body['suggestion']}")
            for s in body["sources"]:
                p = f"{s['jev_p']:.2f}" if s["jev_p"] is not None else "-"
                print(
                    f"  [{s['stance']:>11}] luna={s['luna_stance']:<11} jev={s['jev_stance'] or '-':<11} p={p} "
                    f"{s['trust']:<6} {s['year'] or '----'} {(s['title'] or '(untitled)')[:60]} / {s['section'][:40]}"
                )
                if s["quote"]:
                    print(f"      quote: {s['quote'][:160]!r}")

            assert body["status"] == "checked"
            assert body["verdict"] in VERDICTS
            assert set(body["review_reasons"]) <= REASONS
            assert isinstance(body["needs_review"], bool)
            assert len(body["sources"]) <= settings.check_top_k
            for s in body["sources"]:
                assert s["stance"] in STANCES and s["luna_stance"] in STANCES
                assert s["url"].startswith("http")
                if s["jev_stance"] is not None:
                    assert 0.0 <= s["jev_p"] <= 1.0

    cost = (total.input_tokens * PRICE_IN + total.output_tokens * PRICE_OUT) / 1e6
    print(
        f"\nLuna total: input {total.input_tokens} (cached {total.cached_tokens}), output {total.output_tokens} "
        f"(reasoning {total.reasoning_tokens}); ~${cost:.5f} at list price for {len(CLAIMS)} claims"
    )
