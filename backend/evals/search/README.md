# Search eval (issue #53)

This eval measures how well the evidence search finds passages that settle a claim, before any verdict is made. It
compares five ways of searching on a golden set of claims (`golden.jsonl`, described in `GOLDEN.md`).

**Synthetic and AI-written.** The claims are written by an AI and the passages are graded by an LLM. Use the
numbers to compare variants with each other. They are not an absolute accuracy figure.

## Running

From `backend/`:

```
python -m evals.search.run                                   # golden.jsonl, all five variants
python -m evals.search.run --golden evals/search/smoke.jsonl # the 3-claim smoke set
python -m evals.search.run --limit 10 --variants v1_hybrid_claim,v2_hybrid_queries
python -m evals.search.run --refresh                         # ignore every cache
python -m evals.search.run --refresh judge                   # re-grade only (also: queries, search)
python -m evals.search.run --env /path/to/.env               # default: the repo-root .env
```

You need `OPEN_AI_KEY` (Luna), `OPEN_AI_VECTOR_KEY` (embeddings), `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. Keys are
never printed. In an agent worktree, pass the main checkout's `.env` with `--env`.

Other flags: `--concurrency N` (default 4, the number of claims in flight), `--out DIR`, `--cache DIR`.

The runner writes two files to `results/`:

- `<UTC timestamp>.md`: the report. Commit these.
- `<UTC timestamp>.json`: every claim's queries, ranked ids per variant, pool and grades. Gitignored.

The offline tests are `tests/test_search_eval_metrics.py` and `tests/test_search_eval_run.py`, which use fakes. No
live test runs in the default suite.

`smoke.jsonl` holds 3 hand-written rows (Breslow, MMR + pembrolizumab, HER2 + endocrine therapy). Their
`gold_chunk_ids` are database ids, so they go stale after `ingest_corpus.py --rebuild`. When that happens, the
report lists them under "Gold chunk ids not found in the corpus".

## Variants

Every variant is scored on its top 8, which is what `/check` uses (`CHECK_TOP_K`).

| Variant | What is searched | Search |
|---|---|---|
| `v1_hybrid_claim` | the raw claim | hybrid `match_chunks` (the old `/check` flow) |
| `v2_hybrid_queries` | the claim plus Luna's 1-3 queries (`generate_queries`) | hybrid, fused with `merge_results` (the current `/check` flow) |
| `vec_claim` | the raw claim | vector only (`query_text = ""` leaves the keyword list empty) |
| `vec_queries` | the same queries | vector only, fused with `merge_results` |
| `v3_terms_cancer` | the claim plus Luna's queries from the v3 plan (`generate_plan`) | hybrid with Luna's key terms as the keyword text and the keyword list filtered to Luna's cancer (issue #55, the new `/check` flow), fused with `merge_results` |

`v3_terms_cancer` needs `supabase/migrations/20261128000000_match_chunks_filter.sql`. Its plan (queries, key terms,
cancer) is cached under `v3:<claim id>` in `cache/queries.json`, so the cached v2 queries stay valid; the first v3
run makes one extra Luna call per claim. If the plan fails, it searches the claim alone with no filter (the same
call as `v1_hybrid_claim`).

Each search fetches 10 results to build the pool. A multi-query variant's scored top 8 merges the top 8 of each
query, as `/check` does. The SQL ranks before it limits, so that top 8 equals the first 8 of the top 10. The SQL
defaults apply to all variants: `per_doc` 2 and `min_similarity` 0.35.

## Pooling and grading

1. **Pool:** for each claim, the union of every variant's top 10, plus the row's `gold_chunk_ids`.
2. **Grade:** gpt-6-luna (the `/check` model, at low reasoning effort) grades the whole pool in one call per claim,
   using a strict JSON schema. The call includes the claim, its context, the `needed` note, and each passage
   numbered with its chunk id, title and section. The model does not know which passages are gold.
   - 2: the passage contains information that confirms or refutes this specific claim.
   - 1: same topic, but it does not settle the claim.
   - 0: unrelated, or about a different cancer or subtype.
3. **Gold chunks are forced to 2.** If Luna gave a gold chunk anything other than 2, the report lists it under
   "Gold disagreements". Several disagreements on one claim usually mean the golden row is wrong, not the judge.
4. **Missing grades** (a passage that Luna skipped) count as 0. They are counted in the report and not cached, so
   the next run asks again. Chunk ids in the answer that weren't asked for are ignored and counted.

### Caveats

- **Judge bias.** The judge is the same model that writes the v2 queries, so it may favour passages phrased the way
  it would phrase them. It also grades on its own reading of "settles the claim". Spot-check the grades of the
  worst misses in the `.json` before trusting a small difference.
- **Recall is limited by the pool.** Recall counts only grade-2 passages that some variant found, plus the gold
  chunks. A relevant passage that no variant found does not count, so recall is an upper bound and is only fair
  for comparing the variants that were pooled together. Adding a variant can lower the others' recall.
- **Small numbers.** With about 15 covered claims per cancer, one claim moves a per-cancer mean by about 0.07.
- **Non-determinism.** Luna's queries and grades are not deterministic, and neither are OpenAI embeddings. The
  caches keep reruns stable. Use `--refresh` to measure that variance.

## Metrics

Grades: 2 = settles the claim, 1 = on topic, 0 = unrelated. Unjudged passages count as 0. The main tables average
over **covered** claims (`supported` and `contradicted`). `not_covered` claims are scored only under Noise.

| Metric | In plain words |
|---|---|
| **Hit rate@k** | Share of claims with at least one grade-2 passage in the top k. Does the checker see any usable evidence? |
| **Recall@k** | Of the grade-2 passages known for the claim, the share that appear in the top k. Left out when none is known. |
| **Strict precision@k** | Share of the returned top k that is grade 2. |
| **Lenient precision@k** | Share of the returned top k that is grade 1 or 2, so not off-topic. |
| **MRR** | 1 / rank of the first grade-2 passage in the top 8, or 0 if there is none. 1.0 means the best evidence is always first. |
| **NDCG@k** | Rewards ranking 2s above 1s above 0s, with a smaller reward further down the list (log2 discount). The ideal ranking sorts every judged grade for the claim, from the whole pool. 1.0 means ideal order. Left out when nothing is graded above 0. |
| **Noise** (`not_covered` only) | The corpus has nothing that settles these claims. Mean lenient precision@8 and the share of claims with any grade-2 passage in the top 8. Both should be about 0. A grade 2 here means the row isn't really "not covered", or the judge is too generous. |

How precision is computed: the denominator is min(k, number returned). A search that returns 3 good passages and
then stops is not punished for the 5 it did not return. The report gives, per variant, how many claims got fewer
than 8 results. An empty result has undefined precision and is left out of the mean.

The report also includes:

- k=8 breakdowns by cancer, by style (technical vs lay) and by type (supported vs contradicted).
- The 10 worst misses for `v2_hybrid_queries`: claims with no grade-2 passage in the top 10 first, then claims whose
  first grade-2 passage is ranked worst.
- Judge statistics.
- **Search latency** per variant: calls, mean, p50 and p95 wall-clock seconds per `match_chunks` call (embedding
  included), and timeouts (a failure mentioning Postgres 57014 or a client timeout). Only searches run in this run
  are timed, so cached ones don't count: compare variants with `--refresh search`. A call shared by two variants
  (the claim search of v1 and v2) counts for both.
- Token usage, cost and wall time.

## Cost

Prices: gpt-6-luna $0.10 per 1M input tokens and $0.50 per 1M output tokens; `text-embedding-3-small` $0.02 per 1M.

Smoke run (3 claims, 2026-11-27): pools of about 21 passages, about 9k judge input tokens per claim, $0.0036 in total,
20 s. A full 100-claim run should cost about **$0.12** and take a few minutes at concurrency 4. Almost all of the
cost is the judge. A rerun on the caches costs nothing. Only new pool passages are graded (for example after adding
a variant), in one call per claim.

## Caches

`cache/` is gitignored and holds:

- `queries.json`: Luna's queries per claim id, and the v3 plan (queries, key terms, cancer) per `v3:<claim id>`.
- `searches.json`: ranked ids per (mode, query), and per (key terms, cancer, query) for v3.
- `chunks.json`: passage text per chunk id.
- `judgements.json`: grades per (claim id, chunk id).
- `meta.json`: the corpus fingerprint, a hash of `corpus_meta`.

Editing a claim, its context or its `needed` note drops that claim's cached queries and grades. A change in
`corpus_meta` (re-chunk or rebuild) drops the search, chunk and grade caches. The queries stay, because they don't
depend on the corpus.
