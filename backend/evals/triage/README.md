# Triage gold labels

`keys/<sample_id>.json` holds the expected Jev triage label for every doctor sentence of one sample
conversation in `frontend/public/transcripts/` (docs/PRD-jev-triage.md, "Sample transcripts"). The
`pytest -m jev` eval scores `/triage` against them. They live here, outside `frontend/public/`, so
the UI never ships the answers.

**Synthetic and AI-written.** The conversations and labels were written by an AI and have not been
checked by a pathologist. Don't use them to score anything until a pathologist has reviewed them,
together with the fact-check keys in `../factcheck/` (issue #22).

Reviewed by: (pending)

## Editing

Don't edit the JSON by hand. The source is `backend/tests/fixtures/transcripts/scenarios.py`
(each scenario's keys, `claims` and `asr_error`). Change it there, then run
`python tests/fixtures/transcripts/generate.py` from `backend/`. `tests/test_transcript_samples.py`
fails if these files are stale, and `tests/test_triage_gold.py` checks that they cover every
doctor sentence.

## Format

```json
{
  "notice": "Synthetic and AI-written triage gold labels. ...",
  "sample_id": "crc_adenocarcinoma",
  "file": "transcripts/gi/crc_adenocarcinoma.json",
  "spans": [
    {"doctor_turn_index": 2, "start": 0, "end": 54, "quote": "The growth in your sigmoid colin is an adenocarcinoma.",
     "label": "transcription", "base_kind": "skip", "wrong": "colin", "intended": "colon",
     "intended_quote": "The growth in your sigmoid colon is an adenocarcinoma."},
    {"doctor_turn_index": 2, "start": 55, "end": 119, "quote": "That's a cancer that starts in the gland cells lining the colon.",
     "label": "claim"}
  ]
}
```

- The spans are exactly what `/triage` sends to Jev. The sample is imported (`POST /transcripts/import`),
  and its doctor turns are cut with `app.triage.segment.split_spans`. Filler-only spans ("Okay.",
  "Yes.") are skipped locally and get no label. Every other span gets exactly one label, in turn order.
- `doctor_turn_index`: 0-based index among the imported doctor turns. Turn ids can shift when
  same-speaker lines merge, so match on `quote` first. `start`/`end` are character offsets into that
  turn's text, which tell a repeated sentence apart.
- `label`:
  - `claim`: a general medical fact that the literature could confirm or refute. That includes:
    - a general fact used to justify a conclusion about the patient ("Because it's moderately
      differentiated, that tells us it hasn't spread.")
    - a hedged general fact ("this usually means...")
    - a myth mentioned in order to correct it

    Every sentence holding a fact-check key span (`../factcheck/keys/`) is a claim. So is every
    unkeyed general fact listed in the scenario's `claims`.
  - `skip` (small talk) includes:
    - greetings, empathy, questions to the patient, scheduling and logistics
    - plans and recommendations ("We'll still do a CT scan...")
    - a patient-specific result with no general assertion ("The growth in your sigmoid colon is an
      adenocarcinoma.")
  - `transcription`: the sentence holds the scenario's one planted speech-to-text error. Extra fields:
    - `base_kind`: what the sentence would be with the error fixed (`claim` or `skip`). This is
      what a doctor's "Yes, that's correct" resolves to.
    - `wrong`: the misheard text.
    - `intended`: the word(s) the doctor said.
    - `intended_quote`: the corrected sentence.

## Planted transcription errors

There is one per sample, always in a doctor sentence without a fact-check key.

Where the vendor format carries word confidences (Deepgram, AWS, WhisperX), only the planted words
get a low score, at that one spot. OpenAI diarized has no confidences, so `breast_idc` and
`prostate_gg2` test text-only detection.

Each sample also keeps one correctly transcribed medical term with a low score (its
`low_confidence`, e.g. "sigmoid" 0.52). That term should not be flagged.
