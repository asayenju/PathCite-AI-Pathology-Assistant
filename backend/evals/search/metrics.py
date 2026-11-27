"""Retrieval metrics for the search eval (issue #53). Pure: no I/O.

Inputs, per claim:
- `ranked`: chunk ids as a search returned them, best first. A repeated id counts once (its first position).
- `grades`: chunk_id -> 0, 1 or 2 from the judge (gold chunks already forced to 2). Unjudged ids count as 0.
  2 = settles the claim, 1 = same topic but doesn't settle it, 0 = unrelated.

Choices (see README.md):
- Precision@k divides by min(k, number returned), so a search that returns 3 good passages and stops isn't
  punished for the 5 it didn't return. How often fewer than k came back is reported separately (`short@k`).
  Precision of an empty result is undefined (None).
- Recall@k is undefined (None) when no grade-2 chunk is known for the claim; NDCG@k is undefined when no chunk has
  a grade above 0. Undefined values are left out of the means.
- The ideal DCG uses every judged grade for the claim (the whole pool), not only what this search returned.
- `aggregate` averages over covered claims (supported, contradicted) only. `not_covered` claims have nothing to
  find, so they are scored by `noise` instead: mean lenient precision and the share with any grade-2 passage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from math import log2

KS = (1, 3, 5, 8)
MRR_CUTOFF = 8
COVERED = ("supported", "contradicted")
NOT_COVERED = "not_covered"


def _check_k(k: int) -> None:
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError("k must be a positive integer")


def _top(ranked: Sequence[int], k: int) -> list[int]:
    _check_k(k)
    out: list[int] = []
    seen: set[int] = set()
    for cid in ranked:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        if len(out) == k:
            break
    return out


def _grade(grades: Mapping[int, int], cid: int) -> int:
    return grades.get(cid, 0)


def _relevant(grades: Mapping[int, int]) -> int:
    return sum(1 for g in grades.values() if g == 2)


def hit_at(ranked: Sequence[int], grades: Mapping[int, int], k: int) -> float:
    """1.0 if any grade-2 chunk is in the top k, else 0.0."""
    return 1.0 if any(_grade(grades, c) == 2 for c in _top(ranked, k)) else 0.0


def recall_at(ranked: Sequence[int], grades: Mapping[int, int], k: int) -> float | None:
    """Grade-2 chunks in the top k / grade-2 chunks known for the claim. None when none is known."""
    top = _top(ranked, k)
    known = _relevant(grades)
    if known == 0:
        return None
    return sum(1 for c in top if _grade(grades, c) == 2) / known


def precision_at(ranked: Sequence[int], grades: Mapping[int, int], k: int, *, min_grade: int = 2) -> float | None:
    """Share of the top k with grade >= min_grade (2: strict, 1: lenient), over min(k, number returned)."""
    top = _top(ranked, k)
    if not top:
        return None
    return sum(1 for c in top if _grade(grades, c) >= min_grade) / len(top)


def mrr(ranked: Sequence[int], grades: Mapping[int, int], cutoff: int = MRR_CUTOFF) -> float:
    """1 / rank of the first grade-2 chunk within the top `cutoff`, else 0."""
    for rank, cid in enumerate(_top(ranked, cutoff), 1):
        if _grade(grades, cid) == 2:
            return 1.0 / rank
    return 0.0


def _dcg(gains: Iterable[int]) -> float:
    return sum(g / log2(rank + 1) for rank, g in enumerate(gains, 1))


def ndcg_at(ranked: Sequence[int], grades: Mapping[int, int], k: int) -> float | None:
    """NDCG@k with gain = grade and a log2(rank + 1) discount. None when no judged chunk has a grade above 0."""
    top = _top(ranked, k)
    ideal = _dcg(sorted(grades.values(), reverse=True)[:k])
    if ideal == 0:
        return None
    return _dcg(_grade(grades, c) for c in top) / ideal


def best_rank(ranked: Sequence[int], grades: Mapping[int, int]) -> int | None:
    """1-based rank of the first grade-2 chunk in the whole list, None if there is none."""
    for rank, cid in enumerate(dict.fromkeys(ranked), 1):
        if _grade(grades, cid) == 2:
            return rank
    return None


def score_claim(ranked: Sequence[int], grades: Mapping[int, int], ks: Sequence[int] = KS) -> dict:
    """Every metric for one claim: hit@k, recall@k, p_strict@k, p_lenient@k, ndcg@k, short@k (fewer than k
    returned) for each k, and mrr."""
    out: dict = {}
    n_returned = len(dict.fromkeys(ranked))
    for k in ks:
        out[f"hit@{k}"] = hit_at(ranked, grades, k)
        out[f"recall@{k}"] = recall_at(ranked, grades, k)
        out[f"p_strict@{k}"] = precision_at(ranked, grades, k)
        out[f"p_lenient@{k}"] = precision_at(ranked, grades, k, min_grade=1)
        out[f"ndcg@{k}"] = ndcg_at(ranked, grades, k)
        out[f"short@{k}"] = n_returned < k
    out["mrr"] = mrr(ranked, grades)
    return out


def mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


MEAN_METRICS = ("hit", "recall", "p_strict", "p_lenient", "ndcg")


def aggregate(claims: Sequence[Mapping], ks: Sequence[int] = KS) -> dict:
    """Means over covered claims. Each claim is a mapping with `type`, `ranked` and `grades`.
    Returns n, every `<metric>@k` mean (None when nothing is defined), `mrr`, and `short@k` as a count."""
    scored = [score_claim(c["ranked"], c["grades"], ks) for c in claims if c["type"] in COVERED]
    out: dict = {"n": len(scored)}
    for k in ks:
        for m in MEAN_METRICS:
            out[f"{m}@{k}"] = mean(s[f"{m}@{k}"] for s in scored)
        out[f"short@{k}"] = sum(1 for s in scored if s[f"short@{k}"])
    out["mrr"] = mean(s["mrr"] for s in scored)
    return out


def noise(claims: Sequence[Mapping], k: int = 8) -> dict:
    """For not_covered claims only: mean lenient precision@k and the share of claims with any grade-2 chunk in
    the top k (both should be about 0)."""
    nc = [c for c in claims if c["type"] == NOT_COVERED]
    if not nc:
        return {"n": 0, "p_lenient": None, "any_relevant_share": None}
    return {
        "n": len(nc),
        "p_lenient": mean(precision_at(c["ranked"], c["grades"], k, min_grade=1) for c in nc),
        "any_relevant_share": sum(hit_at(c["ranked"], c["grades"], k) for c in nc) / len(nc),
    }
