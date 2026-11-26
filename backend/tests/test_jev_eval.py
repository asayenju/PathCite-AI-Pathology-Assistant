"""Opt-in live eval of Jev triage: `pytest -m jev -s` (needs JEV_KEY and network; skipped by default).

a. Samples vs gold. Each of the 9 sample transcripts (frontend/public/transcripts/) is imported through
   POST /transcripts/import, its doctor turns (the speaker suggested as doctor) go through the real POST /triage
   (real JevClient, configured thresholds), and the NDJSON spans are aligned with the gold labels in
   backend/evals/triage/keys/ by (doctor_turn_index, start, end). Report: reports/jev_triage_samples.md.

b. Cancer-Myth (Cancer-Myth/Cancer-Myth, CC-BY-4.0, split validation). Every `source_myth` that is a real
   statement (not "From physicians.") is triaged as one span on its own, ask_asr=True, with `decide` and the
   configured thresholds. Every myth is a general medical assertion, so each should be a claim. Duplicate
   statements are sent once and scored per row. Report: reports/jev_triage_cancer_myth.md.

Both reports include a threshold sweep replayed from the probabilities already collected (no extra calls).

Claim recall rule: a gold claim counts as caught when it is predicted `claim`, or `transcription` with
base_kind `claim` (the doctor's "Yes, that's correct" then sends it to the checker). A gold claim predicted
`transcription` with base_kind `skip` is a miss.

Env:
- JEV_KEY: the key (or the repo-root .env, as for the app). Never printed or written.
- JEV_EVAL_CM_STEP: send every Nth distinct Cancer-Myth statement (default 1 = all), for rate limits.
- JEV_EVAL_DELAY_S: pause between Cancer-Myth requests (default 0.2 s). Requests are sequential; the client
  retries 429 with retry-after.

The sample transcripts and their gold labels are synthetic and AI-written, not reviewed by a pathologist.
"""

import asyncio
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.triage.decide import decide
from app.triage.jev import SpanInput
from app.triage.router import default_jev_client_factory
from tests.fixtures.transcripts import generate
from tests.fixtures.transcripts.scenarios import SCENARIOS

pytestmark = pytest.mark.jev

BACKEND = Path(__file__).resolve().parents[1]
REPORTS = BACKEND / "reports"
CACHE = BACKEND / ".cache" / "cancer_myth"

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


def _triage_sample(client: TestClient, sc) -> list[dict]:
    """Rows of {sample, gold..., kind, base_kind, p_claim, p_asr} for every gold span of one sample."""
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
    index = {t["id"]: i for i, t in enumerate(turns)}
    predicted = {(index[e["turn_id"]], e["start"], e["end"]): e for e in events if e["type"] == "span"}

    rows = []
    for g in _gold(sc.id)["spans"]:
        e = predicted[(g["doctor_turn_index"], g["start"], g["end"])]
        assert e["quote"] == g["quote"]
        rows.append(
            {
                "sample": sc.id,
                "turn": g["doctor_turn_index"],
                "quote": g["quote"],
                "gold": g["label"],
                "gold_base": g.get("base_kind", g["label"]),
                "kind": e["kind"],
                "base_kind": e["base_kind"],
                "p_claim": e["p_claim"],
                "p_asr": e["p_asr"],
            }
        )
    return rows, events[-1].get("model")


def _low_conf_rows(rows: list[dict]) -> list[dict]:
    """Gold non-transcription spans containing a sample's correct low-confidence term (e.g. "sigmoid")."""
    terms = {sc.id: list(sc.low_confidence) for sc in SCENARIOS}
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
    lowc = _low_conf_rows(rows)
    lowc_ids = {id(r) for r in lowc}
    return {
        "claim_caught": sum(_caught_claim(d.kind, d.base_kind) for r, d in dec if r["gold"] == "claim"),
        "claims": len(claims),
        "asr_caught": sum(d.kind == "transcription" for r, d in dec if r["gold"] == "transcription"),
        "asr": len(asr),
        "skip_correct": sum(r["gold"] == "skip" for r in pred_skip),
        "pred_skip": len(pred_skip),
        "skip_kept": sum(d.kind == "skip" for r, d in dec if r["gold"] == "skip"),
        "skips": len(skips),
        "false_flags": sum(d.kind == "transcription" for r, d in dec if r["gold"] != "transcription"),
        "non_asr": len(rows) - len(asr),
        "lowc_flagged": sum(d.kind == "transcription" for r, d in dec if id(r) in lowc_ids),
        "lowc": len(lowc),
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
    rows, model = [], None
    with TestClient(app) as client:
        app.state.settings = settings
        app.state.jev_client_factory = default_jev_client_factory
        for sc in SCENARIOS:
            got, model = _triage_sample(client, sc)
            rows += got

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "jev_triage_samples.json").write_text(json.dumps(rows, indent=1))
    report = _samples_report(rows, settings, model or settings.jev_model)
    (REPORTS / "jev_triage_samples.md").write_text(report)
    print("\n" + report)

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
    (REPORTS / "jev_triage_cancer_myth.json").write_text(
        json.dumps([{"statement": t, "p_claim": p, "p_asr": a} for t, (p, a) in probs.items()], indent=1)
    )
    report = _cm_report(rows, probs, settings, step, len(statements))
    (REPORTS / "jev_triage_cancer_myth.md").write_text(report)
    print("\n" + report)

    caught = sum(
        _caught_claim(d.kind, d.base_kind)
        for d in (_replay(*probs[r["source_myth"]], skip_min=settings.triage_skip_min, asr_flag=settings.triage_asr_flag) for r in rows)
    )
    assert caught / len(rows) >= CLAIM_RECALL_MIN, "Cancer-Myth claim recall below 0.95: see reports/jev_triage_cancer_myth.md"
