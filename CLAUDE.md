# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Research-use web app that serves a fine-tuned timm `resnet18` (`model/resnet18_finetuned_v2.pt`) classifying 224×224 colorectal H&E patches into 9 NCT-CRC tissue classes. Not a medical device; the UI and API both carry that disclaimer, keep it.

- `backend/`: FastAPI (`GET /health`, `POST /predict`). See `backend/README.md` for the API and error table.
- `frontend/`: Vue 3 + TS + Tailwind v4 + shadcn-vue. See `frontend/README.md`.
- `docs/PRD.md`: backend PRD.
- `docs/PRD-transcript-import.md`: doctor–patient transcript import (step 1 of the RAG fact checker; issues labelled `transcript-import`).
- `docs/PRD-transcript-review-ui.md`: agent review UI (highlights, Docs-style comments, resolve/disagree/reply) on a hand-written mock agent; issues labelled `transcript-review`.
- `docs/PRD-jev-triage.md`: Jev (TypeSafe decision model) triage of doctor sentences into small talk / possible transcription error / medical claim, feeding a stubbed checker; issues labelled `jev-triage`.
- `docs/PRD-corpus-ingest.md`: chunk, embed (OpenAI `text-embedding-3-small`) and store the cancer evidence corpus (NICE/PubMed/WikiDoc guidelines + PubMed abstracts) in Supabase pgvector for the claim checker; issues labelled `corpus-ingest`.
- `docs/PRD-rag-checker.md`: claim checker. Hybrid search (`match_chunks` RPC) over the corpus, then GPT-6 Luna and Jev each judge every retrieved source (supports/contradicts/irrelevant) and code rules give the verdict (mixed sources → `disputed`, never a majority vote); issues labelled `rag-checker`.

## Commands

Backend (from `backend/`, venv in `.venv`):
```
uvicorn app.main:app --reload              # http://localhost:8000/docs
pytest                                      # offline suite (hf tests excluded by addopts)
pytest tests/test_images.py::test_name      # single test
pytest -m hf -s                             # opt-in network smoke eval on Hugging Face data
pytest -m jev -s                            # opt-in live Jev triage eval (needs JEV_KEY in repo-root .env)
pytest -m corpus -s                         # opt-in live ingest smoke test (re-ingests 3 docs into Supabase)
pytest -m check -s                          # opt-in live claim checker tests (CHECK_ENV overrides the .env path)
python scripts/fetch_cancer_corpus.py       # download the corpus to .cache/corpus/ (gitignored)
python scripts/ingest_corpus.py --dry-run   # chunk plan report, no API calls
python scripts/ingest_corpus.py [--limit N] [--prune] [--rebuild] [--rechunk] [--env PATH] [--corpus-dir PATH]
```

Frontend (from `frontend/`):
```
npm run dev          # :5173, proxies /api/* to localhost:8000 (prefix stripped; backend has no CORS)
npm test             # vitest; single file: npx vitest run src/lib/api.test.ts
npm run type-check
npm run build        # type-check + production build
```

## Architecture notes

- **Class order is a contract.** `backend/app/classes.py::CLASS_NAMES` (ADI, BACK, DEB, LYM, MUC, MUS, NORM, STR, TUM = indices 0–8) must match `frontend/src/lib/classes.ts`; a frontend test parses the Python file to enforce it. The order was verified against the dataset, so don't reorder.
- **Backend request path:** `main.py` (lifespan loads the model and computes `model_version` as `name@sha256[:8]`; a missing weights file runs the app degraded with `/predict` 503, a mismatched one still fails startup) → `images.read_upload`/`load_image` (format sniffed from content, pixel cap, EXIF, first TIFF frame, alpha flattened to white, RGB) → `predict.predict_probs` in a threadpool → `build_response`. Preprocessing must stay `Resize((224,224))` + ImageNet normalize to match training (`test_preprocessing_parity.py`).
- **Errors** are always `{detail, code}` (`errors.py`); the frontend `lib/api.ts::messageFor` maps on `code`, and maps network failures to a "start uvicorn" message.
- **Privacy:** uploads are never written to disk or logged. The lifespan raises `MultiPartParser.spool_max_size` above the upload limit so Starlette doesn't spool to temp files (there's a test); unexpected errors log only the exception type.
- **`uncertain`** is decided server-side (`confidence < UNCERTAIN_THRESHOLD`, default 0.7). The frontend only draws the 0.70 tick; colour/state follows the response flag.
- **Frontend flow:** `composables/usePredict.ts` is the state machine (`idle|loading|result|error`). Sample slides are dragged (custom MIME `application/x-pathcite-slide`, payload = unique file path) or clicked from `SlideFolders` onto `Stage`, fetched as a Blob and sent through the same `/predict` path as a real upload. While the stage is occupied, file drops are refused (clear first); slide drops replace.
- **Transcript review** (`docs/PRD-transcript-review-ui.md`, `docs/PRD-jev-triage.md`, `docs/PRD-rag-checker.md`): once a speaker is the doctor, `TranscriptDrop` runs `composables/useTranscriptReview.ts` with `lib/jevReviewSource.ts` for every transcript (samples and uploads). The backend `POST /triage` (`app/triage/`) splits doctor turns into sentences (`segment.split_spans`; Jev can't split text), asks Jev (TypeSafe decision model, `JEV_KEY` in the repo-root `.env`, gitignored) a small-talk/claim choice plus a transcription-error yes/no per sentence, and `decide` maps that to skip / claim / transcription, failing safe to claim. It streams NDJSON. Claims go to the RAG `ClaimChecker` (`lib/claimChecker.ts::httpClaimChecker`, `POST /api/check`, at most 4 in flight, queued checks abort without starting; `stubClaimChecker` is for tests only), which answers `{status: "checked", verdict, issue, suggestion, needs_review, review_reasons, sources, model, jev_model}`. The comment keeps verdict `claim` and stores the answer in `check` (`checkStatus: "checked"`); the card shows the check verdict (accurate / inaccurate / incomplete / unverified "Not verified — the corpus doesn't cover this" / disputed "Sources disagree"), issue, suggestion, a "Needs your review" banner with the reasons, and sources grouped Supports / Contradicts / Unclear (irrelevant collapsed; links http(s) only, new tab), and export includes `check`. A stopped check ends `not_checked`; errors map through `messageFor` (`check_*` codes). Transcription cards ask "Is this correct?" (Yes keeps the triage label, No re-triages the typed text via `/triage/span`; the transcript is never edited). Only doctor-turn text is sent: to TypeSafe for triage, and for each claim the claim text plus up to 2 previous doctor turns go to OpenAI (embedding and verdict), TypeSafe (Jev stances) and Supabase (search text); nothing is logged or stored. Keep the label "Triage by Jev (TypeSafe). Claims are checked against NICE/PubMed/WikiDoc with OpenAI and Jev." (`lib/review.ts::JEV_REVIEW_LABEL`). Claims are general medical facts only (plans and patient-specific results are skipped). The hand-written mock in `src/mocks/review/` is test data only. Clear, a new import and Edit roles (with a `window.confirm` if there are comments) discard the review. Export is a client-side JSON download. Jev comment ids contain a colon (`t0005:12`), so use `getElementById`/attribute selectors, not `#comment-…`.
- **Review DOM contract:** `TranscriptView` puts `data-anchor-for="<id>"` on the first run of each highlight and `aria-describedby="comment-<id>"` on the highlights. `ReviewMargin` finds anchors by that attribute inside `TranscriptReviewLayout`'s single scroll container, and cards have `id="comment-<id>"`. Don't rename these without updating both sides.
- **Bundled samples:** `frontend/public/samples/<CODE>/<row>.jpg` (72 = 8 per class) listed in `samples.json`; the folder is the true label. They're JPEG renderings from NCT-CRC-HE-100K (CC-BY-4.0, 0.5 µm/px, hence the 25 µm scale bar on bundled slides only). Tests assert 8 per class and JPEG magic bytes.
- **Evidence corpus** (`backend/app/corpus/`, `docs/PRD-corpus-ingest.md`, offline script, not part of the API): load + document dedupe → structural split at markdown headings, boilerplate sections dropped → our own semantic breakpoint chunking (numpy over `sentences.split_sentences`, not Chonkie, so "e.g."/"Fig. 3"/"1.2 mm" are never cut; 10–512 tokens) → lexical chunk dedupe (exact + MinHash ≥ 0.95, with a negation/number guard; never by embedding) → plan cache `chunks.jsonl` (reused unless `--rechunk`, because OpenAI embeddings aren't deterministic) → quality filter (`quality.py`, applied to the loaded plan, own `quality_filter_hash` in `corpus_meta`, never part of the chunker hash; drops Sandbox pages, NICE economic-modelling sections by heading, research questions, cross-reference stubs, wiki navigation, garbled tables; `--prune` deletes stored documents it emptied) → OpenAI `text-embedding-3-small` (1536) → Supabase via PostgREST (`app/corpus/supabase.py`, `ingest_document` RPC, one transaction per document). Resume skips documents whose stored hashes match the plan; `corpus_meta` locks model/dims/chunker config (mismatch refused without `--rebuild`). RLS is on with no policies: only `SUPABASE_SECRET_KEY` (backend scripts only, `apikey` header, never logged) reads it. The API server's `/check` now reads it too (still backend only); claims are embedded with OpenAI for search, which the review label states.
- **Claim checker** (`app/check/`, `docs/PRD-rag-checker.md`, `POST /check`): `search.EvidenceSearch` (embed + `match_chunks`, one retry for a cold-start JSON 5xx) → `verify.luna_stances` (GPT-6 Luna, groundedness guard) and `jev_check.judge_sources` (one Jev Choice per passage via `JevClient.ask`) concurrently → `aggregate.aggregate` (pure rules: a passage counts only when both models agree and Jev p ≥ `check_jev_min`; any supports+contradicts mix is `disputed`, never a majority vote). The `review_reasons` strings `sources_disagree` / `models_disagree` / `jev_unavailable` are a frontend contract. No `JEV_KEY` or a Jev failure is not fatal (Luna alone + `jev_unavailable`); search/OpenAI errors map to `check_unavailable` / `check_rate_limited` / `check_upstream_error`. Clients come from `app.state.check_*_factory` (tests swap in fakes). Never log the claim or context.

## Gotchas

- The model has no "not tissue" class and its softmax is uncalibrated. The current weights are v2 (colour jitter + label smoothing; the earlier baseline is in git history). Notebook results: 95.9% on half of the external NCT-CRC test set, 94% on the 72 bundled JPEG samples, but only 65% on the Kather `dpdl-benchmark` set (domain shift in stain colour and tile scale; the baseline got 15% there, and ~8% in the old API smoke test). A temperature fitted on in-domain data makes v2 overconfident on Kather, so none is applied. Don't present confidence as probability of correctness; the UI calls it "model score".
- Plain `shadcn-vue@latest` crashes on Node 20; components in `src/components/ui/` were added with CLI 2.4.3. TS 6: no `baseUrl`, use `paths` only.
- `backend/.cache/` (HF smoke images), `.venv/`, `reports/` are gitignored.
- Not built yet: Azure storage, auth, batch upload, dark mode, confidence calibration / OOD detection.
