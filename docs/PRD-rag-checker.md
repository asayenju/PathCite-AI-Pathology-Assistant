# PRD: RAG claim checker (Luna-written searches + GPT-6 Luna stances + one Jev confidence)

Issues: GitHub #47–#50 (v1), #51 (backend v2), #52 (frontend v2) and #55 (search speed), label `rag-checker`. Builds on `docs/PRD-jev-triage.md` (claims) and `docs/PRD-corpus-ingest.md` (the evidence corpus).

> **v2 (issue #51) replaced the v1 design below in three places:** Luna writes the searches, Luna alone judges the passages, and Jev gives one confidence over all passages instead of a per-passage stance. See [v2 (issue #51)](#v2-issue-51); sections 3–5 describe v2, and the v1 text is kept only where it still applies.

## What

Replace the placeholder `ClaimChecker` (`frontend/src/lib/claimChecker.ts`, answers `not_checked` for every claim) with a real fact check:

1. **Search:** hybrid search over the Supabase corpus (2,151 documents, 9,543 chunks of NICE, PubMed-sourced guidelines, PubMed abstracts and WikiDoc). It uses `match_chunks`, which is done and committed.
2. **Judge each source separately.** GPT-6 Luna (OpenAI) says, for every retrieved passage, whether it **supports**, **contradicts** or is **irrelevant** to the doctor's claim. (v1: Jev also judged every passage; v2: Jev gives one independent confidence over all passages.)
3. **Verdict by code rules, not a model and not a majority vote.**
4. **Show it on the claim card:** the verdict, the sources grouped by stance (with trust level, year, the exact quote and a link), and a **"Needs your review"** banner when the sources or the two models disagree.

## Why

- **A doctor needs to see evidence, not a model's opinion.** Every supporting or contradicting statement must be a word-for-word quote from a retrieved passage, with its URL.
- **Conflicts must stay visible.** If 4 sources support a claim and 1 contradicts it, a single combined verdict hides the conflict. A majority vote is also wrong: one current NICE guideline outweighs four WikiDoc pages. So a mix of support and contradiction is always shown as **"Sources disagree"** and sent to review. Nothing is resolved silently.
- **Two independent models catch each other's misses.** v1: a passage only counted when Luna and Jev agreed on it. v2: Jev independently answers whether the passages support, contradict or don't settle the claim; if that disagrees with Luna's verdict, or Jev isn't confident, the card goes to review.
- **Cost:**
  - `gpt-6-luna` is $0.10 in / $0.50 out per 1M tokens: about $0.0005 a claim, about $0.015 a transcript.
  - Claim embeddings are about $0.02 per 1M tokens, effectively free.
  - Jev adds one request per claim.

## v2 (issue #51)

**Why it changed.** In v1 Luna and Jev each judged every passage, and a passage counted only when both agreed with Jev's p ≥ 0.6. On real claims almost every card said **"Needs your review"**: Jev's per-passage probabilities were often 0.5–0.65, and the two models split on minor passages that didn't matter to the verdict. The banner stopped meaning anything. Separately, the doctor's sentence was a poor search query ("Because it's HER2 negative, hormone-blocking pills won't work for you" shares few words with guideline text on endocrine therapy).

**What v2 does ("approach A", chosen by the user), in order:**
1. **Luna writes the searches** (`app/check/queries.py`). One small strict-JSON call (reasoning effort low): given the claim and up to 2 previous doctor turns, 1–3 short, neutral queries that would find guideline text confirming or refuting the general medical fact (for the HER2 example: "HER2 status and endocrine therapy response", "hormone receptor positive breast cancer endocrine therapy"). The raw claim is always searched too, first. All queries run concurrently through `EvidenceSearch`; the per-query results (each already with `per_doc` and `min_similarity` applied by the RPC) are merged by reciprocal rank fusion (sum of 1/(60 + rank)), deduplicated by `chunk_id`, capped at 2 chunks per `doc_id`, top `check_top_k` (8). If query generation fails, the claim alone is searched (only the exception type is logged): the check doesn't fail.
2. **Luna judges the passages** exactly as in v1 (`verify.luna_stances`, groundedness guard). The verdict comes from code rules on Luna's grounded stances (guard-unclear passages count as not relevant): none relevant → `unverified`; all support → `accurate` (`incomplete` if `missing_qualifier`); all contradict → `inaccurate`; any mix → `disputed`.
3. **Jev gives one confidence**, after Luna (`jev_check.jev_confidence`). One Choice question, `verdict`: do these passages show the general medical claim is **supported** / **contradicted** / **insufficient** (not settled)? The state is the claim, the context and all passages, numbered, with title, section, trust and year. **Jev is not shown Luna's stances or verdict**, so it is an independent check. Luna's verdict maps to a class: accurate/incomplete → supported, inaccurate → contradicted, unverified → insufficient, disputed → none.
4. **Review rules** (`aggregate.py`): `sources_disagree` if the verdict is `disputed`; `jev_unavailable` if Jev failed or has no key; otherwise `jev_disagrees` if Jev's top label isn't Luna's class, or `low_confidence` if Jev's probability for Luna's class is below `check_jev_min` (now **0.7**). Confident agreement needs no review. `models_disagree` is gone.

**Trade-off.** A passage no longer needs two models to count: Luna alone decides each stance, guarded by its word-for-word quotes, and Jev checks the overall answer. Disagreement is still never resolved silently (it's a review reason), but it is now measured where it matters, on the verdict.

**Live smoke run (2026-11-27, `pytest -m check -s`):** Breslow "how wide" → `inaccurate`, Jev contradicted 0.97, no review; colon MMR testing → `accurate`, Jev supported 0.99, no review; HER2/hormone pills → `inaccurate`, Jev contradicted 0.62 → `low_confidence`. 8–15 s per claim, about 4–5k Luna input tokens over 2 requests.

## v3 search speed (issue #55)

**Why.** The keyword half of `match_chunks` scored (`ts_rank_cd`) every chunk matching *any* word of the claim. Common words ("cancer", "spread") match thousands of the 9,543 chunks, so on Supabase's free tier one RPC call took 1–9 s and hit the 8 s statement timeout (code 57014) under load. Measured by hand: passing 2–5 hand-picked key terms instead of the sentence made calls 2–3x faster, with nearly identical top results.

**What.**
- **Luna's query call also returns** `key_terms` (2–5 short, distinctive terms for full-text search: named tests, scores, staging terms, drug names, abbreviations such as "Breslow", "IPI", "PSA nadir", "Dukes C"; never generic words like cancer, tumour, patient, treatment, spread, risk) and `cancer` (colorectal | breast | melanoma | lymphoma | prostate | thyroid | unknown). Key terms are cleaned: whitespace folded; blank, duplicate, over-40-character and lone generic words dropped; at most 5.
- **Every search** embeds its own query (as in v2), but uses the key terms joined with spaces as `query_text` (the same for every query), and passes `cancer` when known.
- **`supabase/migrations/20261128000000_match_chunks_filter.sql`** drops both earlier overloads and recreates `match_chunks` with an extra `cancer text default null`. The filter `(cancer is null or c.cancers @> array[cancer])` sits inside the keyword candidate query, next to the full-text match, so Postgres can bitmap-AND the fts and cancers GIN indexes and only ranks the filtered rows. The vector list is not filtered (a safety net for a wrong or missing label); the 0.35 floor, per-doc cap and RRF are unchanged.
- **Fallbacks:** query generation fails or no key terms → keyword text is the raw claim (the v2 behaviour); cancer `unknown` or generation failed → no filter.
- `/check`'s response is unchanged (`search_queries` is still the claim plus Luna's queries). The search eval has a `v3_terms_cancer` variant and a "Search latency" table (mean, p50, p95, timeouts per variant).

## How

### 1. Search (done: `supabase/migrations/20261127010000_match_chunks.sql`, cancer filter added by `20261128000000_match_chunks_filter.sql`, see [v3](#v3-search-speed-issue-55))
- **`match_chunks(query_embedding, query_text, k=8, candidates=40, per_doc=2, rrf_k=60, min_similarity=0.35)`.** It finds candidates with two searches:
  - **vector:** the top 40 by cosine distance (HNSW);
  - **keyword:** the top 40 full-text matches, with the claim's lexemes **OR-ed**, because AND-ing a whole sentence matches almost nothing.
- **Merging:**
  - Reciprocal rank fusion combines the two lists.
  - Everything under 0.35 cosine similarity is dropped. On sample claims, the keyword list's junk (T-cell lymphoma for a melanoma claim, a pain guideline for a HER2 claim) scored 0.23–0.34, while relevant keyword-only hits scored 0.37 or higher.
  - At most 2 chunks come from one document, and the top 8 are kept.
- **Access:** `security definer`, executable by service_role only. The publishable key gets 401.
- **Trying it:** `backend/scripts/try_search.py "<claim>" ...` prints each result with its vector, keyword and fused ranks.
- **Backend module `app/check/search.py`:**
  - Embeds the claim with `OPEN_AI_VECTOR_KEY` (`text-embedding-3-small`; reuse `app/corpus/embed.py`).
  - Calls the RPC through PostgREST, reusing the error and retry pattern of `app/corpus/supabase.py`.

### 2. Luna per-source stance (`app/check/llm.py`, `app/check/verify.py`)
- **Calls the OpenAI Responses API** with httpx, using the new `OPEN_AI_KEY` and model setting `checker_model = "gpt-6-luna"`.
  - Retry, timeout and error classes follow `app/triage/jev.py` and `app/corpus/embed.py`.
  - The key and claim text are never logged.
- **Structured JSON output:**
  - `stances: [{chunk_id, stance: supports|contradicts|irrelevant, quote}]`, one per chunk;
  - `missing_qualifier: bool`;
  - `issue`, `suggestion`.
- **Prompt:**
  - The fixed instructions go first, so they're cached.
  - Then the claim, up to 2 previous doctor turns, and the numbered passages.
  - Rules:
    - Judge only from the passages.
    - Quote word for word.
    - A patient-specific detail is not a contradiction.
    - **A passage about a different subtype or a different cancer is irrelevant, not a contradiction** (for example, anaplastic thyroid for a papillary claim).
- **Groundedness guard (code):** a supports or contradicts stance whose quote is not found in its chunk becomes **unclear**. Matching is after NFC normalisation, whitespace folding and case folding. A verdict can never rest on a made-up quote.

### 3. Jev confidence (`app/check/jev_check.py`, v2)
- **`jev_confidence(jev, claim, context, chunks) -> JevVerdict(probs, label, p)`** reuses `JevClient.ask` (transport, retries and errors).
  - One Choice question, `verdict`: supported / contradicted / insufficient over all passages, in one request.
  - `state` holds the claim, the context and the numbered passages (title, section, trust, year, text). Never Luna's answer.
- **Runs after Luna** (sequential).
- **If Jev fails,** Luna's verdict stands, with `needs_review = true`, reason `jev_unavailable` and `confidence: null`.
- (v1's `judge_sources`, one Choice per passage, was removed.)

### 4. Aggregation (`app/check/aggregate.py`, pure, fully unit-tested)
- **The verdict**, from Luna's grounded stances (unclear counts as not relevant):

  | Relevant chunks (supports or contradicts) | Verdict | Luna's class for Jev |
  |---|---|---|
  | none | `unverified` | insufficient |
  | all support | `accurate`, or `incomplete` if Luna reports `missing_qualifier` | supported |
  | all contradict | `inaccurate` | contradicted |
  | any mix | `disputed` ("Sources disagree"), always `needs_review` | none |

- **Review reasons**, ordered and unique: `sources_disagree` (disputed); `jev_unavailable`; else `jev_disagrees` (Jev's top label ≠ Luna's class) or `low_confidence` (Jev's p for Luna's class < `check_jev_min`, 0.7). Confident agreement → no review.

### 5. Endpoint `POST /check` (`app/check/router.py`)
- **Request:** `{turn_id, quote, context[]}`. This is the frontend's `ClaimToCheck`.
- **Response (v2):**
  ```
  {status:"checked", verdict, issue, suggestion, needs_review,
   review_reasons:[sources_disagree|jev_unavailable|jev_disagrees|low_confidence],
   sources:[{chunk_id, title, section, url, trust, year, quote, stance, luna_stance}],
   search_queries:[claim, ...Luna's queries],
   confidence:{label:supported|contradicted|insufficient, p, p_verdict|null, agrees|null} | null,
   model, jev_model}
  ```
  - `stance` equals `luna_stance`. Per-source `jev_stance` / `jev_p` were removed in v2.
  - `confidence`: `label`/`p` are Jev's top answer, `p_verdict` is Jev's probability for Luna's class, `agrees` whether they match; both `null` for `disputed`; the whole object `null` when Jev is unavailable.
- **Errors** use `{detail, code}`:
  - `check_unavailable` (503): a missing OpenAI or Supabase key.
  - `check_rate_limited` (429).
  - `check_upstream_error` (502).
  - `invalid_check_request` (422).
- **Settings:** `openai_key` (alias `OPEN_AI_KEY`), `checker_model`, `check_top_k=8`, `check_jev_min=0.7` (0.6 in v1).
- **The Supabase secret key is now read by the API server too.** It stays backend only and is never sent to the frontend or logged.
- **`.env.example`** adds `OPEN_AI_KEY`.

### 6. Frontend
- **Types:** `CheckResult` gains `{status:'checked', ...}`, and `ReviewVerdict` gains `unverified` and `disputed`.
- **`httpClaimChecker`** (`lib/claimChecker.ts`) posts to `/api/check`.
  - At most 4 checks run at once, and it honours `AbortSignal`.
  - It becomes `defaultClaimChecker`. The stub stays for tests.
- **The claim card shows:**
  - the verdict, the issue and the suggestion;
  - sources grouped **Supports / Contradicts / Unclear**, each with title, section, trust badge, year, quote and link;
  - a "Needs your review" banner with the reason.
- **Errors** map through `messageFor`.
- **Review label** becomes: "Triage by Jev (TypeSafe). Claims are checked against NICE/PubMed/WikiDoc with OpenAI and Jev."

## Privacy
- **Where claim text goes:** doctor claim sentences (and up to 2 previous doctor turns as context) go to OpenAI for embedding and the verdict, to TypeSafe (Jev), and to Supabase as the search text.
- **Nothing is logged or stored:** not the claim, not the context and not the verdict. Only exception type names are logged.
- **Patient turns are never sent.**

## Non-goals
- **Evals:** the test set, accuracy metrics, model comparison and threshold tuning. These are a separate plan.
- **A Terra second opinion.**
- **A cancer filter on search.**
- **Remembering doctor disagreements:** the next PRD. This one returns chunk ids and quotes so that feature can plug in.
- **Auth.**

## Issues
1. **#47 Search module (`app/check/search.py`).** The RPC is already done.
   - Acceptance:
     - Offline tests with MockTransport.
     - Results carry every field.
     - Supabase and OpenAI errors map to the check error classes.
     - An opt-in live test (`-m check`) returns ≤ 8 results with ≤ 2 per document for 3 sample claims.
2. **#48 OpenAI Responses client and the Luna verifier, with the groundedness guard.**
   - Acceptance:
     - A schema-valid answer is parsed, and a malformed answer becomes an upstream error.
     - A fabricated quote becomes unclear.
     - 401, 429 and 5xx are handled.
     - The key and claim are never logged.
3. **#49 Jev per-source stance, `aggregate.py` and `POST /check`.**
   - Acceptance:
     - Every aggregation rule is tested, including 4 supporting + 1 contradicting giving `disputed`, and a Luna/Jev split giving unclear and `needs_review`.
     - A Jev failure still returns a verdict flagged for review.
     - The error codes are as listed.
     - Luna and Jev run concurrently.
     - An opt-in live smoke test runs 3 claims end to end and checks the response shape.
4. **#50 Frontend checker, card and docs.**
   - Acceptance:
     - Vitest covers the HTTP checker (abort, errors, concurrency).
     - The card renders every verdict, the grouped sources and the review banner.
     - The label is updated, and `npm run type-check` passes.
     - CLAUDE.md and backend/README are updated.

5. **#51 Backend v2** (see [v2 (issue #51)](#v2-issue-51)).
   - Acceptance:
     - Offline tests: query generation (schema, 1–3 queries, fallback on failure), multi-query merge (RRF order, dedupe, per-doc cap, top k), the single Jev question (no Luna verdict in its state) and its parsing, every review rule including confident agreement → no review, Luna before Jev, a Jev failure keeping the verdict, zero results → `unverified` with no judging or Jev call, the response shape, and no claim text in logs.
     - The live smoke test prints the search queries, verdict, confidence, reasons, per-source stances, latency and Luna tokens for 3 claims.
6. **#52 Frontend v2:** the card shows `confidence` and the new review reasons; per-source Jev fields are gone.
7. **#55 Search speed** (see [v3 search speed](#v3-search-speed-issue-55)).
   - Acceptance: offline tests for the schema (`key_terms`, `cancer` enum), key-term cleaning, every fallback, the RPC body (`query_text` = key terms, `cancer` only when known), the migration text (both overloads dropped, filter inside the keyword candidates, grants) and the eval variant and latency table. The migration is run by hand in the SQL editor.

Waves: #47 and #48 in parallel, then #49, then #50. #50 can start alongside #49 against the response contract above. #51 and #52 in parallel against the v2 response contract.
