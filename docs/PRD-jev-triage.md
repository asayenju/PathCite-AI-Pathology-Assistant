# PRD: Jev triage of doctor speech

## What
Step 1 of the real fact checker. When **Review** runs, each doctor turn is split into sentences (spans), and [Jev](https://docs.typesafe.ai/models) (TypeSafe AI's decision model) sorts each span into one of three kinds:

| Kind | Example | What the UI does |
|---|---|---|
| **Small talk** | "Good morning, please have a seat." "We'll do a CT next week." | Skipped: no highlight, no card. |
| **Possible transcription error** | "The growth in your sigmoid colin…" | Dashed highlight and a card: "Possible transcription error. Is this correct?" with **Yes** / **No**. |
| **Medical claim** | "Because it's moderately differentiated, that tells us it hasn't spread." | Blue highlight and a card: "Claim: checking…". The span goes to the checker, which is a stub for now ("Not fact-checked yet"). |

This replaces the hand-written mock as the Review source for every transcript, uploads included. The mock in `src/mocks/review/` stays as test data.

## Why
- The checker (retrieval and verdicts, a later PRD) is the expensive part. Only claims should reach it, and a greeting should never get a "verdict".
- The transcript import PRD already says the checker must tell "the doctor was wrong" apart from "the transcription was wrong". Asking the doctor "Is this correct?" settles that without the audio.
- Jev returns typed, calibrated decisions (`choice` + `probabilities`, `noul` = probability of yes), not text. That suits a three-way routing decision, is cheap (about $0.042 per million input tokens, output free), and can't make up wording.
- **Claims are general medical facts only, for now.** Plans and recommendations ("we'll do a CT to stage it") are skipped, because there is no dataset to check a doctor's suggestion against. They can be added once there is one.

## How
**Jev cannot split text.** It only answers questions about the input it's given. So spans are cut by our own deterministic code, and Jev labels each span.

**Backend** (`backend/app/triage/`):
- **Config** (`app/config.py`, read from the repo-root `.env`, which is gitignored; `.env.example` lists the variables):
  - `jev_key` from `JEV_KEY` (secret, optional)
  - `jev_model = "jev-1.14.0"`, pinned rather than `jev-latest` so results don't change under us
  - `jev_timeout_s = 15`
  - `triage_asr_flag = 0.5`
  - `triage_skip_min = 0.8`
  - `triage_max_turns = 500`, `triage_max_turn_chars = 5000`

  Without a key, `/triage` returns 503 `triage_unavailable` and the rest of the app works as before.
- **`segment.py`**: `split_spans(text) -> [(start, end)]`. A pure function returning character offsets into the NFC turn text.
  - Spans cover the turn without overlapping, and are trimmed.
  - It splits on `.?!` followed by whitespace. It does not split after abbreviations (Dr., Mr., Mrs., Ms., e.g., i.e., vs., approx.), inside decimals (`1.2 mm`), after ellipses or after initials.
  - Unpunctuated runs over 40 words (common in WhisperX output) are cut at the nearest comma or conjunction, or at 40 words as a last resort.
  - Spans with no letters, or made only of fillers (`um`, `okay`, `mm-hmm`), are skipped locally and never sent to Jev.
- **`jev.py`**: a thin `httpx` client for `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer`.
  - It retries `429` at most twice, following `retry-after`, then raises `JevRateLimited`.
  - A timeout or 5xx raises `JevUnavailable`. A 401 maps to `triage_unavailable`.
  - Request bodies, responses and the key are never logged.
- **One Jev request per doctor turn:**
  - `state`: `{ previous_doctor_turns: [the last 2 doctor turns], turn, spans: [{id, text, low_confidence_words: [{word, confidence}]}] }`. Only doctor text is sent: no patient turns, speaker ids, file names or vendor metadata.
  - Each span gets two questions:
    - `kind_<i>`, a **Choice** between `small_talk` and `medical_claim`. The option descriptions define the line:
      - **Claim:** a general medical fact that the literature could confirm or refute. This includes a general fact used to justify a conclusion about the patient, a hedged general fact ("this usually means…"), and a myth that is mentioned in order to correct it.
      - **Small talk:** greetings, empathy, questions to the patient, scheduling and logistics, plans and recommendations, and a patient-specific result with no general assertion ("your biopsy shows…").
    - `asr_<i>`, a **Noul**: "Does this span contain a word speech-to-text likely got wrong: a non-word, a misspelled or out-of-place medical term, or a homophone that makes no sense here?" Word confidences are a hint, not the deciding factor.

    A span can be both a claim and garbled, which is why there are two questions rather than one three-way choice.
  - Turns with more than 40 spans are split across several requests.
- **`decide.py`** (pure function):
  - `p_asr ≥ triage_asr_flag` → `transcription`. The span also keeps `base_kind`, which is used if the doctor answers "Yes".
  - Otherwise `P(small_talk) ≥ triage_skip_min` → `skip`.
  - Otherwise → `claim`.

  **When unsure, a span becomes a claim.** A missed claim is a falsehood that never gets checked, while a needless check costs little. This also limits what prompt injection in transcript text can do: Jev only returns labels, so the worst case is a wrong label.
- **`router.py`**:
  - `POST /triage`
    - Body: `{turns: [{id, text, words?: [{text, confidence}]}]}` (doctor turns only).
    - Streams **NDJSON** events, in this order per turn:
      1. `{type: "turn_started", turn_id}`
      2. one `{type: "span", turn_id, start, end, quote, kind: "skip"|"claim"|"transcription", base_kind, p_claim, p_asr}` per span
      3. `{type: "turn_done", turn_id}`

      After the last turn: `{type: "done", model}`.
    - Turns run one after another, and the stream stops when the client disconnects.
  - `POST /triage/span`
    - Body: `{text, previous_doctor_turns}`. Returns `{kind, p_claim}` (Choice only).
    - Used for a doctor's typed correction.
  - Both endpoints are registered in `main.py` and work without the image model.

**Errors.** Before the stream starts they are the usual `{detail, code}`. Once the stream has started, an error arrives as `{type: "error", code}`.

| Status | Code | When |
|---|---|---|
| 503 | `triage_unavailable` | No key configured, or Jev rejected the key |
| 429 | `triage_rate_limited` | Jev still rate-limits after 2 retries |
| 502 | `triage_upstream_error` | Jev timed out, returned a 5xx, or sent a malformed answer |
| 422 | `invalid_triage_request` | Empty body, too many turns, or a turn over the character cap |

**Frontend:**
- **Contract** (`src/types.ts`):
  - `ReviewVerdict` gains `'claim' | 'transcription'`. Skipped spans produce no comment.
  - Comments arrive with `start`/`end` already set, so a sentence repeated within a turn is placed by its offsets, not found by its quote.
  - New `ClaimChecker = (claim: {turn_id, quote, context}, signal) => Promise<CheckResult>`. A **stub** returns `not_checked`. The RAG PRD replaces it.
- **`lib/jevReviewSource.ts`**:
  - A `ReviewSource` that sends only `role === 'doctor'` turns, with word confidences when available, to `/api/triage`.
  - It parses the NDJSON into `ReviewEvent`s, and aborting cancels the fetch.
  - The new error codes are added to `lib/api.ts::messageFor`.
- **`useTranscriptReview.ts`**:
  - A claim has a check state, `checking → not_checked`, run through the `ClaimChecker`.
  - A transcription card has `confirm()` and `correct(text)`:
    - **Yes** turns the span into its `base_kind`. A claim goes to the checker; small talk is resolved.
    - **No** opens a text box. The typed text goes to `/api/triage/span`. If it's a claim, the card shows "Checked as: '…'" and goes to the checker; otherwise it is resolved.
    - The transcript text is never edited.
  - Disagree stays, because "this isn't a claim" is useful eval data. Reply is hidden on triage cards, because Jev can't write answers.
  - The export adds `kind`, `p_claim`, `p_asr`, the Yes/No answer, the correction text and the Jev `model`.
- **UI**:
  - Claim: hematoxylin-blue highlight.
  - Transcription: dashed underline with a "?" icon. Submitting an empty correction is blocked.
  - The header counts claims, open transcription checks, and skipped spans.
  - The label becomes "Triage by Jev (TypeSafe). Claims are not fact-checked yet. Doctor turns are sent to TypeSafe."
  - Review is enabled for every sample and for uploads.

**Sample transcripts** (`backend/tests/fixtures/transcripts/scenarios.py`, then regenerate):
- **Facts only.** Each keyed plan span (e.g. "We'll also test the tumor for mismatch repair proteins", keyed incomplete) is rewritten as a general-fact sentence that keeps the intended error, or loses its key. Every scenario still has ≥1 accurate, ≥1 incomplete and ≥1 inaccurate key.
- **Planted transcription errors.** One per scenario, in a doctor line, with the `intended` word, and low confidence wherever the vendor format has confidences. Examples: "colin" for colon, "breast low" for Breslow, "glee son" for Gleason. OpenAI diarized has no confidences, so those samples test text-only detection.
- **Triage gold labels.** Every doctor sentence gets `skip | claim | transcription`. Keyed spans are claims, and unkeyed fact sentences such as "Staging tells us how deep the tumor goes…" are listed as claims explicitly. The labels are written to `backend/evals/triage/keys/<id>.json`, outside `public/`.
- The three mock files' quotes are updated so the mock tests still pass. The "synthetic, needs pathologist review" notice stays.

## Edge cases
- **Turns with nothing to send:** empty turns, filler-only turns, and turns where every span is skipped locally make no Jev call but still emit `turn_started` and `turn_done`.
- **Roles:**
  - No doctor assigned: Review stays disabled.
  - Several doctors: all doctor turns are triaged.
  - `role: null` turns are never sent.
- **Splitting:** decimals, abbreviations, quotes, ellipses and unpunctuated WhisperX runs. A sentence repeated within one turn is placed by offset.
- **Low confidence:** a correct medical term with low confidence ("sigmoid" at 0.52) should not be flagged. The eval reports how often it is.
- **Borderline wording:**
  - Doctor questions are small talk.
  - Rhetorical questions that assert a fact are claims.
  - A myth stated in order to correct it is a claim.
  - A hedged general fact is a claim. A hedged patient-specific guess ("it's probably early") is skipped.
- **Failures and interruptions:**
  - A Jev 429, timeout or 5xx mid-review keeps the spans already received, sets status `error`, and Retry works.
  - Stop, Clear, a new import or a role change aborts the review, and the backend sees the disconnect.
- **Bad input and privacy:**
  - A missing or invalid key returns `triage_unavailable`, and oversized input returns 422.
  - The key and transcript text never appear in logs or on disk.
  - Non-English transcripts keep the existing warning.

## Non-goals
PubMed/Supabase retrieval and real verdicts (next PRD), checking plans or recommendations, editing the transcript, generated suggestions or replies, triaging patient turns, audio, auth, and a BAA with TypeSafe (its docs don't mention HIPAA, so keep uploads to synthetic or de-identified text).

## Testing
**Offline** (`pytest`, `npm test`):
- Unit tests:
  - `split_spans`: abbreviations, decimals, ellipses, quotes, no punctuation, filler, offsets round-trip.
  - `decide`: thresholds, and the fail-safe that turns an unsure span into a claim.
  - `jev.py` against `httpx.MockTransport`: request shape, auth header, 429 retry with `retry-after`, timeout, 401, a malformed answer.
- `/triage` with a fake Jev:
  - event order
  - turns that are skipped locally
  - disconnect stops the run
  - caps return 422
  - no key returns 503
  - a mid-stream error
  - no transcript text in the logs (caplog)
- Frontend:
  - NDJSON parsing across chunk boundaries, and abort.
  - The composable's confirm / correct / checker flow.
  - The claim and transcription cards.
  - Reply hidden on triage cards.
  - Uploads enabled.
  - The new label.
  - No web storage used.
- `npm run type-check` and `npm run build` pass.

**Opt-in, needs network and key** (`pytest -m jev`; a new marker excluded by `addopts`, like `hf`):
1. **Samples against the gold labels.** Writes a confusion matrix to `reports/`. Asserts claim recall ≥ 0.95 and transcription recall ≥ 0.8. Also reports skip precision and how often correct low-confidence terms are falsely flagged. `triage_skip_min` and `triage_asr_flag` are tuned from these numbers.
2. **[Cancer-Myth](https://hf.co/datasets/Cancer-Myth/Cancer-Myth)** (585 expert-verified false cancer beliefs, CC-BY-4.0).
   - Each `source_myth` statement is triaged as a single span; rows that only say "From physicians." are skipped.
   - Asserts claim recall ≥ 0.95, and the misses are listed in the report.
   - The dataset is cached in `backend/.cache/` and never committed. Its expert corrections are kept for the checker PRD.

**Manual:** put `JEV_KEY` in the root `.env`, then run uvicorn and `npm run dev`. Review the CRC sample and check:
- the greeting isn't highlighted
- the "moderately differentiated… hasn't spread" sentence is a claim
- the "colin" line asks "Is this correct?"
- Yes leads to Checking…
- No → "colon" leads to a claim

Then repeat with an uploaded transcript.

## Issues (label `jev-triage`; dependencies: 1 → 2 → {3, 4} → 5 → 6)
**1. Config, span splitting, decision rule and Jev client**
- [ ] `.env` is gitignored, and the settings above exist. A missing key leaves the app running.
- [ ] `split_spans` and `decide` are pure functions with the unit tests above.
- [ ] `jev.py` sends the documented request (checked once against the live API with the real key), and its retry, timeout and error mapping are tested offline.

**2. `/triage` and `/triage/span` endpoints**
- [ ] NDJSON events in the documented order, one turn at a time, stopping on disconnect.
- [ ] The error table is implemented, with errors before the stream as JSON and errors during it as events.
- [ ] Caps are enforced, and nothing is logged beyond the exception type.

**3. Sample transcripts: facts only, transcription errors, gold labels**
- [ ] Plan spans are rewritten or unkeyed, and every scenario still has all three verdicts.
- [ ] One planted transcription error per scenario, in a doctor line.
- [ ] `backend/evals/triage/keys/*.json` is generated, and every label's span is found in a doctor turn.
- [ ] The samples are regenerated, the staleness test passes, and the mock quotes are updated.

**4. Frontend contract, Jev source and checker stub**
- [ ] `ReviewVerdict` is extended, and `ClaimChecker` has a stub.
- [ ] `jevReviewSource` streams events and can be aborted, and the new error codes have messages.
- [ ] The composable's confirm and correct flows, the check state, and the export fields.

**5. Triage cards and header**
- [ ] Claim and transcription highlights and cards, the Yes/No/correction flow, Reply hidden, Disagree kept.
- [ ] Header counts, the new label and notice, and Review enabled for uploads.
- [ ] Every control can be used from the keyboard. The existing review, import and sample tests pass.

**6. Evals, tuning and docs**
- [ ] `pytest -m jev` runs both evals and writes the reports, and the thresholds are set from the results.
- [ ] `backend/README.md`, `frontend/README.md` and `CLAUDE.md` are updated.
