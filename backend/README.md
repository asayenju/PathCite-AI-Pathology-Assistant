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
| 429 | `triage_rate_limited` | Jev still rate-limits after 2 retries |
| 500 | `internal_error` | unexpected error (no details are returned) |
| 502 | `triage_upstream_error` | Jev timed out, returned a 5xx, or sent a malformed answer |
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

## Privacy
Uploaded images and transcripts are processed in memory only: never written to disk (uploads are not spooled to temp files) and never logged. Unexpected errors log only the exception type. `/triage` sends doctor turn text to TypeSafe, which has no BAA, so use synthetic or de-identified text only. The text, Jev's requests and answers, and `JEV_KEY` are never logged or stored.

## Tests

```
pytest                # offline, synthetic images, no GPU needed
pytest -m hf -s       # opt-in smoke eval on a Hugging Face dataset (needs network)
```
