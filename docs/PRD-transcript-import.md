# PRD: Transcript Import

## What
`POST /transcripts/import` takes one speech-to-text JSON file, detects which tool produced it (Deepgram, AWS Transcribe / Transcribe Medical, WhisperX, OpenAI diarized transcription), and returns a normalized conversation:

```
{ source, language, duration_s,
  speakers: [{ id, source_label, word_count, talk_time_s, preview: [str], suggested_role }],
  turns:    [{ id, speaker, start, end, text, is_final, words: [{ text, start, end, confidence }], min_confidence }],
  warnings: [str], notice }
```

A new frontend view lets the user drop the file, check the turns, assign each speaker a role (doctor, patient or other), and confirm. The confirmed transcript stays in browser memory. It is the input for the future fact checker.

## Why
- Every later step (claim extraction, retrieval, verdicts) needs clean doctor turns. If roles are wrong, the checker fact-checks the patient.
- Speech-to-text tools disagree on shape: word lists vs utterances, numbers stored as strings (AWS), punctuation as separate items (AWS), missing word timestamps (WhisperX), speakers vs channels. Normalizing once keeps vendor quirks out of the fact checker.
- No tool labels speakers as doctor or patient. Only a human can confirm that reliably.
- Low-confidence words have to survive the import. Later, the checker must tell "the doctor was wrong" apart from "the transcription was wrong."

## How
**Backend** (`backend/app/transcripts/`):
- `detect.py` sniffs the format from the JSON content, never from the filename:
  - Deepgram: `results.channels`
  - AWS: `results.items` plus `jobName` or `results.transcripts`
  - WhisperX: `segments[*].speaker` or `word_segments`
  - OpenAI diarized: `segments[*].type == "transcript.text.segment"`
  - Plain Whisper (`segments` with `avg_logprob`/`seek` and no speaker): rejected.
- One parser per vendor in `parsers/`. Each outputs a shared `Segment(speaker_label, start, end, text, words, is_final)` stream.
  - **Deepgram:** prefer `results.utterances`. Otherwise group `alternatives[0].words` by `speaker`, using `punctuated_word` when present. Multichannel: label `ch{n}`, or `ch{n}:{speaker}` when also diarized.
  - **AWS:** prefer `audio_segments`. Otherwise use `items` with `speaker_label`. Older outputs only have `speaker_labels.segments`, so match items to segments by time. `channel_labels` maps to `ch_{n}`. Numbers are parsed from strings. Punctuation items attach to the previous word. Call Analytics output (`Transcript[].ParticipantRole`) and HealthScribe are rejected as unsupported.
  - **WhisperX:** words may lack `start`/`end`/`speaker`, so they inherit from their neighbours or the segment. A segment without a speaker gets the label `unknown`. Confidence comes from `score`.
  - **OpenAI diarized:** segments with `speaker` ("A", "B"). No word-level detail, so confidence is null. Field names must be checked against the current docs.
- `normalize.py`: a `TurnBuilder` with `append(segments)` and `finish()`. It is incremental so a future streaming endpoint can reuse it.
  - Segments are sorted by start time. A new turn starts when the speaker changes; consecutive segments from the same speaker merge.
  - Turn IDs are stable (`t0001…`). Speaker IDs are stable (`S1…`, in order of first appearance).
  - Text keeps its original wording, casing and filler words; only Unicode NFC normalization is applied. Later fact-check citations point at exact spans.
  - `min_confidence` per turn. Words below `asr_low_confidence` (default 0.6) are reported.
- `roles.py`: `suggested_role` comes from a small bundled medical vocabulary. The speaker with the most medical-term hits is suggested as doctor, the other as patient. On a tie, or with more than 2 speakers and no clear winner, the suggestion is `null`.
- `router.py`: the endpoint reads the upload with `images.read_upload` under a new size cap, `max_transcript_mb` (default 20). Parsing runs in a threadpool.
- `main.py`:
  - A **missing** weights file no longer stops startup. It logs a warning, sets model to None, and `/predict` returns its existing 503 `model_unavailable`.
  - A **mismatched** weights file still fails fast.
  - `/health` returns 200 with `status: ok|degraded` and `model_loaded`.
  - `spool_max_size` uses the larger of the two upload limits.

**Errors** (always `{detail, code}`):

| Status | Code | When |
|---|---|---|
| 413 | `file_too_large` | Over the size cap |
| 422 | `empty_file` | Empty upload |
| 422 | `invalid_json` | Not UTF-8 or not JSON |
| 422 | `unsupported_transcript_format` | Unrecognized format |
| 422 | `undiarized_transcript` | Plain Whisper |
| 422 | `empty_transcript` | No words |

**Warnings** (the import still succeeds):
- Language detected as something other than English.
- A speaker labelled `unknown`.
- Only one speaker found.
- More than 4 speakers.
- Overlapping speech.
- More than 20% of words are low-confidence.

**Privacy**, same rules as images:
- Parsed in memory only. Never written to disk, never logged.
- Nothing is echoed back from vendor metadata: `jobName`, `accountId`, S3 URIs and `request_id` are dropped. AWS job names often contain patient names.

**Frontend:**
- A top-level switch between "Slides" and "Transcripts" (no router).
- `lib/transcripts.ts`: the API client, a client-side size and `.json` check, and new codes added to `messageFor`.
- `composables/useTranscriptImport.ts`: the state machine `idle|loading|review|confirmed|error`.
- `TranscriptDrop.vue`: drag-and-drop or file picker.
- `SpeakerRoles.vue`: one row per speaker with preview lines, talk time and a role select pre-filled from `suggested_role`.
- `TranscriptView.vue`: turns coloured by role, timestamps, low-confidence words underlined with their score in a tooltip.
- Confirm is disabled until at least one speaker is the doctor. Several doctors are allowed (resident plus attending). Roles are applied client-side.
- The confirmed transcript lives only in memory: no localStorage. Clear resets everything.

## Non-goals
Fact-checking, RAG, LLM calls, audio upload or transcription, the streaming/WebSocket endpoint, plain Whisper, SRT/VTT/plain text, AWS Call Analytics and HealthScribe, several files per visit, translation, persistence, auth.

## Testing
- Fixtures in `backend/tests/fixtures/transcripts/` are synthetic: `generate.py` renders one fictional colorectal "biopsy results" dialogue into each vendor's documented JSON shape (deterministic; `test_transcript_fixtures.py` fails if they go stale). See the fixtures README for the expected result per file. They encode our reading of the docs, not real vendor output.
- Edge-case fixtures:
  - AWS with only `speaker_labels.segments`
  - AWS channel identification
  - Deepgram multichannel
  - Deepgram with words only (no utterances)
  - WhisperX words without timestamps
  - Plain Whisper
  - Call Analytics
  - Non-English
  - Single speaker
  - Empty transcript
  - Truncated JSON
  - Oversized file
- Run with `pytest` (backend) and `npm test` (frontend).

## Issues

**1. Make model loading optional; health reports degraded**
- [ ] With no weights file, the app starts, logs a warning, `/health` returns 200 `{status:"degraded", model_loaded:false}`, and `/predict` returns 503 `model_unavailable`.
- [ ] A mismatched weights file still stops startup (existing test passes).
- [ ] `/health` returns `status:"ok"` when the model is loaded. Existing tests are updated and CLAUDE.md is corrected.

**2. Normalized transcript schema and streaming-ready TurnBuilder**
- [ ] Pydantic models for `TranscriptImport`, `Speaker`, `Turn`, `Word`, plus an internal `Segment`.
- [ ] `TurnBuilder.append()` called in several chunks gives exactly the same output as one call (tested).
- [ ] Adjacent same-speaker segments merge. A speaker change starts a new turn. Out-of-order segments are sorted by start time.
- [ ] Turn and speaker IDs are deterministic. Text is kept apart from NFC. `is_final` is always true for file imports.
- [ ] `min_confidence` is null when the vendor gives no confidences.

**3. Format detection and import errors**
- [ ] Each supported fixture is detected correctly, whatever its filename or content type.
- [ ] Plain Whisper returns 422 `undiarized_transcript`. Call Analytics and HealthScribe return 422 `unsupported_transcript_format`.
- [ ] Empty, non-UTF-8, truncated and wordless files return the matching codes from the table.
- [ ] A file over `max_transcript_mb` returns 413 without being fully buffered.

**4. Deepgram parser**
- [ ] Utterances are used when present. Otherwise words are grouped by speaker, preferring `punctuated_word`.
- [ ] Multichannel fixture: one speaker per channel, labelled `ch{n}` or `ch{n}:{speaker}`.
- [ ] A detected language other than English adds a warning.

**5. AWS Transcribe / Transcribe Medical parser**
- [ ] String timings and confidences are parsed as floats. Punctuation attaches to the previous word with no extra space.
- [ ] `audio_segments`, item-level `speaker_label`, and the older `speaker_labels.segments` layout all give the same turns for the same conversation.
- [ ] `channel_labels` fixture maps to channel speakers.
- [ ] `jobName`, `accountId` and S3 URIs never appear in the response (asserted).

**6. WhisperX and OpenAI diarized parsers**
- [ ] WhisperX words missing timestamps or speaker inherit sensible values. A segment without a speaker gives an `unknown` speaker plus a warning.
- [ ] `score` maps to confidence.
- [ ] OpenAI diarized segments map speakers "A"/"B". Confidence is null.

**7. Speaker summaries and suggested roles**
- [ ] Each speaker has `word_count`, `talk_time_s`, and up to 2 preview lines of at most 120 characters each.
- [ ] Two-speaker fixture: the clinician is suggested as doctor and the other speaker as patient.
- [ ] A tie, one speaker, or an unclear result with more than 2 speakers gives `null` suggestions. Warnings for one speaker and for more than 4 speakers.

**8. `POST /transcripts/import` endpoint and privacy**
- [ ] Multipart `file` upload. Parsing runs in a threadpool. Response matches the schema and includes the research-use `notice`.
- [ ] Works while the model is not loaded.
- [ ] Upload is not spooled to disk (test like the existing spool test). Logs from a failing parse contain no transcript text (caplog test).
- [ ] Shown in OpenAPI docs. `backend/README.md` error table updated.

**9. Frontend: Transcripts view, upload, API client**
- [ ] The Slides/Transcripts switch keeps the existing slide flow unchanged (existing tests pass).
- [ ] Dropping or picking a `.json` file calls the endpoint. Non-JSON or oversized files are refused client-side with the mapped message.
- [ ] Every new backend error code has a `messageFor` entry (tested). The network failure message is reused.
- [ ] State machine tests cover idle → loading → review / error, and clearing.

**10. Frontend: speaker role assignment and transcript review**
- [ ] Each speaker row shows preview lines, talk time, and a role select pre-filled from `suggested_role`.
- [ ] Confirm is disabled until at least one speaker is the doctor. Several doctors are allowed.
- [ ] Turns are shown coloured by role with timestamps. Low-confidence words are underlined with a tooltip score. Warnings are shown.
- [ ] Nothing goes to localStorage or sessionStorage (tested). Clear resets everything.
- [ ] `npm run type-check` and `npm test` pass.

**11. (Backlog) Validate parsers against real vendor output**
Synthetic fixtures exist (`backend/tests/fixtures/transcripts/generate.py`) and cover every format and edge case above. This issue checks them against reality.
- [ ] Run at least one short TTS-voiced synthetic dialogue through each of Deepgram, AWS Transcribe Medical and WhisperX (and OpenAI diarized if used). Commit the outputs with provenance notes.
- [ ] Any field where real output differs from the synthetic shape is fixed in the generator and the parsers, and all tests pass.
