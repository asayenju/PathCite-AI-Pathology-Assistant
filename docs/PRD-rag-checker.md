# PRD: RAG claim checker (hybrid search + GPT-6 Luna + Jev per-source stance)

Issues: GitHub #47–#50, label `rag-checker`. Builds on `docs/PRD-jev-triage.md` (claims) and `docs/PRD-corpus-ingest.md` (the evidence corpus).

## What

Replace the placeholder `ClaimChecker` (`frontend/src/lib/claimChecker.ts`, answers `not_checked` for every claim) with a real fact check:

1. **Search:** hybrid search over the Supabase corpus (2,151 documents, 9,543 chunks of NICE, PubMed-sourced guidelines, PubMed abstracts and WikiDoc). It uses `match_chunks`, which is done and committed.
2. **Judge each source separately.** GPT-6 Luna (OpenAI) and Jev (TypeSafe) each say, for every retrieved passage, whether it **supports**, **contradicts** or is **irrelevant** to the doctor's claim.
3. **Verdict by code rules, not a model and not a majority vote.**
4. **Show it on the claim card:** the verdict, the sources grouped by stance (with trust level, year, the exact quote and a link), and a **"Needs your review"** banner when the sources or the two models disagree.

## Why

- **A doctor needs to see evidence, not a model's opinion.** Every supporting or contradicting statement must be a word-for-word quote from a retrieved passage, with its URL.
- **Conflicts must stay visible.** If 4 sources support a claim and 1 contradicts it, a single combined verdict hides the conflict. A majority vote is also wrong: one current NICE guideline outweighs four WikiDoc pages. So a mix of support and contradiction is always shown as **"Sources disagree"** and sent to review. Nothing is resolved silently.
- **Two independent models catch each other's misses.** A passage only counts as support or contradiction when Luna and Jev agree on it. If they disagree, the passage is marked **unclear** and the card goes to review.
- **Cost:**
  - `gpt-6-luna` is $0.10 in / $0.50 out per 1M tokens: about $0.0005 a claim, about $0.015 a transcript.
  - Claim embeddings are about $0.02 per 1M tokens, effectively free.
  - Jev adds one request per claim.

## How

### 1. Search (done: `supabase/migrations/20261127010000_match_chunks.sql`)
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

### 3. Jev per-source stance (`app/check/jev_check.py`)
- **`judge_sources(claim, context, chunks)`** reuses `JevClient`'s transport, retries and errors.
  - It asks one Choice per chunk, `stance_<i>`: supports / contradicts / irrelevant, with the same criteria as Luna, all in one request.
  - `state` holds the claim, the context and the chunks.
- **Runs concurrently with Luna.**
- **If Jev fails,** the verdict is computed from Luna's stances alone, with `needs_review = true` and Jev's fields `null`.

### 4. Aggregation (`app/check/aggregate.py`, pure, fully unit-tested)
- **Per chunk:**
  - **Both models agree, and Jev's p ≥ `check_jev_min` (0.6):** that stance counts.
  - **They disagree, or Jev's p is too low:** the chunk is **unclear**.
  - **If both say irrelevant,** the chunk is irrelevant.
- **The verdict:**

  | Relevant chunks (supports or contradicts) | Verdict |
  |---|---|
  | none | `unverified` |
  | all support | `accurate`, or `incomplete` if Luna reports `missing_qualifier` |
  | all contradict | `inaccurate` |
  | any mix | `disputed` ("Sources disagree"), always `needs_review` |

- **Any unclear chunk** sets `needs_review`.

### 5. Endpoint `POST /check` (`app/check/router.py`)
- **Request:** `{turn_id, quote, context[]}`. This is the frontend's `ClaimToCheck`.
- **Response:**
  ```
  {status:"checked", verdict, issue, suggestion, needs_review, review_reasons:[...],
   sources:[{chunk_id, title, section, url, trust, year, quote, stance,
             luna_stance, jev_stance, jev_p}],
   model, jev_model}
  ```
- **Errors** use `{detail, code}`:
  - `check_unavailable` (503): a missing OpenAI or Supabase key.
  - `check_rate_limited` (429).
  - `check_upstream_error` (502).
  - `invalid_check_request` (422).
- **Settings:** `openai_key` (alias `OPEN_AI_KEY`), `checker_model`, `check_top_k=8`, `check_jev_min=0.6`.
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

Waves: #47 and #48 in parallel, then #49, then #50. #50 can start alongside #49 against the response contract above.
