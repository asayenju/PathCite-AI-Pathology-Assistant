# PRD: Ingest the cancer evidence corpus into Supabase

## What
Step 2 of the fact checker, after [Jev triage](PRD-jev-triage.md). It takes the cancer evidence we downloaded, splits it into chunks, embeds them with OpenAI and stores them in Supabase (Postgres + pgvector), so the claim checker has evidence it can search and cite.

**Input.** `backend/scripts/fetch_cancer_corpus.py` writes these files to `backend/.cache/corpus/` (gitignored):

| File | Contents | Size |
|---|---|---|
| `guidelines.jsonl` | 371 clinical guidelines from [epfl-llm/guidelines](https://hf.co/datasets/epfl-llm/guidelines): 177 NICE, 95 PubMed-sourced (91 linked to PubMed), 99 WikiDoc | 10.9 MB |
| `pubmed.jsonl` | 1,796 PubMed review and guideline abstracts (2016–2026), found by medical subject heading | 1.8 MB |

Both cover the 6 cancers in the sample transcripts: colorectal, breast, melanoma, DLBCL lymphoma, prostate and thyroid. Every document has a URL.

**Output.** Two Supabase tables: `documents`, with one row per source including its URL, and `chunks`. Each chunk row holds its text, the heading path inside its document, cancer tags, source and trust level, and a 1536-dimension embedding.

**Search is not part of this PRD.** The search algorithm gets its own design.

## Why
- **Answers need sources.** The checker can only give a verdict a doctor will trust if it can point to a source. That needs the evidence stored with its citation, not just as vectors.
- **Chunks should match how guidelines are written.** Guidelines are long (median 21K characters, up to 181K) and organised under headings. A single embedding of a whole guideline blurs all its topics together. Chunks that follow its headings, and that are split by meaning inside long sections, keep each chunk about one thing.
- **It fits the free plan.** About 10.4M characters remain after dropping boilerplate sections. That is about 10K chunks (the real plan: about 10,050) and an estimated 170 MB, under the free plan's 500 MB.
- **Why lexical-only dedupe.** Only 0.2% of paragraphs are exact repeats. The real saving is dropping low-value sections (cost-effectiveness, price, committee lists, references), about 19% of the guideline text. Deduplicating by *meaning* would be dangerous for a fact checker, because "X is recommended" and "X is not recommended" embed at about 0.95 similarity and one would be silently lost.

## How
**Keys** go in the repo-root `.env` (gitignored; `.env.example` lists them):
- `OPEN_AI_VECTOR_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

The secret key is used only by backend scripts, never by the frontend.

**Libraries** (pinned):

| Job | Library | Why |
|---|---|---|
| Structural chunking | `langchain-text-splitters` `MarkdownHeaderTextSplitter` | Almost every guideline has markdown headings (177/177 NICE, 94/95 PubMed-sourced, 98/99 WikiDoc) |
| Semantic chunking | our own breakpoint code (numpy over our sentence splitter) | Planned as `chonkie[openai]` `SemanticChunker`, but Chonkie 1.7 splits sentences on plain delimiters and cuts "Dr. Smith" and "Fig. 3", so the fallback below was used and Chonkie is not a dependency |
| Near-duplicate detection | `datasketch` (MinHash + LSH) | Lexical, fast, no embeddings needed |
| Token counting | `tiktoken` (`cl100k_base`) | The tokenizer `text-embedding-3-*` uses |
| API calls | `httpx` | The same pattern as `app/triage/jev.py`, so no extra SDKs |

Both steps live behind our own `app/corpus/chunking.py` interface.
- **Sentence-boundary rules:** the tests require that no chunk is cut at "e.g.", "i.e.", "vs.", "Dr.", "Fig. 3", "1.2 mm" or after an initial.
- **Fallback (used):** Chonkie's sentence splitter can't meet those rules, so the breakpoint step is implemented with numpy on our own sentence splitter.

**Pipeline** (`backend/app/corpus/`, run with `backend/scripts/ingest_corpus.py`):
1. **Load** both files with Unicode NFC normalisation. Each document gets a trust level:

   | Source | Trust |
   |---|---|
   | NICE | high |
   | PubMed-sourced guideline | high |
   | PubMed abstract | medium |
   | WikiDoc | low |

2. **Document dedupe:**
   - An abstract whose PMID matches a guideline's `pmid` is dropped, because the full text wins (5 cases).
   - Exact duplicate texts are dropped, keeping the more trusted source (6 cases).
3. **Structural split** at markdown headings. Each section gets a path such as `Melanoma: assessment and management › Staging › Sentinel lymph node biopsy`.
   - A document with no headings, and every abstract, becomes one section named after its title.
   - A heading with no body is dropped, but stays in the paths of its subsections.
4. **Section filter:** sections whose heading matches a configured boilerplate list are dropped. The list covers cost, price, PPRS, economic, marketing authorisation, committee, references, acknowledgements, information for the public, implementation, about this, external links, see also and historical perspective. The run reports how many characters were dropped.
5. **Semantic split:**
   - **Short sections:** a section of up to 512 tokens is one chunk.
   - **Long sections:** they are cut where similarity between neighbouring sentence windows drops.
   - **Over 512 tokens:** a piece is cut at its weakest point, or at a sentence boundary as a last resort.
   - **Under 64 tokens:** a piece is merged into its neighbour in the same section, never across a heading.
   - **Under 10 tokens:** a piece is dropped.
   - Bulleted lists stay together where the size allows.
6. **What gets embedded:** `"{section path}\n\n{chunk text}"`. Only the text is stored in `content`; the path goes in `section`. URLs and IDs are never embedded.
7. **Chunk dedupe:** exact matches (SHA-256 of normalised text) and near-duplicates (MinHash over 5-word shingles, Jaccard ≥ 0.95, configurable) are skipped, keeping the more trusted copy. Skips are counted in the report. The threshold was raised from 0.9 because at 0.9 a short WikiDoc chunk about MRI was dropped as a copy of one about CT (Jaccard 0.900). A near-duplicate is also never dropped when its negations, numbers, stage numerals or comparison signs differ: in a long chunk one inserted "not" still scores about 0.97.
8. **Chunk plan cache:** steps 1–7 write `backend/.cache/corpus/chunks.jsonl`, which ingest reads.
   - **Why:** OpenAI embeddings aren't bit-for-bit deterministic, so re-chunking could move boundaries and change every hash.
   - **When to re-chunk:** only with `--rechunk`. The plan records a hash of the chunker config.
9. **Embed** with `text-embedding-3-small` at 1536 dimensions, in batches of up to 2,048 inputs and under the per-request token limit.
   - **Errors:** `429` and `5xx` are retried with backoff. `401` stops with a clear message.
   - **Key:** never logged.
10. **Write** one document at a time with the RPC `ingest_document(doc jsonb, chunks jsonb)`.
    - **Atomic:** a single transaction replaces that document's chunks, so a crash never leaves a document half-ingested.
    - **Resume:** documents whose stored hashes already match the plan are skipped, so a re-run only embeds what is missing.
11. **Guards:**
    - **Model and config locks:** `corpus_meta` stores `embedding_model`, `dims` and `chunker_config_hash`. The CLI refuses to mix models or chunker configs unless given `--rebuild`, which truncates and re-ingests.
    - **`--dry-run`:** prints documents, chunks, tokens, estimated cost and estimated size, without any API calls.
    - **`--limit N`:** ingests only N documents.

**Schema** (`supabase/migrations/20261127000000_corpus.sql`). There's no Postgres connection string, so the user runs it once in the Supabase SQL editor. It's written to be safe to run twice.
- `create extension if not exists vector;`
- **`documents`:** `id text pk`, `source`, `trust`, `title`, `url not null`, `pmid`, `journal`, `year int`, `pub_types text[]`, `cancers text[]`, `ingested_at`.
- **`chunks`:**
  - **Columns:** `id bigint identity pk`, `doc_id → documents on delete cascade`, `ord`, `section`, `content`, `content_hash`, `token_count`, `cancers text[]`, `source`, `trust`, `embedding vector(1536) not null`, a generated `fts tsvector` (over `section` and `content`), and `unique(doc_id, ord)`.
  - **Indexes:** HNSW on `embedding` (`vector_cosine_ops`), GIN on `fts`, GIN on `cancers`.
  - `cancers`, `source` and `trust` are copied from the document so search can filter without a join.
- **`corpus_meta(key text pk, value jsonb)`.**
- **RPCs:**
  - `ingest_document(doc, chunks)`.
  - `corpus_stats()`, which returns row counts and the size of each table and index.
  - Both are `security definer`, with execute permission revoked from `anon` and `authenticated`.
- **Row-level security** is on for all three tables, with no policies. Only the secret key, which bypasses it, can read or write. So the publishable key can't read the licensed guideline text through Supabase's automatic REST API.

**Cost:** about 2.6M tokens for the chunks, plus the sentence-window embeddings used to find semantic breakpoints. Well under $1 with `text-embedding-3-small`.

### Quality filter (issue #46)
Profiling the first full ingest found chunks that would pollute search for clinical claims. `app/corpus/quality.py` drops them after chunk dedupe and before embedding, on the loaded plan, so nothing is re-chunked.
- **Rules** (first match wins; counts on the real plan, 10,009 → 9,543 chunks):

  | Rule | Drops | Removed |
  |---|---|---|
  | Sandbox | WikiDoc documents whose title starts with "Sandbox" | 57 (4 documents) |
  | NICE economic | NICE sections whose own headings (path after the title) are about the model, utility values, comparators, decision problem, extrapolation, end of life, managed access, severity/QALYs/ICERs, resource use, innovation or equality; "Cancer Drugs Fund" only in the section's last heading, so a CDF reconsideration keeps its clinical subsections. Any heading about clinical effectiveness/evidence/efficacy, an indirect comparison or a network meta-analysis keeps the section | 307 |
  | Research | "research recommendation(s)", "recommendations for (further) research", "research question(s)" headings, and chunks under 40 tokens that are only questions | 56 |
  | Cross-reference | a chunk that is only "Recommendations 1.2.1 to 1.2.8", "Recommendation 1.3.4", "See 1.2" | 38 |
  | Wiki navigation | WikiDoc chunks with under 10 tokens left once "click here" sentences are removed | 6 |
  | Garbled table | over 50 characters, letters under 50% of the characters, and under half the text in lines of 60+ characters (so a numeric-heavy results sentence is kept) | 2 |

  WikiDoc patient-information pages are kept.
- **Hash:** the rules live in `QualityFilterConfig`, whose hash is stored as `quality_filter_hash` in `corpus_meta`. It is not part of `chunker_config_hash`, and a changed filter is not refused: it only removes chunks, and resume re-ingests (and re-embeds) the documents whose chunk list changed. Kept chunks keep their `ord`, so a document can have gaps.
- **`--prune`** deletes stored documents the filtered plan no longer has (the 4 Sandbox documents). Without it they are only reported.

## Edge cases
- **Document structure:**
  - A document with no headings.
  - A heading with an empty body.
  - Headings nested more than 3 levels deep.
  - A section that is only a list.
  - A 180K-character guideline.
  - An abstract with labelled parts.
  - Non-ASCII hyphens (‐) and NFC normalisation.
- **Sentence boundaries:** "e.g.", "i.e.", "vs.", "Dr.", "Fig. 3", "1.2 mm" and initials are never cut.
- **Size:**
  - No chunk is over 512 tokens or under 10.
  - Merging never crosses a heading.
  - The embedding input stays under OpenAI's 8,191-token limit.
- **Dedupe:**
  - A guideline overlapping with its own abstract.
  - The same title from different sources ("Colorectal cancer" from both NICE and WikiDoc).
  - Near-duplicate NICE boilerplate across appraisals.
  - **The negation case.** "X is recommended" and "X is not recommended" must both be kept.
- **OpenAI:**
  - `429` and `5xx` mid-run: resume works.
  - `401`: a clear error.
  - Wrong embedding length: rejected.
  - Empty input: never sent.
- **Supabase:**
  - Project paused or unreachable: a clear message.
  - One document's RPC fails: the error is reported, the others continue, and that document is retried next run.
  - Model or config mismatch: refused without `--rebuild`.
- **Secrets:** keys never appear in logs or reports. The corpus is public or licensed literature, never patient data, so logging document titles is fine.

## Non-goals
- **Out of scope:**
  - The search algorithm, retrieval evaluation and checker verdicts.
  - Re-fetching or widening the corpus.
  - Auth.
- **Deferred to the search PRD:** changing the review label. At search time, claims will be sent to OpenAI to be embedded, so that PRD must update the label, which today says only that doctor turns go to TypeSafe. Ingest itself never sees a transcript.

## Testing
- **Offline (`pytest`):**
  - The chunking tests use a deterministic fake embedder.
  - The OpenAI and PostgREST clients are tested against `httpx.MockTransport`.
  - Every edge case above has a test.
- **Opt-in live (`pytest -m corpus -s`, excluded by `addopts` like `hf` and `jev`):** ingests 3 documents into the real project and checks the counts.
- **Manual:**
  1. Run the migration in the SQL editor.
  2. Run `scripts/ingest_corpus.py --dry-run`, then `--limit 3`, then the full ingest. Then a second full run, which must embed 0 chunks.
  3. `corpus_stats()` must show under 300 MB.
  4. Spot-check melanoma chunks and their URLs in the SQL editor.

## Issues (GitHub #41–#45, label `corpus-ingest`; dependencies: {1, 2, 3} → 4 → 5)
1. **Corpus prep and chunking (offline):** loading, trust levels, document dedupe, the structural split, the section filter, the semantic split behind an `Embedder` protocol, size limits, the `chunks.jsonl` plan and the `--dry-run` report.
2. **Supabase schema migration:** tables, indexes, row-level security, the `ingest_document` and `corpus_stats` RPCs, revoked grants, and `supabase/README.md`.
3. **OpenAI embedder client:** batching, retries, error mapping, the length check, and `OPEN_AI_VECTOR_KEY` added to `.env.example`.
4. **Chunk-level near-duplicate dedupe:** MinHash with trust-ordered keep and the negation regression test.
5. **Ingest CLI and the real run:** embed and write, resume, guards, the stats report, the live smoke test, the full ingest, and the docs.
6. **Quality filter (#46, after 5):** drop low-value chunks before embedding with its own `quality_filter_hash`, `--prune`, and the real clean-up run.
