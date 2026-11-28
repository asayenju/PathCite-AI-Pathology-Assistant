"""Opt-in live eval of Jev triage: `pytest -m jev -s` (needs JEV_KEY and network; skipped by default).

a. Samples vs gold. Each of the 9 sample transcripts (frontend/public/transcripts/) is imported through
   POST /transcripts/import, its doctor turns (the speaker suggested as doctor) go through the real POST /triage
   (real JevClient, configured thresholds), and the NDJSON spans are aligned with the gold labels in
   backend/evals/triage/keys/ by (doctor_turn_index, start, end). Report: reports/jev_triage_samples.md.

b. Cancer-Myth (Cancer-Myth/Cancer-Myth, CC-BY-4.0, split validation). Every `source_myth` that is a real
   statement (not "From physicians.") is triaged as one span on its own, ask_asr=True, with `decide` and the
   configured thresholds. Every myth is a general medical assertion, so each should be a claim. Duplicate
   statements are sent once and scored per row. Report: reports/jev_triage_cancer_myth.md.

c. Hard cases (issue #57). Each row of backend/evals/triage/hard_cases.jsonl (context-dependent replies, patient
   results, plans phrased as facts, hedged / short / in-reply general facts) goes through the production per-turn
   path, `app.triage.router._triage_turn` (what POST /triage runs for each turn): the row's turn, its previous
   doctor turns, spans from split_spans with the local skips, one classify_spans call, `decide` with the configured
   thresholds. The event for the row's span is scored. The 6 checker-test transcripts
   (frontend/public/transcripts/checker-tests/) are scored in the same report, through the real POST /triage,
   against checker_tests.py's `expect` map ("claim:*" = claim, unlisted doctor sentences = skip).
   Report: reports/jev_triage_hard_cases.md (per category, claim recall, skip precision/recall, misses, sweep).

All reports include a threshold sweep replayed from the probabilities already collected (no extra calls).

Variants (JEV_EVAL_VARIANT, default `production` = the code as shipped, no overrides):
- `baseline`: no short-reply rule, original KIND_CRITERIA.
- `rule`: short-reply rule (segment.Span.short_reply skipped locally), original criteria.
- `criteria`: no rule, criteria with the issue #57 context notes.
- `rule+criteria`: both.
Each run saves its rows to reports/jev_triage_runs/<variant>/<set>-<time>.json; the reports and
reports/jev_triage_variants.md aggregate every saved run (mean and range). Delete that folder to start over.

Claim recall rule: a gold claim counts as caught when it is predicted `claim`, or `transcription` with
base_kind `claim` (the doctor's "Yes, that's correct" then sends it to the checker). A gold claim predicted
`transcription` with base_kind `skip` is a miss.

Env:
- JEV_KEY: the key (or the repo-root .env, as for the app). Never printed or written.
- JEV_EVAL_CM_STEP: send every Nth distinct Cancer-Myth statement (default 1 = all), for rate limits.
- JEV_EVAL_DELAY_S: pause between Cancer-Myth and hard-case requests (default 0.2 s). Requests are sequential;
  the client retries 429 with retry-after.
- JEV_EVAL_VARIANT: see above.

The sample transcripts and their gold labels are synthetic and AI-written, not reviewed by a pathologist.
"""

import asyncio
import dataclasses
import json
import os
import re
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.triage import jev as jev_module
from app.triage import router as router_module
from app.triage import segment
from app.triage.decide import decide
from app.triage.jev import SpanInput, kind_criteria
from app.triage.router import default_jev_client_factory
from app.triage.schemas import TriageTurn
from tests.fixtures.transcripts import checker_tests, generate
from tests.fixtures.transcripts.scenarios import SCENARIOS

pytestmark = pytest.mark.jev

BACKEND = Path(__file__).resolve().parents[1]
REPORTS = BACKEND / "reports"
RUNS = REPORTS / "jev_triage_runs"
CACHE = BACKEND / ".cache" / "cancer_myth"
HARD_CASES = BACKEND / "evals" / "triage" / "hard_cases.jsonl"

VARIANT = os.environ.get("JEV_EVAL_VARIANT", "production")
VARIANTS = {  # name: (short-reply rule on, criteria with context notes); None = leave the code as shipped
    "production": None,
    "baseline": (False, False),
    "rule": (True, False),
    "criteria": (False, True),
    "rule+criteria": (True, True),
}
if VARIANT not in VARIANTS:
    raise ValueError(f"JEV_EVAL_VARIANT must be one of {sorted(VARIANTS)}")
HARD_CLAIM_CATEGORIES = ("hedged_fact", "short_fact", "fact_in_reply")
HARD_SKIP_CATEGORIES = ("context_reply", "patient_specific", "plan_as_fact")

CM_DATASET = "Cancer-Myth/Cancer-Myth"
CM_ROWS = 585
NON_STATEMENTS = {"From physicians."}  # the only non-statement source_myth value (checked 2026-11-25)

CLAIM_RECALL_MIN = 0.95
ASR_RECALL_MIN = 0.8
LABELS = ("skip", "claim", "transcription")
SKIP_SWEEP = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
ASR_SWEEP = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


@pytest.fixture(scope="module")
def settings() -> Settings:
    s = Settings()
    if s.jev_key is None or not s.jev_key.get_secret_value():
        pytest.skip("JEV_KEY is not set (env or repo-root .env): the live Jev eval needs it")
    return s


@pytest.fixture(scope="module", autouse=True)
def variant():
    """Apply JEV_EVAL_VARIANT for the module: the router's split_spans and the criteria Jev is sent."""
    config = VARIANTS[VARIANT]
    if config is None:
        yield VARIANT
        return
    rule, notes = config

    def split_spans(text: str):
        spans = segment.split_spans(text)
        return spans if rule else [dataclasses.replace(s, short_reply=False) for s in spans]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(router_module, "split_spans", split_spans)
        mp.setattr(jev_module, "KIND_CRITERIA", kind_criteria(context_notes=notes))
        yield VARIANT


def _counting_factory(counter: list[int]):
    """default_jev_client_factory, counting classify_spans calls (one per Jev request batch of <= 40 spans)."""

    def factory(settings: Settings):
        client = default_jev_client_factory(settings)
        inner = client.classify_spans

        async def classify_spans(*args, **kwargs):
            counter[0] += 1
            return await inner(*args, **kwargs)

        client.classify_spans = classify_spans
        return client

    return factory


def _report_path(stem: str, ext: str = "md") -> Path:
    suffix = "" if VARIANT == "production" else f".{VARIANT.replace('+', '_')}"
    return REPORTS / f"{stem}{suffix}.{ext}"


def _save_run(name: str, rows: list[dict], calls: int, model: str | None) -> None:
    folder = RUNS / VARIANT.replace("+", "_")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    doc = {"variant": VARIANT, "set": name, "model": model, "jev_calls": calls, "rows": rows}
    (folder / f"{name}-{stamp}.json").write_text(json.dumps(doc, indent=1))


def _load_runs(name: str, variant: str | None = None) -> list[dict]:
    folder = RUNS / (variant or VARIANT).replace("+", "_")
    return [json.loads(p.read_text()) for p in sorted(folder.glob(f"{name}-*.json"))] if folder.is_dir() else []


def _spread(values: list[float]) -> str:
    """Mean and range of a list of ratios, as percentages."""
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]:.1%}"
    return f"{statistics.mean(values):.1%} ({min(values):.1%}-{max(values):.1%})"


# ---- shared scoring ---------------------------------------------------------------------------------------


def _caught_claim(kind: str, base_kind: str) -> bool:
    return kind == "claim" or (kind == "transcription" and base_kind == "claim")


def _replay(p_claim: float, p_asr: float | None, *, skip_min: float, asr_flag: float):
    return decide(1.0 - p_claim, p_asr, asr_flag=asr_flag, skip_min=skip_min)


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} = {n / d:.1%}" if d else "n/a"


# ---- a. samples vs gold -----------------------------------------------------------------------------------


def _gold(sid: str) -> dict:
    return json.loads((generate.REPO / generate.TRIAGE_KEYS_DIR / f"{sid}.json").read_text(encoding="utf-8"))


def _triage_transcript(client: TestClient, sc) -> tuple[list[dict], list[dict], str | None]:
    """Import one sample transcript and send its doctor turns through POST /triage: (turns, span events, model)."""
    data = (generate.REPO / "frontend/public" / generate.sample_path(sc)).read_bytes()
    r = client.post("/transcripts/import", files={"file": (f"{sc.id}.json", data, "application/json")})
    assert r.status_code == 200, (sc.id, r.status_code)
    imp = r.json()
    (doctor,) = [s for s in imp["speakers"] if s["suggested_role"] == "doctor"]
    assert doctor["source_label"] == generate.DOCTOR_LABEL[sc.vendor], f"{sc.id}: wrong speaker suggested as doctor"
    turns = [t for t in imp["turns"] if t["speaker"] == doctor["id"]]
    body = {
        "turns": [
            {"id": t["id"], "text": t["text"], "words": [{"text": w["text"], "confidence": w["confidence"]} for w in t["words"]]}
            for t in turns
        ]
    }
    resp = client.post("/triage", json=body)
    assert resp.status_code == 200, (sc.id, resp.status_code, resp.text[:200])
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert events[-1]["type"] == "done", (sc.id, events[-1])
    return turns, [e for e in events if e["type"] == "span"], events[-1].get("model")


def _row(sample: str, turn: int, quote: str, gold: str, gold_base: str, e: dict) -> dict:
    return {
        "sample": sample,
        "turn": turn,
        "quote": quote,
        "gold": gold,
        "gold_base": gold_base,
        "kind": e["kind"],
        "base_kind": e["base_kind"],
        "p_claim": e["p_claim"],
        "p_asr": e["p_asr"],
    }


def _triage_sample(client: TestClient, sc) -> tuple[list[dict], str | None]:
    """Rows of {sample, gold..., kind, base_kind, p_claim, p_asr} for every gold span of one sample."""
    turns, spans, model = _triage_transcript(client, sc)
    index = {t["id"]: i for i, t in enumerate(turns)}
    predicted = {(index[e["turn_id"]], e["start"], e["end"]): e for e in spans}
    rows = []
    for g in _gold(sc.id)["spans"]:
        e = predicted[(g["doctor_turn_index"], g["start"], g["end"])]
        assert e["quote"] == g["quote"]
        rows.append(_row(sc.id, g["doctor_turn_index"], g["quote"], g["label"], g.get("base_kind", g["label"]), e))
    return rows, model


def _checker_gold(expect: str) -> tuple[str, str]:
    """(gold, gold_base) from a checker_tests.py `expect` value. Every planted error there sits in a general claim."""
    if expect.startswith("claim"):
        return "claim", "claim"
    if expect == "transcription":
        return "transcription", "claim"
    assert expect == "skip", expect
    return "skip", "skip"


def _triage_checker_test(client: TestClient, sc) -> tuple[list[dict], str | None]:
    """Rows for every non-filler doctor span of one checker-test transcript. Unlisted sentences are gold skip."""
    turns, spans, model = _triage_transcript(client, sc)
    index = {t["id"]: i for i, t in enumerate(turns)}
    filler = {
        (t["id"], s.start, s.end) for t in turns for s in segment.split_spans(t["text"]) if s.filler
    }
    rows, seen = [], set()
    for e in spans:
        if (e["turn_id"], e["start"], e["end"]) in filler:
            continue
        gold, base = _checker_gold(sc.expect.get(e["quote"], "skip"))
        seen.add(e["quote"])
        rows.append(_row(sc.id, index[e["turn_id"]], e["quote"], gold, base, e))
    assert set(sc.expect) <= seen, (sc.id, set(sc.expect) - seen)
    return rows, model


def _low_conf_rows(rows: list[dict]) -> list[dict]:
    """Gold non-transcription spans containing a sample's correct low-confidence term (e.g. "sigmoid")."""
    terms = {sc.id: list(sc.low_confidence) for sc in SCENARIOS}
    terms |= {sc.id: list(sc.low_confidence) for sc in checker_tests.SCENARIOS}
    out = []
    for r in rows:
        if r["gold"] == "transcription":
            continue
        for term in terms[r["sample"]]:
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", r["quote"], re.IGNORECASE):
                out.append({**r, "term": term})
                break
    return out


def _sample_metrics(rows: list[dict], *, skip_min: float, asr_flag: float) -> dict:
    claims = [r for r in rows if r["gold"] == "claim"]
    asr = [r for r in rows if r["gold"] == "transcription"]
    skips = [r for r in rows if r["gold"] == "skip"]
    dec = [(r, _replay(r["p_claim"], r["p_asr"], skip_min=skip_min, asr_flag=asr_flag)) for r in rows]
    pred_skip = [r for r, d in dec if d.kind == "skip"]
    lowc_ids = {(r["sample"], r["turn"], r["quote"]) for r in _low_conf_rows(rows)}
    return {
        "claim_caught": sum(_caught_claim(d.kind, d.base_kind) for r, d in dec if r["gold"] == "claim"),
        "claims": len(claims),
        "asr_caught": sum(d.kind == "transcription" for r, d in dec if r["gold"] == "transcription"),
        "asr": len(asr),
        "skip_correct": sum(r["gold"] == "skip" for r in pred_skip),
        "pred_skip": len(pred_skip),
        "skip_kept": sum(d.kind == "skip" for r, d in dec if r["gold"] == "skip"),
        "claim_pred": sum(_caught_claim(d.kind, d.base_kind) for _, d in dec),
        "claim_pred_correct": sum(_caught_claim(d.kind, d.base_kind) for r, d in dec if r.get("gold_base", r["gold"]) == "claim"),
        "skips": len(skips),
        "false_flags": sum(d.kind == "transcription" for r, d in dec if r["gold"] != "transcription"),
        "non_asr": len(rows) - len(asr),
        "lowc_flagged": sum(d.kind == "transcription" for r, d in dec if (r["sample"], r["turn"], r["quote"]) in lowc_ids),
        "lowc": len(lowc_ids),
    }


def _samples_report(rows: list[dict], settings: Settings, model: str) -> str:
    skip_min, asr_flag = settings.triage_skip_min, settings.triage_asr_flag
    m = _sample_metrics(rows, skip_min=skip_min, asr_flag=asr_flag)
    cm = Counter((r["gold"], r["kind"]) for r in rows)
    lines = [
        "# Jev triage eval: sample transcripts vs gold",
        "",
        f"Model `{model}`, skip_min {skip_min}, asr_flag {asr_flag}, {len(rows)} gold spans in "
        f"{len(SCENARIOS)} samples. Synthetic, AI-written gold labels (not pathologist-reviewed).",
        "",
        f"- **Claim recall: {_pct(m['claim_caught'], m['claims'])}** (a gold claim predicted `transcription` "
        "with base_kind `claim` counts as caught)",
        f"- **Transcription recall: {_pct(m['asr_caught'], m['asr'])}**",
        f"- Skip precision: {_pct(m['skip_correct'], m['pred_skip'])} (predicted skip that is gold skip)",
        f"- Skip recall: {_pct(m['skip_kept'], m['skips'])} (gold small talk kept away from the checker)",
        f"- False transcription flags on non-error spans: {_pct(m['false_flags'], m['non_asr'])}",
        f"- False-flag rate on spans with the correct low-confidence term: {_pct(m['lowc_flagged'], m['lowc'])}",
        "",
        "## Confusion matrix (rows = gold, columns = predicted kind)",
        "",
        "| gold \\ pred | " + " | ".join(LABELS) + " |",
        "|---|" + "---:|" * len(LABELS),
    ]
    for g in LABELS:
        lines.append(f"| {g} | " + " | ".join(str(cm[(g, p)]) for p in LABELS) + " |")

    lines += ["", "## Correct low-confidence terms", "", "| sample | term | quote | predicted | p_asr |", "|---|---|---|---|---:|"]
    for r in _low_conf_rows(rows):
        lines.append(f"| {r['sample']} | {r['term']} | {r['quote']} | {r['kind']} | {_f(r['p_asr'])} |")

    lines += ["", "## Misses (predicted kind differs from gold)", ""]
    for sc in SCENARIOS:
        miss = [r for r in rows if r["sample"] == sc.id and r["kind"] != r["gold"]]
        lines.append(f"### {sc.id} ({len(miss)})")
        if miss:
            lines += ["", "| turn | quote | gold | predicted | p_claim | p_asr |", "|---:|---|---|---|---:|---:|"]
            for r in miss:
                gold = r["gold"] + (f" ({r['gold_base']})" if r["gold"] == "transcription" else "")
                pred = r["kind"] + (f" ({r['base_kind']})" if r["kind"] == "transcription" else "")
                lines.append(f"| {r['turn']} | {r['quote']} | {gold} | {pred} | {r['p_claim']:.3f} | {_f(r['p_asr'])} |")
        lines.append("")

    lines += [
        "## Threshold sweep (replayed from the collected probabilities)",
        "",
        "skip_min (asr_flag fixed at the configured value):",
        "",
        "| skip_min | claim recall | skip precision | skip recall |",
        "|---:|---:|---:|---:|",
    ]
    for s in SKIP_SWEEP:
        x = _sample_metrics(rows, skip_min=s, asr_flag=asr_flag)
        lines.append(f"| {s} | {_pct(x['claim_caught'], x['claims'])} | {_pct(x['skip_correct'], x['pred_skip'])} | "
                     f"{_pct(x['skip_kept'], x['skips'])} |")
    lines += ["", "asr_flag (skip_min fixed at the configured value):", "",
              "| asr_flag | transcription recall | false flags | low-confidence term flagged |", "|---:|---:|---:|---:|"]
    for a in ASR_SWEEP:
        x = _sample_metrics(rows, skip_min=skip_min, asr_flag=a)
        lines.append(f"| {a} | {_pct(x['asr_caught'], x['asr'])} | {_pct(x['false_flags'], x['non_asr'])} | "
                     f"{_pct(x['lowc_flagged'], x['lowc'])} |")
    lines += ["", "## All spans", "", "| sample | turn | gold | pred | p_claim | p_asr | quote |", "|---|---:|---|---|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['sample']} | {r['turn']} | {r['gold']} | {r['kind']} | {r['p_claim']:.3f} | {_f(r['p_asr'])} | {r['quote']} |")
    return "\n".join(lines) + "\n"


def _f(p) -> str:
    return "-" if p is None else f"{p:.3f}"


def test_samples_vs_gold(settings):
    rows, model, calls = [], None, [0]
    with TestClient(app) as client:
        app.state.settings = settings
        app.state.jev_client_factory = _counting_factory(calls)
        for sc in SCENARIOS:
            got, model = _triage_sample(client, sc)
            rows += got

    REPORTS.mkdir(exist_ok=True)
    _save_run("samples", rows, calls[0], model)
    _report_path("jev_triage_samples", "json").write_text(json.dumps(rows, indent=1))
    report = _samples_report(rows, settings, model or settings.jev_model)
    _report_path("jev_triage_samples").write_text(report)
    _write_variants_summary(settings)
    print("\n" + report + f"\nJev calls: {calls[0]}\n")

    m = _sample_metrics(rows, skip_min=settings.triage_skip_min, asr_flag=settings.triage_asr_flag)
    assert m["claim_caught"] / m["claims"] >= CLAIM_RECALL_MIN, "claim recall below 0.95: see reports/jev_triage_samples.md"
    assert m["asr_caught"] / m["asr"] >= ASR_RECALL_MIN, "transcription recall below 0.8: see reports/jev_triage_samples.md"


# ---- b. Cancer-Myth -----------------------------------------------------------------------------------------


def _cancer_myth_rows() -> list[dict]:
    """All 585 rows, cached in backend/.cache/cancer_myth/ (fetched once via the datasets-server rows API)."""
    path = CACHE / "validation.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    rows = []
    with httpx.Client(timeout=30) as http:
        for offset in range(0, CM_ROWS, 100):
            for attempt in range(5):
                r = http.get(
                    "https://datasets-server.huggingface.co/rows",
                    params={"dataset": CM_DATASET, "config": "default", "split": "validation", "offset": offset, "length": 100},
                )
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            rows += [x["row"] for x in r.json()["rows"]]
            time.sleep(0.5)
    assert len(rows) == CM_ROWS, len(rows)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return rows


async def _classify_each(settings: Settings, statements: list[str], delay: float) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    async with default_jev_client_factory(settings) as client:
        for n, text in enumerate(statements):
            (a,) = await client.classify_spans(text, [], [SpanInput("0", text)], ask_asr=True)
            out[text] = (1.0 - a.p_small_talk, a.p_asr)
            if delay and n < len(statements) - 1:
                await asyncio.sleep(delay)
    return out


def _cm_report(rows: list[dict], probs: dict, settings: Settings, step: int, n_distinct: int) -> str:
    skip_min, asr_flag = settings.triage_skip_min, settings.triage_asr_flag

    def scored(s, a):
        return [(r, _replay(*probs[r["source_myth"]], skip_min=s, asr_flag=a)) for r in rows]

    dec = scored(skip_min, asr_flag)
    caught = sum(_caught_claim(d.kind, d.base_kind) for _, d in dec)
    distinct = {r["source_myth"]: d for r, d in dec}
    d_caught = sum(_caught_claim(d.kind, d.base_kind) for d in distinct.values())
    kinds = Counter(d.kind for _, d in dec)
    lines = [
        "# Jev triage eval: Cancer-Myth statements",
        "",
        f"`{CM_DATASET}` (CC-BY-4.0), split validation. Each `source_myth` statement triaged alone as one span "
        f"(ask_asr on), skip_min {skip_min}, asr_flag {asr_flag}. Excluded non-statements: "
        f"{', '.join(repr(x) for x in sorted(NON_STATEMENTS))}. "
        + (f"Subset: every {step}th distinct statement. " if step > 1 else "")
        + f"{len(rows)} rows, {n_distinct} distinct statements sent.",
        "",
        f"- **Claim recall (rows): {_pct(caught, len(rows))}**",
        f"- Claim recall (distinct statements): {_pct(d_caught, len(distinct))}",
        f"- Predicted kinds (rows): " + ", ".join(f"{k} {kinds[k]}" for k in LABELS),
        "",
        "## By category",
        "",
        "| category | rows | caught | recall | flagged transcription |",
        "|---|---:|---:|---:|---:|",
    ]
    for cat, n in Counter(r["category"] for r in rows).most_common():
        ds = [d for r, d in dec if r["category"] == cat]
        c = sum(_caught_claim(d.kind, d.base_kind) for d in ds)
        lines.append(f"| {cat} | {n} | {c} | {c / n:.1%} | {sum(d.kind == 'transcription' for d in ds)} |")

    misses = sorted(((t, d) for t, d in distinct.items() if not _caught_claim(d.kind, d.base_kind)), key=lambda x: x[1].p_claim)
    lines += ["", f"## Misses ({len(misses)} distinct statements)", "", "| statement | predicted | p_claim | p_asr |", "|---|---|---:|---:|"]
    for t, d in misses:
        lines.append(f"| {t} | {d.kind} | {d.p_claim:.3f} | {_f(d.p_asr)} |")

    closest = sorted(distinct.items(), key=lambda x: x[1].p_claim)[:10]
    lines += ["", "## Closest to skip (10 lowest p_claim)", "", "| statement | predicted | p_claim | p_asr |", "|---|---|---:|---:|"]
    for t, d in closest:
        lines.append(f"| {t} | {d.kind} | {d.p_claim:.3f} | {_f(d.p_asr)} |")

    lines += ["", "## Threshold sweep (rows; replayed from the collected probabilities)", "",
              "| skip_min | claim recall |", "|---:|---:|"]
    for s in SKIP_SWEEP:
        c = sum(_caught_claim(d.kind, d.base_kind) for _, d in scored(s, asr_flag))
        lines.append(f"| {s} | {_pct(c, len(rows))} |")
    lines += ["", "| asr_flag | statements flagged as transcription (all are false flags) |", "|---:|---:|"]
    for a in ASR_SWEEP:
        f = sum(d.kind == "transcription" for _, d in scored(skip_min, a))
        lines.append(f"| {a} | {_pct(f, len(rows))} |")
    return "\n".join(lines) + "\n"


def test_cancer_myth_statements_are_claims(settings):
    try:
        all_rows = _cancer_myth_rows()
    except httpx.HTTPError as e:
        pytest.skip(f"Hugging Face datasets-server unreachable: {type(e).__name__}")
    values = Counter(r["source_myth"] for r in all_rows)
    assert all(v.strip() for v in values), "empty source_myth"
    rows = [r for r in all_rows if r["source_myth"] not in NON_STATEMENTS]

    step = int(os.environ.get("JEV_EVAL_CM_STEP", "1"))
    delay = float(os.environ.get("JEV_EVAL_DELAY_S", "0.2"))
    statements = sorted({r["source_myth"] for r in rows})[::step]
    keep = set(statements)
    rows = [r for r in rows if r["source_myth"] in keep]

    probs = asyncio.run(_classify_each(settings, statements, delay))

    REPORTS.mkdir(exist_ok=True)
    _report_path("jev_triage_cancer_myth", "json").write_text(
        json.dumps([{"statement": t, "p_claim": p, "p_asr": a} for t, (p, a) in probs.items()], indent=1)
    )
    report = _cm_report(rows, probs, settings, step, len(statements))
    _report_path("jev_triage_cancer_myth").write_text(report)
    print("\n" + report)

    caught = sum(
        _caught_claim(d.kind, d.base_kind)
        for d in (_replay(*probs[r["source_myth"]], skip_min=settings.triage_skip_min, asr_flag=settings.triage_asr_flag) for r in rows)
    )
    assert caught / len(rows) >= CLAIM_RECALL_MIN, "Cancer-Myth claim recall below 0.95: see reports/jev_triage_cancer_myth.md"


# ---- c. hard cases + checker-test transcripts ---------------------------------------------------------------


def _hard_rows() -> list[dict]:
    return [json.loads(line) for line in HARD_CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _triage_hard(settings: Settings, cases: list[dict], delay: float, calls: list[int]) -> tuple[list[dict], str]:
    """Each case through router._triage_turn, the per-turn path of POST /triage (same spans, local skips, client,
    classify_spans call and decide thresholds); the event of the case's span is kept."""
    out = []
    async with _counting_factory(calls)(settings) as client:
        for n, case in enumerate(cases):
            turn = TriageTurn(id=case["id"], text=case["turn"])
            events = await router_module._triage_turn(client, turn, case["previous_doctor_turns"], settings)
            (e,) = [e for e in events if e["quote"] == case["span"]]
            out.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "quote": case["span"],
                    "gold": case["label"],
                    "kind": e["kind"],
                    "base_kind": e["base_kind"],
                    "p_claim": e["p_claim"],
                    "p_asr": e["p_asr"],
                }
            )
            if delay and n < len(cases) - 1:
                await asyncio.sleep(delay)
        model = client.model_used or settings.jev_model
    return out, model


def _correct(r: dict, d) -> bool:
    return _caught_claim(d.kind, d.base_kind) if r["gold"] == "claim" else d.kind == "skip"


def _hard_metrics(rows: list[dict], *, skip_min: float, asr_flag: float) -> dict:
    dec = [(r, _replay(r["p_claim"], r["p_asr"], skip_min=skip_min, asr_flag=asr_flag)) for r in rows]
    m = {
        "claim_caught": sum(_caught_claim(d.kind, d.base_kind) for r, d in dec if r["gold"] == "claim"),
        "claims": sum(r["gold"] == "claim" for r in rows),
        "skip_correct": sum(r["gold"] == "skip" for r, d in dec if d.kind == "skip"),
        "pred_skip": sum(d.kind == "skip" for _, d in dec),
        "skip_kept": sum(d.kind == "skip" for r, d in dec if r["gold"] == "skip"),
        "claim_pred": sum(_caught_claim(d.kind, d.base_kind) for _, d in dec),
        "claim_pred_correct": sum(_caught_claim(d.kind, d.base_kind) for r, d in dec if r.get("gold_base", r["gold"]) == "claim"),
        "skips": sum(r["gold"] == "skip" for r in rows),
        "flags": sum(d.kind == "transcription" for _, d in dec),
    }
    for cat in (*HARD_SKIP_CATEGORIES, *HARD_CLAIM_CATEGORIES):
        m[cat] = (sum(_correct(r, d) for r, d in dec if r["category"] == cat), sum(r["category"] == cat for r in rows))
    return m


def _ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def _rates(m: dict) -> dict[str, float | None]:
    """Ratios from _hard_metrics or _sample_metrics (the keys they share mean the same)."""
    out = {
        "claim recall": _ratio(m["claim_caught"], m["claims"]),
        "skip precision": _ratio(m["skip_correct"], m["pred_skip"]),
        "skip recall": _ratio(m["skip_kept"], m["skips"]),
        "claim precision": _ratio(m["claim_pred_correct"], m["claim_pred"]),
    }
    if "asr" in m:
        out["transcription recall"] = _ratio(m["asr_caught"], m["asr"])
    for cat in (*HARD_SKIP_CATEGORIES, *HARD_CLAIM_CATEGORIES):
        if cat in m:
            out[cat] = _ratio(*m[cat])
    return out


def _spread_of(runs: list[dict], metric, key: str) -> str:
    values = [v for v in (_rates(metric(run["rows"])).get(key) for run in runs) if v is not None]
    return _spread(values)


def _hard_report(rows, checker_rows, settings: Settings, model: str, calls: int) -> str:
    skip_min, asr_flag = settings.triage_skip_min, settings.triage_asr_flag
    m = _hard_metrics(rows, skip_min=skip_min, asr_flag=asr_flag)
    cm = _sample_metrics(checker_rows, skip_min=skip_min, asr_flag=asr_flag)
    runs, checker_runs = _load_runs("hard"), _load_runs("checker")

    def hm(rs):
        return _hard_metrics(rs, skip_min=skip_min, asr_flag=asr_flag)

    def sm(rs):
        return _sample_metrics(rs, skip_min=skip_min, asr_flag=asr_flag)

    lines = [
        "# Jev triage eval: hard cases",
        "",
        f"Variant `{VARIANT}`, model `{model}`, skip_min {skip_min}, asr_flag {asr_flag}. {len(rows)} hard cases "
        f"(`evals/triage/hard_cases.jsonl`) through `router._triage_turn`, plus {len(checker_rows)} spans of the "
        f"{len(checker_tests.SCENARIOS)} checker-test transcripts through POST /triage. Jev calls this run: {calls}. "
        "Synthetic, AI-written gold labels (not pathologist-reviewed).",
        "",
        "## This run",
        "",
        f"- **Hard cases claim recall: {_pct(m['claim_caught'], m['claims'])}**",
        f"- Hard cases skip precision: {_pct(m['skip_correct'], m['pred_skip'])}",
        f"- Hard cases skip recall: {_pct(m['skip_kept'], m['skips'])}",
        f"- Hard cases flagged as transcription: {m['flags']}",
        f"- **Checker tests claim recall: {_pct(cm['claim_caught'], cm['claims'])}**, transcription recall "
        f"{_pct(cm['asr_caught'], cm['asr'])}, skip precision {_pct(cm['skip_correct'], cm['pred_skip'])}, skip recall "
        f"{_pct(cm['skip_kept'], cm['skips'])}",
        "",
        "| category | gold | correct |",
        "|---|---|---:|",
    ]
    for cat in (*HARD_SKIP_CATEGORIES, *HARD_CLAIM_CATEGORIES):
        gold = "skip" if cat in HARD_SKIP_CATEGORIES else "claim"
        lines.append(f"| {cat} | {gold} | {_pct(*m[cat])} |")

    keys = ["claim recall", "claim precision", "skip precision", "skip recall", *HARD_SKIP_CATEGORIES, *HARD_CLAIM_CATEGORIES]
    lines += [
        "",
        f"## All saved runs of `{VARIANT}` ({len(runs)} hard, {len(checker_runs)} checker): mean (range)",
        "",
        "| metric | hard cases | checker tests |",
        "|---|---:|---:|",
    ]
    for k in [*keys, "transcription recall"]:
        lines.append(f"| {k} | {_spread_of(runs, hm, k)} | {_spread_of(checker_runs, sm, k)} |")

    lines += ["", "## Hard-case misses across saved runs", "", "| id | category | gold | missed | p_claim per run | span |",
              "|---|---|---|---:|---|---|"]
    by_id: dict[str, list[tuple[dict, bool]]] = {}
    for run in runs:
        for r in run["rows"]:
            d = _replay(r["p_claim"], r["p_asr"], skip_min=skip_min, asr_flag=asr_flag)
            by_id.setdefault(r["id"], []).append((r, _correct(r, d)))
    for rid, results in by_id.items():
        missed = sum(not ok for _, ok in results)
        if missed:
            r = results[0][0]
            ps = ", ".join(f"{x['p_claim']:.2f}" for x, _ in results)
            lines.append(f"| {rid} | {r['category']} | {r['gold']} | {missed}/{len(results)} | {ps} | {r['quote']} |")

    lines += ["", "## Checker-test misses (this run)", "", "| sample | quote | gold | predicted | p_claim | p_asr |",
              "|---|---|---|---|---:|---:|"]
    for r in checker_rows:
        if r["kind"] != r["gold"]:
            lines.append(f"| {r['sample']} | {r['quote']} | {r['gold']} | {r['kind']} | {r['p_claim']:.3f} | {_f(r['p_asr'])} |")

    lines += ["", "## Threshold sweep (all saved runs, replayed): mean (range)", "",
              "| skip_min | hard claim recall | hard skip precision | hard skip recall | checker claim recall |",
              "|---:|---:|---:|---:|---:|"]
    for s in SKIP_SWEEP:
        def hs(rs, s=s):
            return _hard_metrics(rs, skip_min=s, asr_flag=asr_flag)

        def cs(rs, s=s):
            return _sample_metrics(rs, skip_min=s, asr_flag=asr_flag)

        lines.append(f"| {s} | {_spread_of(runs, hs, 'claim recall')} | {_spread_of(runs, hs, 'skip precision')} | "
                     f"{_spread_of(runs, hs, 'skip recall')} | {_spread_of(checker_runs, cs, 'claim recall')} |")

    lines += ["", "## All hard cases (this run)", "", "| id | category | gold | pred | p_claim | p_asr | span |",
              "|---|---|---|---|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['category']} | {r['gold']} | {r['kind']} | {r['p_claim']:.3f} | {_f(r['p_asr'])} | {r['quote']} |")
    return "\n".join(lines) + "\n"


def _write_variants_summary(settings: Settings) -> None:
    """reports/jev_triage_variants.md: every variant with saved runs, all three sets, mean (range) over runs."""
    skip_min, asr_flag = settings.triage_skip_min, settings.triage_asr_flag

    def hm(rs):
        return _hard_metrics(rs, skip_min=skip_min, asr_flag=asr_flag)

    def sm(rs):
        return _sample_metrics(rs, skip_min=skip_min, asr_flag=asr_flag)

    common = ["claim recall", "claim precision", "skip precision", "skip recall"]
    sets = [
        ("hard", hm, [*common, *HARD_SKIP_CATEGORIES, *HARD_CLAIM_CATEGORIES]),
        ("checker", sm, [*common, "transcription recall"]),
        ("samples", sm, [*common, "transcription recall"]),
    ]
    lines = ["# Jev triage variants (issue #57)", "", f"skip_min {skip_min}, asr_flag {asr_flag}. Mean (range) over saved runs.", ""]
    for name, metric, keys in sets:
        present = [(v, runs) for v, runs in ((v, _load_runs(name, v)) for v in VARIANTS) if runs]
        if not present:
            continue
        lines += [f"## {name}", "", "| metric | " + " | ".join(f"{v} ({len(r)} runs)" for v, r in present) + " |",
                  "|---|" + "---:|" * len(present)]
        for k in keys:
            lines.append(f"| {k} | " + " | ".join(_spread_of(r, metric, k) for _, r in present) + " |")
        calls = " | ".join(f"{statistics.mean(x['jev_calls'] for x in r):.0f}" for _, r in present)
        lines += [f"| Jev calls per run | {calls} |", ""]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "jev_triage_variants.md").write_text("\n".join(lines) + "\n")


def test_hard_cases(settings):
    delay = float(os.environ.get("JEV_EVAL_DELAY_S", "0.2"))
    hard_calls, checker_calls = [0], [0]
    rows, model = asyncio.run(_triage_hard(settings, _hard_rows(), delay, hard_calls))

    checker_rows = []
    with TestClient(app) as client:
        app.state.settings = settings
        app.state.jev_client_factory = _counting_factory(checker_calls)
        for sc in checker_tests.SCENARIOS:
            got, _ = _triage_checker_test(client, sc)
            checker_rows += got

    REPORTS.mkdir(exist_ok=True)
    _save_run("hard", rows, hard_calls[0], model)
    _save_run("checker", checker_rows, checker_calls[0], model)
    report = _hard_report(rows, checker_rows, settings, model, hard_calls[0] + checker_calls[0])
    _report_path("jev_triage_hard_cases").write_text(report)
    _write_variants_summary(settings)
    print("\n" + report)

    m = _hard_metrics(rows, skip_min=settings.triage_skip_min, asr_flag=settings.triage_asr_flag)
    cm = _sample_metrics(checker_rows, skip_min=settings.triage_skip_min, asr_flag=settings.triage_asr_flag)
    assert m["claim_caught"] / m["claims"] >= CLAIM_RECALL_MIN, "hard-case claim recall below 0.95: see reports/jev_triage_hard_cases.md"
    assert cm["claim_caught"] / cm["claims"] >= CLAIM_RECALL_MIN, "checker claim recall below 0.95: see reports/jev_triage_hard_cases.md"
