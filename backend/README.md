# PathCite backend

FastAPI service that classifies the tissue type of a colorectal H&E patch with the fine-tuned ResNet18 in `../model/resnet18_finetuned_v2.pt`.

> **Research use only. Not a medical device and not a diagnosis.** `confidence` is the model's raw softmax score, not the probability of being correct. The model has no "not tissue" class: any image gets one of 9 labels, so check `uncertain` and `warnings`.

## Run

```
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

## Endpoints

### `GET /health`
Always `200`: `{"status": "ok", "model_loaded": true}`, or `{"status": "degraded", "model_loaded": false}` when the weights file is missing (then only `/predict` is unavailable and returns 503).

### `POST /predict`
Multipart upload, field name `file`: one PNG, JPEG or TIFF image (max 10 MB).

```
curl -F file=@patch.png http://localhost:8000/predict
```

```json
{
  "label": 8,
  "tissue": "TUM",
  "confidence": 0.97,
  "uncertain": false,
  "probabilities": {"ADI": 0.0, "BACK": 0.0, "DEB": 0.0, "LYM": 0.0, "MUC": 0.0, "MUS": 0.0, "NORM": 0.0, "STR": 0.03, "TUM": 0.97},
  "warnings": [],
  "model_version": "resnet18_finetuned_v2.pt@b1850cdb",
  "notice": "Research use only. Not a medical device and not a diagnosis."
}
```

- `uncertain` is true when `confidence` is below `UNCERTAIN_THRESHOLD`. A result is always returned.
- `warnings` is non-empty when the input is not square or not about 224x224 (resized to 224x224 like in training; a resized whole slide is not a valid patch).
- Tissue codes: ADI adipose, BACK background, DEB debris, LYM lymphocytes, MUC mucus, MUS smooth muscle, NORM normal mucosa, STR stroma, TUM tumor epithelium.

### `POST /transcripts/import`
Multipart upload, field name `file`: one JSON transcript (max `MAX_TRANSCRIPT_MB`, 20 MB) from Deepgram, AWS Transcribe / Transcribe Medical, WhisperX with diarization, or OpenAI `diarized_json`. The format is detected from the content, not the file name. Works while the model is not loaded.

```
curl -F file=@visit.json http://localhost:8000/transcripts/import
```

```json
{
  "source": "deepgram",
  "language": "en",
  "duration_s": 73.25,
  "speakers": [
    {"id": "S1", "source_label": "spk_0", "word_count": 210, "talk_time_s": 41.3,
     "preview": ["Hi, thanks for coming in. Do you want to sit down?"], "suggested_role": "doctor"},
    {"id": "S2", "source_label": "spk_1", "word_count": 64, "talk_time_s": 12.8,
     "preview": ["Thanks. I've been pretty nervous about the results."], "suggested_role": "patient"}
  ],
  "turns": [
    {"id": "t0001", "speaker": "S1", "start": 0.3, "end": 4.12,
     "text": "Hi, thanks for coming in. Do you want to sit down?", "is_final": true,
     "words": [{"text": "Hi,", "start": 0.3, "end": 0.51, "confidence": 0.946}],
     "min_confidence": 0.82}
  ],
  "warnings": [],
  "notice": "Research use only. Not a medical device and not a diagnosis."
}
```

- `suggested_role` only pre-fills the UI; the user assigns roles. It is `null` on a tie, with one speaker, or with more than 2 speakers and no clear lead.
- Text keeps the original wording (Unicode NFC only). Word `confidence` is kept (null if the vendor gives none); words below `ASR_LOW_CONFIDENCE` count as low-confidence.
- `warnings` (the import still succeeds): language other than English; a speaker labelled `unknown`; only one speaker; more than 4 speakers; overlapping speech (a turn starts over 0.5 s before the previous one ends); more than 20% of words below `ASR_LOW_CONFIDENCE`.
- Vendor metadata (AWS `jobName`, `accountId`, S3 URIs, Deepgram `request_id`) is never returned.

### `POST /triage`
JSON body: doctor turns only, in transcript order (at most `TRIAGE_MAX_TURNS` turns of up to `TRIAGE_MAX_TURN_CHARS` characters each, with unique ids). `words` is optional. Needs `JEV_KEY`. Works while the image model is not loaded.

```
curl -N -H 'content-type: application/json' http://localhost:8000/triage \
  -d '{"turns": [{"id": "t0001", "text": "Good morning. The growth in your sigmoid colin is small.",
                  "words": [{"text": "colin", "confidence": 0.31}]}]}'
```

Each turn is split into sentence spans, and [Jev](https://docs.typesafe.ai/models) (TypeSafe AI) labels each span. The response streams NDJSON (`application/x-ndjson`), one event per line. For each turn, in request order:

```
{"type": "turn_started", "turn_id": "t0001"}
{"type": "span", "turn_id": "t0001", "start": 0, "end": 13, "quote": "Good morning.", "kind": "skip", "base_kind": "skip", "p_claim": 0.03, "p_asr": 0.01}
{"type": "span", "turn_id": "t0001", "start": 14, "end": 56, "quote": "The growth in your sigmoid colin is small.", "kind": "transcription", "base_kind": "claim", "p_claim": 0.41, "p_asr": 0.93}
{"type": "turn_done", "turn_id": "t0001"}
{"type": "done", "model": "jev-1.14.0"}
```

- `start`/`end` are character offsets into the turn text, and `quote` is `text[start:end]`.
- `kind` is `transcription` when `p_asr >= TRIAGE_ASR_FLAG`. In that case `base_kind` is what the span becomes if the doctor confirms the words. Otherwise the span is `skip` when `1 - p_claim >= TRIAGE_SKIP_MIN`, and `claim` if not. When unsure, a span is a claim.
- Filler spans ("Um, okay.") are never sent to Jev. They come back as `skip` with `p_claim: 0` and `p_asr: null`. Empty and all-filler turns make no Jev call but still emit `turn_started` and `turn_done`.
- Each turn is one Jev request (split above 40 spans). The request includes the 2 earlier turns of the request as context, and, as a hint, the words below `ASR_LOW_CONFIDENCE` that occur in each span. Only text is sent: no ids, speaker labels or file names.
- Turns run one after another, and the run stops when the client disconnects.
- Errors before the stream starts are the usual JSON (see below). Once it has started, an error arrives as a final `{"type": "error", "code": "..."}` line with `triage_unavailable`, `triage_rate_limited` or `triage_upstream_error`. The events before it still stand.

### `POST /triage/span`
JSON body `{"text": "...", "previous_doctor_turns": ["..."]}` (only the last 2 turns are used). It classifies a doctor's typed correction as a single span, without the transcription question.

```
curl -H 'content-type: application/json' http://localhost:8000/triage/span -d '{"text": "The growth in your sigmoid colon is small."}'
```

```json
{"kind": "claim", "p_claim": 0.41}
```

`kind` is `skip` or `claim`. Errors are JSON, with the codes below.

### `POST /check`
Fact-checks one doctor claim against the evidence corpus (`docs/PRD-rag-checker.md`). JSON body `{"turn_id": "t0005", "quote": "...", "context": ["..."]}`: `quote` is the claim sentence (1-2000 characters, not blank), `context` the previous doctor turns, oldest first (only the last 2 are used, each at most 5000 characters).

```
curl -H 'content-type: application/json' http://localhost:8000/check \
  -d '{"turn_id": "t1", "quote": "Colon cancers are tested for mismatch repair proteins.", "context": []}'
```

```json
{"status": "checked", "verdict": "accurate", "issue": null, "suggestion": null,
 "needs_review": false, "review_reasons": [],
 "sources": [{"chunk_id": 812, "title": "Molecular testing strategies for Lynch syndrome ...", "section": "...",
              "url": "https://...", "trust": "high", "year": null, "quote": "Offer testing to all people with ...",
              "stance": "supports", "luna_stance": "supports"}],
 "search_queries": ["Colon cancers are tested for mismatch repair proteins.",
                    "colorectal cancer mismatch repair protein immunohistochemistry testing"],
 "confidence": {"label": "supported", "p": 0.99, "p_verdict": 0.99, "agrees": true},
 "model": "gpt-6-luna", "jev_model": "jev-1.14.0"}
```

Steps, in order (v2, issue #51):
1. **Queries:** GPT-6 Luna writes 1-3 short search queries for the general medical fact (strict JSON, low reasoning effort). The raw claim is always searched too. If this call fails, only the claim is searched.
2. **Search:** each query runs hybrid `match_chunks` concurrently (top `CHECK_TOP_K`, at most 2 per document); the lists are merged by reciprocal rank fusion (sum of 1/(60 + rank)), deduplicated by chunk, at most 2 per document, top `CHECK_TOP_K` (8). The first search after the free project has been idle can get a JSON 500 (statement timeout); it is retried once.
3. **Luna judges each passage** (OpenAI Responses API, word-for-word quotes, groundedness guard: a quote not found in its passage makes the stance `unclear`, which counts as not relevant).
4. **Verdict** over Luna's supporting/contradicting passages: none `unverified`; all support `accurate` (`incomplete` if Luna says a qualifier is missing); all contradict `inaccurate`; a mix `disputed`. No majority vote.
5. **Jev's confidence**, after Luna: one TypeSafe Choice over all passages, supported / contradicted / insufficient. Jev never sees Luna's answer.

- `sources`: the merged passages, best first (the frontend hides irrelevant ones). `quote` is Luna's grounded quote, `""` when there is none. `stance` equals `luna_stance`.
- `search_queries`: what was searched, the raw claim first.
- `confidence`: Jev's top `label` and its `p`; `p_verdict` is Jev's probability for the class of Luna's verdict (accurate/incomplete = supported, inaccurate = contradicted, unverified = insufficient) and `agrees` whether Jev's top label is that class. Both are `null` for `disputed`. The whole object is `null` when Jev is unavailable.
- `issue` / `suggestion`: Luna's, for `inaccurate`, `incomplete` and `disputed`; `null` otherwise.
- `review_reasons`, ordered: `sources_disagree` (disputed), `jev_unavailable` (no `JEV_KEY`, or Jev failed: the verdict rests on Luna alone, `jev_model` is `null`), otherwise `jev_disagrees` (Jev's top label isn't Luna's class) or `low_confidence` (Jev's probability for Luna's class < `CHECK_JEV_MIN`, 0.7). `needs_review` is true exactly when there is a reason; confident agreement needs no review.
- No search results: `verdict: "unverified"`, `sources: []`, `needs_review: false`, `confidence: null`; only the query call is made.
- A missing `JEV_KEY` is not an error. Errors are JSON with the codes below.

### Errors
Always JSON: `{"detail": "...", "code": "..."}` (for `/triage`, only before the stream starts).

| Status | code | When |
|---|---|---|
| 413 | `file_too_large` | upload over `MAX_UPLOAD_MB` (`/predict`) or `MAX_TRANSCRIPT_MB` (`/transcripts/import`) |
| 415 | `unsupported_type` | not PNG/JPEG/TIFF (detected from content, not file name) |
| 422 | `empty_file`, `corrupt_image`, `image_too_large`, `invalid_request` | empty or unreadable file, too many pixels, missing `file` field |
| 422 | `empty_file`, `invalid_json` | transcript upload empty, or not UTF-8 JSON |
| 422 | `unsupported_transcript_format` | unrecognised transcript, AWS Call Analytics or HealthScribe |
| 422 | `undiarized_transcript` | no speaker labels (plain Whisper, WhisperX without `--diarize`) |
| 422 | `empty_transcript` | transcript contains no speech |
| 422 | `invalid_triage_request` | triage body malformed, no turns, more than `TRIAGE_MAX_TURNS` turns, a turn over `TRIAGE_MAX_TURN_CHARS`, duplicate turn ids, or blank `/triage/span` text |
| 422 | `invalid_check_request` | `/check` body malformed, blank or over-long `quote`, or a context turn over 5000 characters |
| 429 | `check_rate_limited` | OpenAI or Supabase still rate-limits after retries (`/check`) |
| 429 | `triage_rate_limited` | Jev still rate-limits after 2 retries |
| 500 | `internal_error` | unexpected error (no details are returned) |
| 502 | `triage_upstream_error` | Jev timed out, returned a 5xx, or sent a malformed answer |
| 502 | `check_upstream_error` | OpenAI or Supabase timed out, returned a 5xx, or sent a malformed answer (`/check`) |
| 503 | `check_unavailable` | `OPEN_AI_KEY`, `OPEN_AI_VECTOR_KEY`, `SUPABASE_URL` or `SUPABASE_SECRET_KEY` missing, or a key was rejected (`/check`) |
| 503 | `model_unavailable` | model not loaded |
| 503 | `triage_unavailable` | no `JEV_KEY` configured ("Set JEV_KEY in .env"), or Jev rejected the key |

## Configuration (environment variables or `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MODEL_PATH` | `../model/resnet18_finetuned_v2.pt` | weights file; if missing the app runs degraded (no `/predict`), if mismatched or corrupt startup fails |
| `UNCERTAIN_THRESHOLD` | `0.7` | confidence below this sets `uncertain` |
| `MAX_UPLOAD_MB` | `10` | max upload size |
| `MAX_IMAGE_PIXELS` | `25000000` | max width x height, guards against decompression bombs |
| `MAX_TRANSCRIPT_MB` | `20` | max transcript JSON upload size |
| `ASR_LOW_CONFIDENCE` | `0.6` | transcript words with speech-to-text confidence below this are low-confidence (warning if over 20% of words) |
| `JEV_KEY` | unset | TypeSafe AI key for Jev. Without it `/triage` and `/triage/span` return 503, and the rest of the app works |
| `JEV_MODEL` | `jev-1.14.0` | pinned Jev model |
| `JEV_TIMEOUT_S` | `15` | Jev request timeout in seconds |
| `TRIAGE_ASR_FLAG` | `0.5` | `p_asr` at or above this marks a possible transcription error |
| `TRIAGE_SKIP_MIN` | `0.8` | P(small talk) at or above this skips a span; below it the span is a claim |
| `TRIAGE_MAX_TURNS` | `500` | max turns per `/triage` request |
| `TRIAGE_MAX_TURN_CHARS` | `5000` | max characters per turn (and per `/triage/span` text) |
| `OPEN_AI_KEY` | unset | OpenAI key for the Luna verdict in `/check` (503 without it) |
| `OPEN_AI_VECTOR_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | unset | claim embedding and evidence search for `/check` (503 without them); see Evidence corpus |
| `CHECKER_MODEL` | `gpt-6-luna` | OpenAI model for the search queries and the per-passage stance |
| `CHECK_TOP_K` | `8` | passages kept per claim after merging the searches |
| `CHECK_JEV_MIN` | `0.7` | Jev's probability for Luna's verdict below this sends the card to review (`low_confidence`) |

## Privacy
Uploaded images and transcripts are processed in memory only: never written to disk (uploads are not spooled to temp files) and never logged. Unexpected errors log only the exception type. `/triage` sends doctor turn text to TypeSafe, which has no BAA, so use synthetic or de-identified text only. The text, Jev's requests and answers, and `JEV_KEY` are never logged or stored. `/check` sends the claim and up to 2 previous doctor turns to OpenAI (embedding and the Luna verdict, `store: false`), Supabase (as the keyword query) and TypeSafe (Jev); patient turns are never sent. The claim, context, verdict, keys and request/response bodies are never logged or stored: failures log only the exception type. The Supabase secret key is read by the API server for `/check` and never leaves the backend.

## Evidence corpus (Supabase)

An offline pipeline, not part of the API, that stores the cancer evidence the claim checker will search (`docs/PRD-corpus-ingest.md`). Run from `backend/` with the venv:

1. **Fetch** the corpus (NICE/PubMed/WikiDoc guidelines from `epfl-llm/guidelines` and PubMed abstracts; no keys needed) into `.cache/corpus/` (gitignored; the guideline licences vary, so keep it out of git):
   ```
   python scripts/fetch_cancer_corpus.py
   ```
2. **Migrate** once: paste `supabase/migrations/20261127000000_corpus.sql` into the Supabase SQL editor, then check it with `python scripts/verify_supabase_schema.py` (see `supabase/README.md`).
3. **Dry run** (no API calls; breakpoints come from a local stand-in embedder, so the counts are approximate):
   ```
   python scripts/ingest_corpus.py --dry-run
   ```
4. **Ingest**, first a few documents, then everything:
   ```
   python scripts/ingest_corpus.py --limit 3
   python scripts/ingest_corpus.py
   ```
   The first real run builds the chunk plan `.cache/corpus/chunks.jsonl` with OpenAI breakpoints and reuses it after that (`--rechunk` rebuilds it). Each document is written atomically by the `ingest_document` RPC. Re-runs skip documents whose stored chunk hashes match the plan, so an interrupted run just continues and a second full run embeds nothing. A document that fails is reported, the others continue, and the exit code is 1 (re-run to retry it). Documents in Supabase that the plan no longer has are reported, and deleted (with their chunks) only with `--prune`.
   - **Quality filter** (`app/corpus/quality.py`, issue #46): after chunking and dedupe, before embedding, it drops low-value chunks from the loaded plan: WikiDoc "Sandbox…" pages, NICE economic-modelling sections (matched on the section's own headings: model, utility values, comparators, extrapolation, end of life, Cancer Drugs Fund, severity/QALYs/ICERs, resource use, innovation, equality; a section with a clinical-effectiveness/evidence/efficacy heading is kept), research recommendations and short question-only chunks, cross-reference stubs ("Recommendations 1.2.1 to 1.2.8"), WikiDoc "click here" navigation and garbled tables. It only removes whole chunks, so the cached plan is reused and kept chunks keep their `ord`; documents that lost chunks are re-embedded and rewritten by the normal resume. Its config has its own hash, recorded as `quality_filter_hash` in `corpus_meta` (a change is not refused). The dry run prints the removed count per rule and per source, and the patterns. On the real corpus it removed 466 of 10,009 chunks (economic 307, sandbox 57, research 56, cross-reference 38, navigation 6, tables 2) and emptied the 4 Sandbox documents.
   - `corpus_meta` locks the embedding model, dimensions and chunker config. A run with a different one is refused (exit 2) unless `--rebuild`, which deletes every document, chunk and lock first.
   - `--prune`: after the ingest, delete stored documents the (filtered) plan no longer has, for example documents the quality filter emptied.
   - Other flags: `--env PATH` (default: repo-root `.env`), `--corpus-dir PATH`.
   - Supabase free projects pause when idle: the CLI then says "Supabase project unreachable or paused — open the dashboard to resume it".

**Keys** (repo-root `.env`, gitignored; see `.env.example`): `OPEN_AI_VECTOR_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`. The secret key bypasses row-level security, so it is for backend scripts only: never the frontend, never logged. Keys never appear in the CLI's output or errors.

**Cost and size:** about 1.0M tokens for the semantic breakpoints (first run only) plus 2.3M for the chunks, so about $0.07 with `text-embedding-3-small` ($0.02/1M). The report prints the actual tokens and cost, documents and chunks per cancer and per source, and `corpus_stats()`. The stored corpus (2,151 documents, 9,543 chunks after the quality filter; `corpus_stats()` about 205 MB including not-yet-vacuumed space) is under the free plan's 500 MB.

## Tests

```
pytest                # offline, synthetic images, no GPU needed
pytest -m hf -s       # opt-in smoke eval on a Hugging Face dataset (needs network)
pytest -m jev -s      # opt-in live Jev triage eval (needs JEV_KEY and network)
pytest -m corpus -s   # opt-in live ingest smoke test (needs the corpus keys and the real chunk plan)
pytest -m check -s    # opt-in live claim checker tests: search, Luna, and 3 claims through POST /check (about a cent; CHECK_ENV overrides the .env path)
```

`pytest -m corpus -s` deletes the first 3 documents of the real (quality-filtered) plan, re-ingests them with `--limit 3` and checks their stored chunk hashes against the plan, then checks a second run embeds nothing. It never uses `--rebuild`, so it is safe after a full ingest. `CORPUS_DIR` and `CORPUS_ENV` override the corpus folder and the `.env` path.

### Evals

`pytest -m jev -s` calls the real Jev API (about 9 requests for the samples plus about 370 for Cancer-Myth, a few cents) and writes `reports/jev_triage_samples.md` and `reports/jev_triage_cancer_myth.md` (gitignored):

- **Samples vs gold:** the 9 sample transcripts go through `/transcripts/import` and the real `/triage`, and each span is compared with the gold labels in `evals/triage/keys/`. It asserts claim recall >= 0.95 and transcription recall >= 0.8.
- **Cancer-Myth** (`Cancer-Myth/Cancer-Myth`, CC-BY-4.0): each myth statement is triaged alone. It asserts that >= 95% of them are claims. The rows are cached in `.cache/cancer_myth/`.

Without `JEV_KEY` both tests skip. `JEV_EVAL_CM_STEP=3` sends every 3rd Cancer-Myth statement only. Latest results and the threshold decision are in [`evals/triage/README.md`](evals/triage/README.md).
