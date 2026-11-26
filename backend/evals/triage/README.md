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
`low_confidence`, e.g. "sigmoid" 0.52). That term should not be flagged. ("sigmoid" itself only occurs in
the crc transcription-error sentence, so the eval scores the 19 non-error spans holding the other 8 terms.)

## Running the eval

```
pytest -m jev -s      # from backend/, needs JEV_KEY (env or repo-root .env) and network
```

`tests/test_jev_eval.py` has two tests (deselected by default, skipped without a key):

- **Samples vs gold.** Each sample is imported through `POST /transcripts/import`. Its doctor turns (from the
  speaker suggested as doctor) go through the real `POST /triage` with the configured thresholds. The spans are
  matched with these keys by `(doctor_turn_index, start, end)`. Writes `backend/reports/jev_triage_samples.md`:
  the confusion matrix, misses per sample, the correct low-confidence terms, a threshold sweep and every span.
  It asserts claim recall >= 0.95 and transcription recall >= 0.8.
- **Cancer-Myth** (`Cancer-Myth/Cancer-Myth` on Hugging Face, CC-BY-4.0, split `validation`, 585 rows, cached in
  `backend/.cache/cancer_myth/`). Every `source_myth` value is a myth stated as a general fact, except the 2
  "From physicians." rows, which are excluded. Each of the 367 distinct statements is sent alone as one span
  (`ask_asr` on), and the results are scored per row (583). Writes `backend/reports/jev_triage_cancer_myth.md`
  with recall by `category`, the misses, the 10 statements closest to skip, and a sweep. It asserts that
  >= 95% of rows are claims. `JEV_EVAL_CM_STEP=N` sends every Nth distinct statement only;
  `JEV_EVAL_DELAY_S` (default 0.2) is the pause between requests.

**Claim recall rule:** a gold claim counts as caught when it is predicted `claim`, or `transcription` with
`base_kind` `claim`. The doctor's "Yes, that's correct" then sends it to the checker. A `transcription` with
`base_kind` `skip` counts as a miss.

The sweeps are replayed from the probabilities already collected, so they cost no extra calls.

## Latest results (2026-11-25, `jev-1.14.0`, skip_min 0.8, asr_flag 0.5)

These numbers come from the synthetic, unreviewed labels above, and there are only 107 spans. Read them as a
smoke test, not as a benchmark.

Samples, full run (107 gold spans: 64 skip, 34 claim, 9 transcription):

| gold \ pred | skip | claim | transcription |
|---|---:|---:|---:|
| skip | 59 | 5 | 0 |
| claim | 0 | 34 | 0 |
| transcription | 1 | 0 | 8 |

- Claim recall: 34/34 (100%)
- Transcription recall: 8/9 (89%)
- Skip precision: 59/60 (98%)
- Skip recall: 59/64 (92%)
- False transcription flags: 0/98
- Correct low-confidence terms flagged: 0/19 spans

Jev is not deterministic, so the samples were run 5 times on 2026-11-25:

- Claim recall: 34/34 every time.
- Transcription recall: 9/9 in 4 runs, 8/9 in 1. The miss is "prostrate" for "prostate" in `prostate_gg2`, a
  real-word homophone in the OpenAI format, which has no word confidences. Its `p_asr` was 0.54, 0.47, 0.51,
  0.57 and 0.58. Every other planted error scored 0.90 to 0.98.
- False flags: 0 in every run. The highest `p_asr` on a correct span was 0.35 to 0.41. It was always one of
  the factually wrong keyed claims, "The Breslow thickness ... how wide the spot was." or "Ulcerative colitis
  mostly affects your small intestine.". Jev's ASR question partly reacts to wrong facts, not only
  mishearing.
- The 5 skip-to-claim misses are short replies or patient-specific results with p_claim 0.23 to 0.87:
  - "Good, that's normal."
  - "Nobody does."
  - "That's normal."
  - "It sits in the middle of the scale."
  - "The biopsies show chronic active colitis, and the pattern fits ulcerative colitis."

  They fail safe: they cost a needless check, not a missed claim.
- The lowest p_claim on a gold claim was 0.36 to 0.41, on the Breslow sentence (P(small talk) <= 0.64).

Cancer-Myth: 583/583 rows (367/367 distinct statements) were claims, with no misses in any category. None
was flagged as transcription; the highest `p_asr` was 0.14. The statements closest to skip are first-person,
patient-voiced myths. Their p_claim is 0.42 to 0.64:
- "If my colon cancer has spread, I probably won't live very long..."
- "I can't have children now that I've had cervical cancer."
- "I don't need to get screened because I don't have any symptoms."
- "I am too young to be diagnosed with cancer."

## Threshold decision (2026-11-25): defaults kept

**`TRIAGE_SKIP_MIN` stays at 0.8.**

The samples columns give the range over the 5 runs.

| skip_min | samples claim recall | samples skip recall | Cancer-Myth recall |
|---:|---:|---:|---:|
| 0.5 | 32-33/34 | 63/64 | 577/583 |
| 0.6 | 33-34/34 | 61-62/64 | 583/583 |
| 0.7 | 34/34 | 61/64 | 583/583 |
| **0.8** | **34/34** | **59/64** | **583/583** |
| 0.9 | 34/34 | 57-58/64 | 583/583 |

- 0.7 would save 2 needless checks out of 64 small-talk spans. But the margin to the nearest real claims would
  shrink to about 0.06, from P(small talk) 0.58 on Cancer-Myth and 0.64 on the samples.
- 0.6 already missed a claim in 1 of the 5 runs, and 0.5 misses claims in every run.
- Claim recall comes first, so 0.8 keeps a margin of about 0.16 for a small cost.

**`TRIAGE_ASR_FLAG` stays at 0.5.**

| asr_flag | transcription recall (5 runs) | false flags (5 runs) |
|---:|---|---|
| 0.4 | 9/9 every run | 0, 0, 0, 0, 1 |
| 0.45 | 9/9 every run | 0 |
| **0.5** | **9/9 in 4 runs, 8/9 in 1** | **0** |
| 0.6 | 8/9 | 0 |

- 0.45 separates all 5 runs. But it rests on one borderline example ("prostrate"), with about 0.04 of margin
  on each side, which is too little evidence to move a default on 107 spans.
- 0.4 already produced a false flag.
- A missed transcription error is not a missed claim: the sentence is still triaged on its own. When the base
  sentence is small talk (as for "prostrate"), nothing reaches the checker either way.
- Revisit with more planted errors, especially real-word homophones in formats without confidences.

**No wording change.** There were no claim misses, so the `jev.py` criteria were left as they are.
