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

## Hard cases (`hard_cases.jsonl`, issue #57)

**Synthetic and AI-written**, like the keys above: invented doctor turns, not from any real consultation, not
reviewed by a pathologist. 76 rows that target where triage goes wrong, one JSON object per line:

```json
{"id": "cr-nobody-does", "category": "context_reply",
 "previous_doctor_turns": ["Once an adenoma has been removed completely, there's no need for another colonoscopy."],
 "turn": "Nobody does. Keep up the fiber and we'll see you in clinic.", "span": "Nobody does.",
 "label": "skip", "note": "Patient: 'I really didn't enjoy the prep.' ..."}
```

- `previous_doctor_turns` (up to 2) and `turn` are exactly what `/triage` would send for that turn. The patient's
  line is never sent (privacy rule), so `note` says what the patient said, for the reader only.
- `span` is one `split_spans` span of `turn`; only that span is scored.
- Categories and labels:

| category | label | rows | what it tests |
|---|---|---:|---|
| `context_reply` | skip | 15 | short replies whose meaning is in the patient's line ("Nobody does.", "Exactly.", "Not always."), often after a medically loaded doctor turn |
| `patient_specific` | skip | 12 | results or guesses about this patient, with medical words ("Yours is three plus four, which makes it a seven.") |
| `plan_as_fact` | skip | 12 | this patient's plan phrased like a fact ("We usually repeat the scan in a year.") |
| `hedged_fact` | claim | 12 | general facts with hedges ("As far as we know, most prostate cancers aren't inherited.") |
| `short_fact` | claim | 13 | short real general claims ("Melanoma can spread.", "HER2 is a protein.") that a short-span rule must keep |
| `fact_in_reply` | claim | 12 | a general fact said in reply to the patient ("No, PSA naturally goes up a little as men get older.") |

`tests/test_triage_hard_cases.py` checks the schema, the category balance, that each span is one triage span of its
turn, and that no gold claim is skipped by the local rule. Edit the JSONL directly (no generator).

## Running the eval

```
pytest -m jev -s      # from backend/, needs JEV_KEY (env or repo-root .env) and network
```

`tests/test_jev_eval.py` has three tests (deselected by default, skipped without a key):

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
- **Hard cases** (`test_hard_cases`). Each row of `hard_cases.jsonl` goes through `app.triage.router._triage_turn`,
  the exact per-turn path of `POST /triage`: `split_spans` with the local skips, one `classify_spans` call with the
  previous doctor turns, and `decide` with the configured thresholds. The event for the row's span is scored. The 6
  checker-test transcripts (`frontend/public/transcripts/checker-tests/`) are scored in the same run, through the
  real `POST /triage`, against `checker_tests.py`'s `expect` map: `claim:*` is claim, `transcription` is a
  transcription with base kind claim, and unlisted doctor sentences are skip. Writes
  `backend/reports/jev_triage_hard_cases.md`: per-category accuracy, claim recall and precision, skip precision and
  recall, misses with `p_claim` across runs, and a threshold sweep. It asserts claim recall >= 0.95 on both.

**Variants.** `JEV_EVAL_VARIANT=baseline|rule|criteria|rule+criteria` runs the same tests with the short-reply rule
on or off and the `KIND_CRITERIA` context notes on or off, without code edits (default `production`: the code as
shipped). Every run saves its rows to `backend/reports/jev_triage_runs/<variant>/`, and
`backend/reports/jev_triage_variants.md` gives mean (range) over all saved runs per variant. Delete that folder to
start over. Example: `JEV_EVAL_VARIANT=baseline pytest -m jev -k "hard_cases or samples" -s`.

**Claim recall rule:** a gold claim counts as caught when it is predicted `claim`, or `transcription` with
`base_kind` `claim`. The doctor's "Yes, that's correct" then sends it to the checker. A `transcription` with
`base_kind` `skip` counts as a miss.

The sweeps are replayed from the probabilities already collected, so they cost no extra calls.

## Latest results (2026-11-27, issue #57, `jev-1.14.0`, skip_min 0.8, asr_flag 0.5)

Variants: `baseline` (code before #57), `rule` (short-reply local skip), `criteria` (context notes in
`KIND_CRITERIA`), `rule+criteria` (**shipped**). Each variant was run 3 times on the hard cases, the checker tests
and the samples. The figures are the mean, with the range in brackets when the runs differed. Jev's probabilities
varied between runs (11 to 28 of 76 hard-case rows changed), but no hard-case decision flipped, so the hard-case
ranges are zero.

| set / metric | baseline | rule | criteria | **rule+criteria** |
|---|---:|---:|---:|---:|
| hard: claim recall (37) | 100% | 100% | 100% | **100%** |
| hard: skip recall (39) | 61.5% | 82.1% | 74.4% | **82.1%** |
| hard: claim precision | 71.2% | 84.1% | 78.7% | **84.1%** |
| hard: skip precision | 100% | 100% | 100% | **100%** |
| hard: context_reply (15) | 33.3% | 86.7% | 66.7% | **86.7%** |
| hard: patient_specific (12) | 91.7% | 91.7% | 91.7% | **91.7%** |
| hard: plan_as_fact (12) | 66.7% | 66.7% | 66.7% | **66.7%** |
| hard: hedged / short / in-reply facts (12/13/12) | 100% | 100% | 100% | **100%** |
| checker tests (66 spans): claim recall, skip recall, transcription recall | 100% | 100% | 100% | **100%** |
| samples (107): claim recall (34) | 100% | 100% | 100% | **100%** |
| samples: skip recall (64) | 92.7% (92.2-93.8) | 96.9% | 96.9% | **96.9%** |
| samples: skip precision | 98.9% (98.3-100) | 99.5% (98.4-100) | 98.9% (98.4-100) | **100%** |
| samples: claim precision | 88.5% (87.8-90.0) | 94.7% | 94.7% | **94.7%** |
| samples: transcription recall (9) | 92.6% (88.9-100) | 96.3% (88.9-100) | 92.6% (88.9-100) | **100%** |
| Cancer-Myth claim recall (583 rows) | 100% (2026-11-25, 5 runs) | not run | not run | **100% (1 run)** |

- **Skip precision was already 100% on the hard set.** Every miss was a skip predicted as a claim, never the other
  way round. So the gain shows up as skip recall and claim precision: fewer needless checks.
- **The rule** fixes 12 of the 15 context replies deterministically, including "Nobody does." from
  `tubular_adenoma` (baseline p_claim 0.85). It leaves 3 to Jev: "It is for some people." (5 words),
  "That's the idea." and "That's a myth." ("idea" and "myth" aren't in its vocabulary). Jev skips "That's the
  idea." but calls the other 2 claims (p_claim 0.46-0.63). On the samples it removes the 3 short-reply misses ("Nobody does.", "That's normal.",
  "Good, that's normal.").
- **The criteria notes** alone fix 5 of the context replies that the rule also fixes. They lower no claim's
  `p_claim` below the margin: the lowest gold claim on the samples went from 0.38-0.39 to 0.45-0.47 (the Breslow
  sentence), and Cancer-Myth stayed at 583/583 (lowest p_claim 0.45). On the hard set they add nothing on top of
  the rule. Both are shipped: the notes cover replies the rule can't see, at no measured cost to claim recall.
- **Not fixed** (left for later): plans phrased as practice ("We usually repeat the scan in a year.", "We don't
  usually do a CT scan at this stage.", p_claim 0.56-0.89) and the Gleason arithmetic on this patient's score.
  They fail safe to claim. The remaining sample misses are "It sits in the middle of the scale." and the
  ulcerative colitis biopsy result.
- **Caveat:** the rule's vocabulary was written together with this hard set, so its 12/15 is optimistic. The guard
  is the 37 short and in-reply claims, which all stay claims. `test_no_gold_claim_is_skipped_by_the_local_rule`
  checks this offline.
- Jev calls: 2,683 in total (hard 76 per run, checker tests 47, samples 70, Cancer-Myth 367).

## Earlier results (2026-11-25, before issue #57, `jev-1.14.0`, skip_min 0.8, asr_flag 0.5)

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
