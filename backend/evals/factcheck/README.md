# Fact-check answer keys

`keys/<id>.json` is the answer key for one sample conversation in
`frontend/public/transcripts/`. Together they are the first test set for the RAG fact checker
(docs/PRD-transcript-samples.md). The keys live here, outside `frontend/public/`, so the UI
never ships the answers.

**Synthetic and AI-written.** The conversations and keys were written by an AI and have not
been checked by a pathologist. They must not be used to score anything until a pathologist
has reviewed every key (issue #22).

Reviewed by: (pending)

## Editing

Don't edit the JSON by hand. The source is `backend/tests/fixtures/transcripts/scenarios.py`;
change it there and run `python tests/fixtures/transcripts/generate.py` from `backend/`.
`tests/test_transcript_samples.py` fails if these files are stale.

## Format

```json
{
  "id": "melanoma", "title": "...", "folder": "Skin & blood", "vendor": "aws_medical",
  "notice": "Synthetic and AI-written. ...",
  "keys": [
    {"span": "...", "verdict": "accurate|incomplete|inaccurate", "issue": "... or null",
     "correct": "...", "source_hint": "...", "speaker": "doctor"}
  ]
}
```

- `span`: an exact, case-sensitive substring of one doctor turn **after import** through
  `POST /transcripts/import`. It occurs exactly once in the whole conversation. Keys point at
  spans rather than turn ids because turn ids shift when consecutive same-speaker lines merge.
- `verdict`:
  - `accurate`: correct as stated. `issue` is null.
  - `incomplete`: not wrong, but leaves out something a patient needs (the why, the plan, the
    alternatives). `issue` says what is missing.
  - `inaccurate`: wrong. `issue` says what is wrong. Several repeat common misconceptions
    (grade read as stage, HER2-negative read as "no hormone therapy", Breslow as width,
    Gleason 7 as "most aggressive", Bethesda IV as "it's cancer").
  - Doctor statements with no key (greetings, scheduling, small talk) are `not_checkable`.
    Patient lines are never keyed.
- `correct`: the fact a good answer states.
- `source_hint`: where a checker should find support, e.g. "CAP cutaneous melanoma protocol",
  "NCCN prostate cancer". A pointer, not a citation.
- `speaker`: always `doctor`.
