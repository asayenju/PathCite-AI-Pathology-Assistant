"""Opt-in smoke eval: `pytest -m hf -s` (needs network, skipped by default).

Samples 50 images per class (seed 42) from dpdl-benchmark/colorectal_histology (Kather 2016,
8 classes, 150x150) and sends them through the real POST /predict endpoint.

The dataset is NOT downloaded: row metadata for the first 2000 rows is scanned through the Hub
datasets-server /rows API and only the 400 sampled images are fetched (as the server's small JPEG
renderings, cached in backend/.cache/). Set HF_TOKEN for higher rate limits.

This is a domain-shift smoke check, not a benchmark. Differences from the training data:
150px upscaled to 224, no Macenko normalization, and JPEG-compressed renderings of the
original TIFFs. 50 per class gives wide error bars. The report is informational.
"""
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.classes import CLASS_NAMES
from app.main import app

pytestmark = pytest.mark.hf

DATASET = "dpdl-benchmark/colorectal_histology"
SEED = 42
PER_CLASS = 50
SCAN_ROWS = 2000  # the dataset is shuffled, so the first 2000 rows (~250/class) are a fair pool
BACKEND = Path(__file__).resolve().parents[1]
CACHE = BACKEND / ".cache" / "hf_smoke"
REPORT = BACKEND / "reports" / "hf_smoke.md"

# Label order verified by eye on sample images (tumor = dense atypical epithelium, label 7 = blank slide...).
HF_LABELS = {0: "tumor", 1: "stroma", 2: "complex", 3: "lympho", 4: "debris", 5: "mucosa", 6: "adipose", 7: "empty"}
# Hugging Face label -> our model's class. "complex" (2) has no equivalent: reported, not scored.
TO_MODEL = {0: "TUM", 1: "STR", 3: "LYM", 4: "DEB", 5: "NORM", 6: "ADI", 7: "BACK"}


def _get(url: str, retries: int = 6) -> bytes:
    headers = {}
    if os.environ.get("HF_TOKEN"):  # optional: higher rate limits
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                retry_after = e.headers.get("Retry-After", "")
                time.sleep(min(int(retry_after), 60) if retry_after.isdigit() else 5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise


def _scan_rows() -> dict[int, list[tuple[int, str]]]:
    """label -> [(row_idx, image_url)] from the /rows metadata (no image bytes)."""
    by_label: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for offset in range(0, SCAN_ROWS, 100):
        q = urllib.parse.urlencode(
            {"dataset": DATASET, "config": "default", "split": "train", "offset": offset, "length": 100}
        )
        page = json.loads(_get(f"https://datasets-server.huggingface.co/rows?{q}"))
        for r in page["rows"]:
            by_label[r["row"]["label"]].append((r["row_idx"], r["row"]["image"]["src"]))
        time.sleep(0.5)  # be gentle with the unauthenticated rate limit
    return by_label


def _fetch_sample() -> list[tuple[int, int, bytes]]:
    by_label = _scan_rows()
    assert sorted(by_label) == sorted(HF_LABELS), f"unexpected labels: {sorted(by_label)}"
    rng = random.Random(SEED)
    picks = [(lab, idx, url) for lab in sorted(by_label) for idx, url in rng.sample(by_label[lab], PER_CLASS)]
    CACHE.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        lab, idx, url = item
        f = CACHE / f"{lab}_{idx}.img"
        if not f.exists():
            f.write_bytes(_get(url))
        return lab, idx, f.read_bytes()

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(fetch, picks))


def _report(results: list[dict]) -> str:
    cols = list(CLASS_NAMES)
    lines = [f"# HF smoke eval: {DATASET}", "",
             f"{PER_CLASS} images per class, seed {SEED}, {len(results)} total, via POST /predict.", "",
             "| HF class | expected | n | correct | recall | mean conf | uncertain |", "|---|---|---:|---:|---:|---:|---:|"]
    cm = {lab: Counter() for lab in HF_LABELS}
    for r in results:
        cm[r["true"]][r["tissue"]] += 1
    scored_total = scored_correct = 0
    for lab, name in HF_LABELS.items():
        rs = [r for r in results if r["true"] == lab]
        exp = TO_MODEL.get(lab)
        n = len(rs)
        conf = sum(r["confidence"] for r in rs) / n
        unc = sum(r["uncertain"] for r in rs)
        if exp:
            ok = sum(r["tissue"] == exp for r in rs)
            scored_total += n
            scored_correct += ok
            lines.append(f"| {lab} {name} | {exp} | {n} | {ok} | {ok / n:.0%} | {conf:.2f} | {unc}/{n} |")
        else:
            lines.append(f"| {lab} {name} | (none, not scored) | {n} | - | - | {conf:.2f} | {unc}/{n} |")
    lines += ["", f"**Overall accuracy on the 7 mapped classes: {scored_correct}/{scored_total} = "
              f"{scored_correct / scored_total:.1%}**  (95% CI roughly +/-4% at this size)", "",
              "## Confusion matrix (rows = HF class, columns = model prediction)", "",
              "| true \\ pred | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    for lab, name in HF_LABELS.items():
        lines.append(f"| {lab} {name} | " + " | ".join(str(cm[lab][c]) for c in cols) + " |")
    return "\n".join(lines)


def test_smoke_eval_50_per_class():
    try:
        sample = _fetch_sample()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        pytest.skip(f"Hugging Face datasets-server unreachable: {e}")

    assert Counter(lab for lab, _, _ in sample) == {lab: PER_CLASS for lab in HF_LABELS}

    results = []
    with TestClient(app) as client:
        for lab, idx, data in sample:
            r = client.post("/predict", files={"file": (f"{lab}_{idx}.jpg", data, "image/jpeg")})
            assert r.status_code == 200, (lab, idx, r.text)
            body = r.json()
            assert sum(body["probabilities"].values()) == pytest.approx(1.0, abs=1e-4)
            # Every image is 150x150, so the "not ~224" warning must always be present.
            assert any("150x150" in w for w in body["warnings"]), body["warnings"]
            results.append({**body, "true": lab})  # body["label"] is the *predicted* index

    report = _report(results)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(report)
    print("\n" + report)

    assert set(TO_MODEL.values()) <= set(CLASS_NAMES)
    # No accuracy threshold on purpose: see module docstring.


# ---- Control: in-distribution images through the same API path ----
# Without this, a low score on the external dataset could be mistaken for an API bug.

CONTROL_DATASET = "chehablab/NCT-CRC-HE-100K"  # the notebook's external test split (95.4% there)
# First row of each class block in that split (class-sorted; sizes from the notebook's report).
CONTROL_STARTS = [0, 1338, 2185, 2524, 3158, 4193, 4785, 5526, 5947]
CONTROL_PER_CLASS = 20
CONTROL_FLOOR = 0.60  # chance is 11%; the first run scored ~82% on these JPEG renderings


def test_control_in_distribution_images_score_well():
    correct = total = 0
    try:
        with TestClient(app) as client:
            for lab, start in enumerate(CONTROL_STARTS):
                q = urllib.parse.urlencode({"dataset": CONTROL_DATASET, "config": "default", "split": "test",
                                            "offset": start + 20, "length": CONTROL_PER_CLASS})
                rows = json.loads(_get(f"https://datasets-server.huggingface.co/rows?{q}"))["rows"]
                time.sleep(0.5)
                for r in rows:
                    row = r["row"]
                    # Also re-verifies the index -> tissue map (issue 1) live, for all 9 classes.
                    assert (row["label"], row["tissue"]) == (lab, CLASS_NAMES[lab])
                    f = CACHE / f"control_{r['row_idx']}.img"
                    CACHE.mkdir(parents=True, exist_ok=True)
                    if not f.exists():
                        f.write_bytes(_get(row["image"]["src"]))
                    resp = client.post("/predict", files={"file": (f.name, f.read_bytes(), "image/jpeg")})
                    assert resp.status_code == 200
                    correct += resp.json()["label"] == lab
                    total += 1
    except (urllib.error.URLError, TimeoutError) as e:
        pytest.skip(f"Hugging Face datasets-server unreachable: {e}")
    accuracy = correct / total
    print(f"\nControl (NCT-CRC test split, {total} images): {correct}/{total} = {accuracy:.1%}")
    assert accuracy >= CONTROL_FLOOR, "serving path looks broken: in-distribution images score near chance"
