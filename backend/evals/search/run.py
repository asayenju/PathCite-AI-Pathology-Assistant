"""Search eval runner (issue #53). From backend/:

    python -m evals.search.run [--golden PATH] [--limit N] [--variants v1_hybrid_claim,...]
                               [--refresh [queries,search,judge]] [--env PATH] [--concurrency 4]

For every golden claim it runs four search variants, pools their top 10 with the gold chunks, has gpt-6-luna grade
the pool 0/1/2 in one call per claim, scores the variants (metrics.py) and writes results/<timestamp>.json (all
per-claim data, gitignored) and results/<timestamp>.md (the report, committed). See README.md.

Variants (k=8 scored; each search fetches 10 for the pool):
- v1_hybrid_claim:   match_chunks(claim), the old /check flow
- v2_hybrid_queries: claim + Luna's queries (generate_queries), each through match_chunks, merged with
                     merge_results, the current /check flow
- vec_claim:         match_chunks(claim) with query_text "" (no keyword list: vector only)
- vec_queries:       the same queries, vector only, merged with merge_results

The scored top 8 of a multi-query variant merges each query's top 8, exactly like /check (the SQL orders and then
limits, so a query's top 8 is the first 8 of its top 10). The pool list merges the full top 10s.

Caches (cache/, gitignored): Luna's queries per claim id, search results per (mode, query), chunk text per id, and
judgements per (claim id, chunk id). Queries and judgements are dropped when the claim (or its context/needed note)
changes; search, chunk and judgement caches are dropped when the corpus fingerprint (corpus_meta) changes.

Privacy: the claims are synthetic. Still, nothing is logged but exception type names; keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError

from app.check.llm import LLMError, LLMUpstreamError
from app.check.queries import merge_results
from app.check.search import Evidence
from evals.search.metrics import COVERED, KS, aggregate, best_rank, noise, score_claim

HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = HERE / "golden.jsonl"
DEFAULT_CACHE = HERE / "cache"
DEFAULT_RESULTS = HERE / "results"

POOL_K = 10
SCORE_K = 8
MAIN_VARIANT = "v2_hybrid_queries"
# name -> (what is searched, vector only)
VARIANTS: dict[str, tuple[str, bool]] = {
    "v1_hybrid_claim": ("claim", False),
    "v2_hybrid_queries": ("queries", False),
    "vec_claim": ("claim", True),
    "vec_queries": ("queries", True),
}
REFRESH_CHOICES = ("queries", "search", "judge")

# USD per 1M tokens
PRICE_LLM_IN = 0.10
PRICE_LLM_OUT = 0.50
PRICE_EMBED = 0.02

MAX_PASSAGE_CHARS = 3000
JUDGE_SCHEMA_NAME = "relevance_grades"


# --- golden set --------------------------------------------------------------------


class GoldenRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictStr
    cancer: StrictStr
    type: Literal["supported", "contradicted", "not_covered"]
    claim: StrictStr
    context: list[StrictStr]
    needed: StrictStr
    gold_chunk_ids: list[StrictInt]
    style: Literal["technical", "lay"]
    reviewed: StrictBool


def load_golden(path: Path) -> list[GoldenRow]:
    """One JSON object per line; blank lines skipped. Raises ValueError naming the line (never echoing it)."""
    rows: list[GoldenRow] = []
    seen: set[str] = set()
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = GoldenRow.model_validate(json.loads(line))
        except (ValueError, ValidationError) as e:
            fields = ""
            if isinstance(e, ValidationError):
                fields = ": " + ", ".join(".".join(map(str, err["loc"])) or "row" for err in e.errors())
            raise ValueError(f"{path} line {n}: invalid golden row{fields}") from None
        if r.id in seen:
            raise ValueError(f"{path} line {n}: duplicate id {r.id!r}")
        seen.add(r.id)
        rows.append(r)
    return rows


# --- ports -------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkInfo:
    chunk_id: int
    doc_id: str
    title: str | None
    section: str
    content: str


class Planner(Protocol):
    async def generate(self, claim: str, context: Sequence[str]) -> list[str]:
        """Luna's queries (without the claim). Raises LLMError on failure."""


class Searcher(Protocol):
    async def search(self, query: str, *, vector_only: bool, k: int) -> list[Evidence]: ...
    async def fetch_chunks(self, ids: Sequence[int]) -> list[ChunkInfo]: ...
    async def fingerprint(self) -> str: ...


class Judge(Protocol):
    async def grade(self, row: GoldenRow, chunks: Sequence[ChunkInfo]) -> dict:
        """The judge's parsed JSON answer: {"grades": [{"chunk_id": int, "grade": 0|1|2}, ...]}."""


# --- judge prompt ------------------------------------------------------------------

JUDGE_INSTRUCTIONS = """\
You grade search results for a medical evidence search engine (cancer guidelines and paper abstracts).

You get a CLAIM (a general medical statement), optional CONTEXT (earlier sentences, only to tell what the claim \
refers to), a NEEDED note describing the information that would settle the claim, and numbered PASSAGES, each \
with its chunk_id, document title and section.

Grade every passage, by its chunk_id:
- 2: the passage contains information that confirms or refutes this specific claim (the NEEDED note describes \
it). It need not use the same words, and it counts even if it contradicts the claim.
- 1: the same topic (same cancer and subject), but it does not settle the claim.
- 0: unrelated, or about a different cancer or subtype, or too vague to help.

Judge each passage on its own text only. Do not use your own knowledge to decide whether the claim is true. Return \
one grade for every passage, using the chunk_id shown.
"""

JUDGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "grades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "integer"},
                    "grade": {"type": "integer", "enum": [0, 1, 2]},
                },
                "required": ["chunk_id", "grade"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["grades"],
    "additionalProperties": False,
}


def build_judge_input(row: GoldenRow, chunks: Sequence[ChunkInfo]) -> str:
    parts = [f"CLAIM:\n{row.claim}", "CONTEXT:"]
    parts += [f"- {t}" for t in row.context] if row.context else ["(none)"]
    parts += [f"NEEDED:\n{row.needed}", "PASSAGES:"]
    for i, c in enumerate(chunks, 1):
        text = " ".join(c.content.split())[:MAX_PASSAGE_CHARS]
        parts.append(f"[{i}] chunk_id={c.chunk_id} | {c.title or '(untitled)'} | {c.section or '(no section)'}\n{text}")
    return "\n\n".join(parts)


def _parse_grades(data: object, asked: set[int]) -> tuple[dict[int, int], list[int]]:
    """(grades for asked ids, unknown ids returned). Bad items are skipped (so their chunk counts as missing);
    a duplicate keeps the first grade."""
    if not isinstance(data, dict) or not isinstance(data.get("grades"), list):
        raise LLMUpstreamError("judge answer has no grades list")
    out: dict[int, int] = {}
    unknown: list[int] = []
    for item in data["grades"]:
        if not isinstance(item, dict):
            continue
        cid, g = item.get("chunk_id"), item.get("grade")
        if not isinstance(cid, int) or isinstance(cid, bool) or g not in (0, 1, 2) or isinstance(g, bool):
            continue
        if cid not in asked:
            if cid not in unknown:
                unknown.append(cid)
            continue
        out.setdefault(cid, g)
    return out, unknown


# --- cache ---------------------------------------------------------------------------


class Cache:
    NAMES = ("meta", "queries", "searches", "chunks", "judgements")

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.meta: dict = {}
        self.queries: dict = {}
        self.searches: dict = {}
        self.chunks: dict = {}
        self.judgements: dict = {}

    def load(self) -> None:
        for name in self.NAMES:
            p = self.dir / f"{name}.json"
            setattr(self, name, json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name in self.NAMES:
            p = self.dir / f"{name}.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(getattr(self, name), ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)


def _hash(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]


def _lite(cid: int, doc_id: str) -> Evidence:
    """merge_results only needs chunk_id and doc_id."""
    return Evidence(cid, doc_id, "", "", "", "", None, "", None, None, None, 0.0, 0.0)


# --- evaluation -------------------------------------------------------------------------


async def evaluate(
    rows: Sequence[GoldenRow],
    *,
    planner: Planner,
    searcher: Searcher,
    judge: Judge,
    cache: Cache,
    variants: Sequence[str],
    refresh: set[str] = frozenset(),
    concurrency: int = 4,
) -> dict:
    """Run, pool and judge every claim. Returns {"claims": [...], "stats": {...}, "variants": [...]}.
    A claim that fails is recorded with `error` (the exception type) and left out of the metrics."""
    unknown_variants = [v for v in variants if v not in VARIANTS]
    if unknown_variants:
        raise ValueError(f"unknown variants: {', '.join(unknown_variants)}")
    stats: Counter = Counter()
    fp = await searcher.fingerprint()
    if cache.meta.get("corpus") != fp:
        if cache.meta.get("corpus") is not None:
            stats["corpus_changed"] = 1
        cache.searches.clear()
        cache.chunks.clear()
        cache.judgements.clear()
        cache.meta["corpus"] = fp
    sem = asyncio.Semaphore(concurrency)

    async def one(row: GoldenRow) -> dict:
        async with sem:
            try:
                return await _claim(row, planner, searcher, judge, cache, variants, refresh, stats)
            except Exception as e:  # noqa: BLE001 - recorded per claim, never the message (it could hold text)
                stats["errors"] += 1
                return {**_base(row), "error": type(e).__name__}

    claims = await asyncio.gather(*(one(r) for r in rows))
    return {"claims": list(claims), "stats": dict(stats), "variants": list(variants)}


def _base(row: GoldenRow) -> dict:
    return {
        "id": row.id,
        "cancer": row.cancer,
        "type": row.type,
        "style": row.style,
        "claim": row.claim,
        "needed": row.needed,
        "gold_chunk_ids": list(row.gold_chunk_ids),
        "reviewed": row.reviewed,
    }


async def _queries(row, planner, cache, refresh, stats) -> tuple[list[str], bool]:
    h = _hash(row.claim, row.context)
    cached = cache.queries.get(row.id)
    if cached and cached.get("hash") == h and "queries" not in refresh:
        stats["query_cache_hits"] += 1
        generated = cached["queries"]
    else:
        try:
            generated = await planner.generate(row.claim, row.context)
        except LLMError:
            stats["query_fallbacks"] += 1
            return [row.claim], True
        stats["query_calls"] += 1
        cache.queries[row.id] = {"hash": h, "queries": list(generated)}
    claim_key = " ".join(row.claim.split()).casefold()
    return [row.claim, *(q for q in generated if q.casefold() != claim_key)], False


async def _search(query, vector_only, searcher, cache, refresh, stats) -> list[Evidence]:
    key = f"{'vector' if vector_only else 'hybrid'}|k{POOL_K}|{query}"
    if key in cache.searches and "search" not in refresh:
        stats["search_cache_hits"] += 1
        return [_lite(cid, doc) for cid, doc in cache.searches[key]]
    results = await searcher.search(query, vector_only=vector_only, k=POOL_K)
    stats["searches"] += 1
    cache.searches[key] = [[e.chunk_id, e.doc_id] for e in results]
    for e in results:
        cache.chunks[str(e.chunk_id)] = {"doc_id": e.doc_id, "title": e.title, "section": e.section, "content": e.content}
    return results


async def _claim(row, planner, searcher, judge, cache, variants, refresh, stats) -> dict:
    record = _base(row)
    queries, fallback = [row.claim], False
    if any(VARIANTS[v][0] == "queries" for v in variants):
        queries, fallback = await _queries(row, planner, cache, refresh, stats)
    record["queries"] = queries
    record["query_fallback"] = fallback

    needed: list[tuple[bool, str]] = []
    for v in variants:
        source, vector_only = VARIANTS[v]
        for q in [row.claim] if source == "claim" else queries:
            if (vector_only, q) not in needed:
                needed.append((vector_only, q))
    found = await asyncio.gather(*(_search(q, vo, searcher, cache, refresh, stats) for vo, q in needed))
    lists = dict(zip(needed, found))

    record["variants"] = {}
    pool: list[int] = []
    for v in variants:
        source, vector_only = VARIANTS[v]
        per = [lists[(vector_only, q)] for q in ([row.claim] if source == "claim" else queries)]
        ranked = [e.chunk_id for e in merge_results([p[:SCORE_K] for p in per], SCORE_K)]
        pooled = [e.chunk_id for e in merge_results(per, POOL_K)]
        record["variants"][v] = {"ranked": ranked, "pool": pooled}
        pool += [c for c in pooled if c not in pool]
    pool += [c for c in row.gold_chunk_ids if c not in pool]

    absent = [c for c in pool if str(c) not in cache.chunks]
    if absent:
        for info in await searcher.fetch_chunks(absent):
            cache.chunks[str(info.chunk_id)] = {
                "doc_id": info.doc_id,
                "title": info.title,
                "section": info.section,
                "content": info.content,
            }
    not_in_corpus = [c for c in pool if str(c) not in cache.chunks]

    h = _hash(row.claim, row.context, row.needed)
    entry = cache.judgements.get(row.id)
    if not entry or entry.get("hash") != h or "judge" in refresh:
        entry = {"hash": h, "grades": {}}
        cache.judgements[row.id] = entry
    to_judge = sorted(c for c in pool if str(c) not in entry["grades"] and str(c) in cache.chunks)
    missing: list[int] = []
    unknown: list[int] = []
    if to_judge:
        chunks = [ChunkInfo(c, **cache.chunks[str(c)]) for c in to_judge]
        data = await judge.grade(row, chunks)
        stats["judge_calls"] += 1
        parsed, unknown = _parse_grades(data, set(to_judge))
        for c in to_judge:
            if c in parsed:
                entry["grades"][str(c)] = parsed[c]
            else:
                missing.append(c)  # not cached, so a rerun asks again
    stats["judged_chunks"] += len(to_judge)
    stats["judgement_cache_hits"] += len(pool) - len(to_judge)
    stats["missing_grades"] += len(missing)
    stats["unknown_ids"] += len(unknown)

    luna = {str(c): entry["grades"].get(str(c)) for c in pool}
    grades = {k: (g if g is not None else 0) for k, g in luna.items()}
    disagreements = []
    for g in row.gold_chunk_ids:
        if luna.get(str(g)) != 2 and g not in not_in_corpus:
            disagreements.append({"chunk_id": g, "luna_grade": luna.get(str(g))})
        grades[str(g)] = 2
    record.update(
        pool=pool,
        luna_grades=luna,
        grades=grades,
        missing_grades=missing,
        unknown_ids=unknown,
        gold_disagreements=disagreements,
        gold_not_in_corpus=[c for c in row.gold_chunk_ids if c in not_in_corpus],
        chunks={
            str(c): {"title": cache.chunks[str(c)]["title"], "section": cache.chunks[str(c)]["section"]}
            for c in pool
            if str(c) in cache.chunks
        },
    )
    return record


# --- summary ---------------------------------------------------------------------------


def _items(claims: Sequence[dict], variant: str) -> list[dict]:
    return [
        {
            "id": c["id"],
            "type": c["type"],
            "cancer": c["cancer"],
            "style": c["style"],
            "ranked": c["variants"][variant]["ranked"],
            "pool": c["variants"][variant]["pool"],
            "grades": {int(k): g for k, g in c["grades"].items()},
        }
        for c in claims
        if not c.get("error")
    ]


def summarize(result: dict) -> dict:
    claims = result["claims"]
    out: dict = {"variants": {}}
    for v in result["variants"]:
        items = _items(claims, v)

        def by(field: str, values=None) -> dict:
            keys = values or sorted({i[field] for i in items})
            return {key: aggregate([i for i in items if i[field] == key], ks=(SCORE_K,)) for key in keys}

        nc_hits = [i["id"] for i in items if i["type"] == "not_covered" and score_claim(i["ranked"], i["grades"], (SCORE_K,))[f"hit@{SCORE_K}"]]
        out["variants"][v] = {
            "all": aggregate(items, ks=KS),
            "by_cancer": by("cancer"),
            "by_style": by("style", ["technical", "lay"]),
            "by_type": by("type", list(COVERED)),
            "noise": {**noise(items, k=SCORE_K), "ids_with_grade_2": nc_hits},
        }
    main = MAIN_VARIANT if MAIN_VARIANT in result["variants"] else (result["variants"] or [None])[0]
    out["main_variant"] = main
    out["worst_misses"] = _worst(claims, main) if main else []
    return out


def _worst(claims: Sequence[dict], variant: str, n: int = 10) -> list[dict]:
    by_id = {c["id"]: c for c in claims}
    rows = []
    for i in _items(claims, variant):
        if i["type"] not in COVERED:
            continue
        rank = best_rank(i["pool"], i["grades"])
        ndcg = score_claim(i["ranked"], i["grades"], (SCORE_K,))[f"ndcg@{SCORE_K}"] or 0.0
        c = by_id[i["id"]]
        titles = [(c["chunks"].get(str(cid)) or {}).get("title") or "(untitled)" for cid in i["ranked"][:3]]
        rows.append({"id": i["id"], "claim": c["claim"], "needed": c["needed"], "best_rank": rank, "ndcg": ndcg, "top3": titles})
    rows.sort(key=lambda r: (r["best_rank"] is not None, -(r["best_rank"] or 0), r["ndcg"]))
    return rows[:n]


# --- cost ------------------------------------------------------------------------------


def cost_usd(usage: dict) -> dict:
    """usage: {"query_llm": (in, out), "judge_llm": (in, out), "embedding_tokens": n} for this run."""
    q_in, q_out = usage.get("query_llm", (0, 0))
    j_in, j_out = usage.get("judge_llm", (0, 0))
    out = {
        "query_llm": (q_in * PRICE_LLM_IN + q_out * PRICE_LLM_OUT) / 1e6,
        "judge_llm": (j_in * PRICE_LLM_IN + j_out * PRICE_LLM_OUT) / 1e6,
        "embeddings": usage.get("embedding_tokens", 0) * PRICE_EMBED / 1e6,
    }
    out["total"] = sum(out.values())
    return out


# --- report ----------------------------------------------------------------------------


def _f(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def _cell(text: object) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


METRIC_TITLES = [
    ("hit", "Hit rate (a grade-2 passage in the top k)"),
    ("recall", "Recall (share of the known grade-2 passages in the top k)"),
    ("p_strict", "Strict precision (share of the top k graded 2)"),
    ("p_lenient", "Lenient precision (share of the top k graded 1 or 2)"),
    ("ndcg", "NDCG (graded ranking quality, 1 = ideal order)"),
]


def render_markdown(result: dict, summary: dict) -> str:
    meta = result.get("meta", {})
    stats = result.get("stats", {})
    claims = result["claims"]
    variants = result["variants"]
    V = summary["variants"]
    ok = [c for c in claims if not c.get("error")]
    n_type = Counter(c["type"] for c in ok)
    L: list[str] = []
    w = L.append

    w(f"# Search eval {meta.get('timestamp', '')}".rstrip())
    w("")
    w(f"- Golden set: `{meta.get('golden', '?')}`, {len(claims)} claims "
      f"({n_type['supported']} supported, {n_type['contradicted']} contradicted, {n_type['not_covered']} not covered)"
      + (f", {sum(1 for c in claims if c.get('reviewed'))} reviewed" if claims else ""))
    if stats.get("errors"):
        w(f"- **{stats['errors']} claims failed** and are left out: "
          + ", ".join(f"{c['id']} ({c['error']})" for c in claims if c.get("error")))
    w(f"- Judge and query model: {meta.get('model', 'gpt-6-luna')}; scored at k = {', '.join(map(str, KS))}; "
      f"pool = top {POOL_K} of every variant + gold chunks")
    w(f"- Means over the {V[variants[0]]['all']['n'] if variants else 0} covered claims (supported + contradicted). "
      "Not-covered claims are scored only under Noise.")
    w("- **Synthetic, AI-written claims; grades by an LLM judge.** Read this as a comparison between variants, "
      "not as an absolute accuracy figure.")
    w("")

    for key, title in METRIC_TITLES:
        w(f"## {title}")
        w("")
        w("| Variant | @1 | @3 | @5 | @8 |")
        w("|---|---:|---:|---:|---:|")
        for v in variants:
            a = V[v]["all"]
            w(f"| {v} | " + " | ".join(_f(a[f"{key}@{k}"]) for k in KS) + " |")
        w("")

    w("## MRR (1 / rank of the first grade-2 passage in the top 8)")
    w("")
    w(f"| Variant | MRR | Claims with fewer than {SCORE_K} results |")
    w("|---|---:|---:|")
    for v in variants:
        a = V[v]["all"]
        w(f"| {v} | {_f(a['mrr'])} | {a[f'short@{SCORE_K}']} |")
    w("")

    for field, title, label in (
        ("by_cancer", "By cancer", "Cancer"),
        ("by_style", "By style", "Style"),
        ("by_type", "By type", "Type"),
    ):
        w(f"## {title} (k = {SCORE_K})")
        w("")
        w(f"| {label} | Variant | n | Hit | Recall | Strict P | NDCG | MRR |")
        w("|---|---|---:|---:|---:|---:|---:|---:|")
        groups = list(V[variants[0]][field]) if variants else []
        for g in groups:
            for v in variants:
                a = V[v][field][g]
                k = SCORE_K
                w(f"| {g} | {v} | {a['n']} | {_f(a[f'hit@{k}'])} | {_f(a[f'recall@{k}'])} | "
                  f"{_f(a[f'p_strict@{k}'])} | {_f(a[f'ndcg@{k}'])} | {_f(a['mrr'])} |")
        w("")

    w(f"## Noise (not-covered claims, k = {SCORE_K})")
    w("")
    w("Nothing in the corpus settles these claims, so both columns should be about 0.")
    w("")
    w("| Variant | n | Lenient P@8 | Share with a grade-2 passage | Claims with a grade-2 passage |")
    w("|---|---:|---:|---:|---|")
    for v in variants:
        nz = V[v]["noise"]
        w(f"| {v} | {nz['n']} | {_f(nz['p_lenient'])} | {_f(nz['any_relevant_share'])} | "
          f"{', '.join(nz['ids_with_grade_2']) or '-'} |")
    w("")

    main = summary.get("main_variant")
    w(f"## Worst misses ({main})")
    w("")
    w(f"Best rank = rank of the first grade-2 passage in the top {POOL_K}; the top 3 titles are from the scored top 8.")
    w("")
    w("| Claim | Text | Needed | Best rank | Top 3 titles |")
    w("|---|---|---|---:|---|")
    for m in summary.get("worst_misses", []):
        rank = "not found" if m["best_rank"] is None else str(m["best_rank"])
        w(f"| {m['id']} | {_cell(m['claim'])} | {_cell(m['needed'])} | {rank} | "
          f"{'; '.join(_cell(t) for t in m['top3']) or '-'} |")
    w("")

    w("## Gold disagreements")
    w("")
    dis = [(c["id"], d) for c in ok for d in c.get("gold_disagreements", [])]
    gone = [(c["id"], g) for c in ok for g in c.get("gold_not_in_corpus", [])]
    if dis:
        w("Gold chunks the judge did not grade 2 (they were forced to 2). Several for one claim may mean a bad golden row.")
        w("")
        w("| Claim | Chunk | Luna grade |")
        w("|---|---:|---:|")
        for cid, d in dis:
            w(f"| {cid} | {d['chunk_id']} | {'missing' if d['luna_grade'] is None else d['luna_grade']} |")
    else:
        w("None: the judge graded every gold chunk 2.")
    if gone:
        w("")
        w("Gold chunk ids not found in the corpus (stale ids after a re-ingest?): "
          + ", ".join(f"{cid}: {g}" for cid, g in gone))
    w("")

    pools = [len(c.get("pool", [])) for c in ok]
    w("## Judging")
    w("")
    w(f"- Pool size per claim: mean {sum(pools) / len(pools):.1f}, max {max(pools)}" if pools else "- No pools")
    w(f"- Judge calls: {stats.get('judge_calls', 0)}; chunks judged now: {stats.get('judged_chunks', 0)}; "
      f"reused from cache: {stats.get('judgement_cache_hits', 0)}")
    w(f"- Missing grades (counted as 0, asked again next run): {stats.get('missing_grades', 0)}; "
      f"unknown chunk ids in answers (ignored): {stats.get('unknown_ids', 0)}")
    w(f"- Query generation: {stats.get('query_calls', 0)} calls, {stats.get('query_cache_hits', 0)} cached, "
      f"{stats.get('query_fallbacks', 0)} fell back to the claim alone")
    w(f"- Searches: {stats.get('searches', 0)} run, {stats.get('search_cache_hits', 0)} cached"
      + ("; **corpus changed since the last run, caches dropped**" if stats.get("corpus_changed") else ""))
    w("")

    usage = meta.get("usage") or {}
    cost = meta.get("cost") or cost_usd(usage)
    w("## Cost and time (this run; cached calls cost nothing)")
    w("")
    w("| Part | Input tokens | Output tokens | USD |")
    w("|---|---:|---:|---:|")
    for key, label in (("query_llm", "Luna queries"), ("judge_llm", "Luna judge")):
        i, o = usage.get(key, (0, 0))
        w(f"| {label} | {i:,} | {o:,} | {cost[key]:.4f} |")
    w(f"| Embeddings | {usage.get('embedding_tokens', 0):,} | - | {cost['embeddings']:.4f} |")
    w(f"| **Total** | | | **{cost['total']:.4f}** |")
    w("")
    w(f"Prices: {meta.get('model', 'gpt-6-luna')} ${PRICE_LLM_IN:.2f} in / ${PRICE_LLM_OUT:.2f} out per 1M tokens "
      f"(cached input billed at the full rate here), embeddings ${PRICE_EMBED:.2f} per 1M.")
    wall = meta.get("wall_time_s")
    w("")
    w(f"Wall time: {'n/a' if wall is None else f'{wall:.1f} s'}")
    w("")
    return "\n".join(L)


# --- run ---------------------------------------------------------------------------------


async def run_eval(
    *,
    golden: Path,
    limit: int | None,
    variants: Sequence[str],
    refresh: set[str],
    out_dir: Path,
    cache_dir: Path,
    planner: Planner,
    searcher: Searcher,
    judge: Judge,
    usage: Callable[[], dict],
    concurrency: int = 4,
    model: str = "gpt-6-luna",
) -> tuple[Path, Path]:
    rows = load_golden(golden)
    if limit is not None:
        rows = rows[:limit]
    cache = Cache(cache_dir)
    cache.load()
    started = datetime.now(UTC)
    t0 = time.monotonic()
    try:
        result = await evaluate(
            rows,
            planner=planner,
            searcher=searcher,
            judge=judge,
            cache=cache,
            variants=variants,
            refresh=refresh,
            concurrency=concurrency,
        )
    finally:
        cache.save()
    u = usage()
    stamp = started.strftime("%Y-%m-%dT%H%M%SZ")
    result["meta"] = {
        "timestamp": stamp,
        "golden": str(golden),
        "model": model,
        "wall_time_s": time.monotonic() - t0,
        "usage": u,
        "cost": cost_usd(u),
    }
    summary = summarize(result)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stamp}.json"
    md_path = out_dir / f"{stamp}.md"
    json_path.write_text(json.dumps({**result, "summary": summary}, indent=1, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(result, summary), encoding="utf-8")
    return json_path, md_path


# --- live adapters -------------------------------------------------------------------------


class LunaPlanner:
    def __init__(self, client):
        self._client = client

    async def generate(self, claim, context):
        from app.check.queries import generate_queries

        queries, _ = await generate_queries(self._client, claim, context)
        return queries


class LunaJudge:
    def __init__(self, client):
        self._client = client

    async def grade(self, row, chunks):
        data, _ = await self._client.structured(
            instructions=JUDGE_INSTRUCTIONS,
            input=build_judge_input(row, chunks),
            schema=JUDGE_SCHEMA,
            name=JUDGE_SCHEMA_NAME,
        )
        return data


class LiveSearch:
    """match_chunks through the corpus clients (sync, run in worker threads). An empty query_text makes the keyword
    list empty, so the RPC ranks by vector alone. Embeddings are memoised per query text for the run."""

    def __init__(self, embedder, store):
        self._embedder = embedder
        self._store = store
        self._vectors: dict[str, list[float]] = {}

    def _vector(self, text: str) -> list[float]:
        if text not in self._vectors:
            [self._vectors[text]] = self._embedder.embed([text])
        return self._vectors[text]

    def _search_sync(self, query: str, vector_only: bool, k: int) -> list[Evidence]:
        from app.check.search import parse_rows

        rows = self._store.match_chunks(self._vector(query), "" if vector_only else query, k=k)
        return parse_rows(rows)

    async def search(self, query, *, vector_only, k):
        import anyio.to_thread

        return await anyio.to_thread.run_sync(self._search_sync, query, vector_only, k)

    def _fetch_sync(self, ids):
        rows = self._store._json(
            "GET",
            "/chunks",
            params={"select": "id,doc_id,section,content,documents(title)", "id": f"in.({','.join(map(str, ids))})"},
        )
        return [
            ChunkInfo(r["id"], r["doc_id"], (r.get("documents") or {}).get("title"), r["section"], r["content"])
            for r in rows
        ]

    async def fetch_chunks(self, ids):
        import anyio.to_thread

        return await anyio.to_thread.run_sync(self._fetch_sync, list(ids))

    async def fingerprint(self):
        import anyio.to_thread

        meta = await anyio.to_thread.run_sync(self._store.read_meta)
        return _hash(meta)


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m evals.search.run", description="Search eval (issue #53).")
    p.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    p.add_argument("--limit", type=int, default=None, help="only the first N claims")
    p.add_argument("--variants", default=",".join(VARIANTS), help="comma-separated, default all four")
    p.add_argument(
        "--refresh",
        nargs="*",
        choices=REFRESH_CHOICES,
        default=None,
        help="ignore the cache for these parts (no value: all of them)",
    )
    p.add_argument("--env", type=Path, default=None, help="the .env file (default: repo-root .env)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--out", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = p.parse_args(argv)
    args.variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    bad = [v for v in args.variants if v not in VARIANTS]
    if bad or not args.variants:
        p.error(f"--variants must be from: {', '.join(VARIANTS)}")
    if args.refresh is None:
        args.refresh = set()
    elif not args.refresh:
        args.refresh = set(REFRESH_CHOICES)
    else:
        args.refresh = set(args.refresh)
    return args


async def _amain(args) -> int:
    from app.check.llm import OpenAIResponsesClient
    from app.config import Settings
    from app.corpus.embed import OpenAIEmbedder
    from app.corpus.supabase import SupabaseCorpus

    if args.env is not None and not args.env.is_file():
        print(f"--env: no such file: {args.env}", file=sys.stderr)
        return 2
    settings = Settings(_env_file=args.env) if args.env else Settings()
    missing = [
        name
        for name, value in (
            ("OPEN_AI_KEY", settings.openai_key),
            ("OPEN_AI_VECTOR_KEY", settings.openai_vector_key),
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_SECRET_KEY", settings.supabase_secret_key),
        )
        if not value
    ]
    if missing:
        print(f"Set {', '.join(missing)} in the .env", file=sys.stderr)
        return 2

    key = settings.openai_key.get_secret_value()
    query_client = OpenAIResponsesClient(key, model=settings.checker_model, timeout_s=settings.checker_timeout_s)
    judge_client = OpenAIResponsesClient(key, model=settings.checker_model, timeout_s=max(settings.checker_timeout_s, 180))
    embedder = OpenAIEmbedder.from_settings(settings, timeout_s=30, max_retries=3)
    store = SupabaseCorpus(settings.supabase_url, settings.supabase_secret_key.get_secret_value(), timeout_s=30, max_retries=3)

    def usage():
        q, j = query_client.total_usage, judge_client.total_usage
        return {
            "query_llm": (q.input_tokens, q.output_tokens),
            "judge_llm": (j.input_tokens, j.output_tokens),
            "embedding_tokens": embedder.total_tokens,
        }

    try:
        json_path, md_path = await run_eval(
            golden=args.golden,
            limit=args.limit,
            variants=args.variants,
            refresh=args.refresh,
            out_dir=args.out,
            cache_dir=args.cache,
            planner=LunaPlanner(query_client),
            searcher=LiveSearch(embedder, store),
            judge=LunaJudge(judge_client),
            usage=usage,
            concurrency=args.concurrency,
            model=settings.checker_model,
        )
    finally:
        await query_client.aclose()
        await judge_client.aclose()
        embedder.close()
        store.close()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    summary = data["summary"]
    for v in args.variants:
        a = summary["variants"][v]["all"]
        print(f"{v:<20} hit@8 {_f(a['hit@8'])}  recall@8 {_f(a['recall@8'])}  ndcg@8 {_f(a['ndcg@8'])}  mrr {_f(a['mrr'])}")
    print(f"cost ${data['meta']['cost']['total']:.4f}, wall {data['meta']['wall_time_s']:.1f} s")
    print(f"report: {md_path}")
    print(f"data:   {json_path}")
    return 0


def main(argv=None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
