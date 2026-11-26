# PRD: Sample Pathology Transcripts

## What
A "Sample transcripts" panel in the Transcripts tab, like the slide folders in the Slides tab. Folders by organ system hold synthetic doctor–patient conversations about pathology results. You click a sample, or drag it onto the drop zone, and it goes through the same `/transcripts/import` path as an uploaded file.

Each sample deliberately mixes accurate, incomplete and inaccurate doctor statements. A hidden answer key records which is which, and that key becomes the first test set for the RAG fact checker.

| Folder | Scenario | Vendor format |
|---|---|---|
| GI | Colorectal adenocarcinoma results | Deepgram |
| GI | Benign tubular adenoma (polyp) | AWS Transcribe Medical |
| GI | Ulcerative colitis biopsy | WhisperX |
| Breast | Invasive ductal carcinoma, ER+/PR+/HER2− | OpenAI diarized |
| Breast | Fibroadenoma | Deepgram |
| Skin & blood | Melanoma, Breslow 1.2 mm | AWS Transcribe Medical |
| Skin & blood | Diffuse large B-cell lymphoma (lymph node) | WhisperX |
| Prostate & thyroid | Prostate biopsy, Gleason 3+4=7 (Grade Group 2) | OpenAI diarized |
| Prostate & thyroid | Thyroid FNA, Bethesda IV | Deepgram |

The formats rotate so the samples exercise all four parsers.

## Why
- **Demos:** today you need a transcript file to see the Transcripts tab do anything. Samples make it one click, like the slides.
- **A test set before the checker exists:** the fact checker can't be judged without conversations whose right answers are known. Writing the errors and the key now means the checker is built against a fixed target instead of whatever it happens to output.
- **Breadth:** colorectal-only samples would let the checker overfit to one protocol. Nine scenarios across organ systems test retrieval on CAP protocols, staging, grading and receptor status.

## How
**Scenario scripts** go in `backend/tests/fixtures/transcripts/scenarios.py`. Each scenario has:
- id, folder, title and vendor
- its lines: `(speaker, text, key?)`

Only doctor lines carry a `key`:

```
{ span: exact substring of the line, verdict: "accurate"|"incomplete"|"inaccurate",
  issue: what is wrong or missing (null when accurate),
  correct: the fact a good answer states, source_hint: e.g. "CAP colon protocol", "AJCC 8th melanoma" }
```

- Keys point at a quoted `span`, not at a turn id. Turn ids change when consecutive same-speaker lines merge, and the future checker reports spans anyway.
- A doctor line without a key (greetings, scheduling) counts as `not_checkable`.
- Keys sit on general medical facts only, never on plans, recommendations or patient-specific results with no general assertion (docs/PRD-jev-triage.md). Those are triaged as small talk and never reach the checker. Since #35, sentences that used to be plans ("we'll take out half of the thyroid") are worded as general facts ("For a Bethesda 4 nodule, the usual next step is surgery to take out half of the thyroid."). Unkeyed doctor sentences that are general facts are listed in the scenario's `claims`.
- Each scenario has at least one accurate, one incomplete and one inaccurate key. Each inaccurate key repeats a common real misconception:
  - crc_adenocarcinoma: grade mistaken for stage ("Because it's moderately differentiated, that tells us it hasn't spread.")
  - tubular_adenoma: no more colonoscopies once an adenoma is removed
  - ulcerative_colitis: ulcerative colitis "mostly affects your small intestine"
  - breast_idc: HER2-negative mistaken for "hormone-blocking pills won't work"
  - fibroadenoma: fibroadenomas "usually turn into cancer"
  - melanoma: Breslow thickness described as width rather than depth
  - dlbcl: lymphoma "only lives in the lymph nodes"
  - prostate_gg2: Gleason 7 described as "the most aggressive kind there is"
  - thyroid_bethesda4: a follicular neoplasm (Bethesda IV) on a needle test described as "it's cancer"
- Each scenario plants exactly one speech-to-text error in a doctor sentence without a key (`asr_error`, e.g. "colin" for "colon", "prostrate" for "prostate", "Breast low" for "Breslow"). In formats with word confidences only the planted words get a low score; the two OpenAI diarized samples (`breast_idc`, `prostate_gg2`) have no confidences, so they test text-only detection.
- Each scenario also keeps one correctly transcribed medical term with a low score everywhere it appears (`low_confidence`, e.g. "sigmoid" at 0.52), which should not be flagged as a transcription error.
- Patient lines include realistic questions and fillers.

**Generation:** `generate.py` renders each script with the existing vendor renderers (deterministic):
- Transcript JSON → `frontend/public/transcripts/<folder-slug>/<id>.json`
- Manifest → `frontend/public/transcripts/index.json`: `[{folder, items: [{id, title, vendor, file}]}]`
- Answer key → `backend/evals/factcheck/keys/<id>.json`. It is kept out of `public/`, so the UI never shows or ships the answers.
- Triage gold labels → `backend/evals/triage/keys/<id>.json` (`skip | claim | transcription` per doctor sentence; scored by `pytest -m jev`, see `backend/evals/triage/README.md`). Also kept out of `public/`.

**Frontend:**
- A `TranscriptFolders.vue` panel beside the drop zone, in the same style as `SlideFolders.vue`: folders expand to show titled samples with a small vendor badge.
- Click a sample, or drag it with the custom MIME type `application/x-pathcite-transcript` (payload = file path). Either way it is fetched as a Blob, wrapped in a `File`, and passed to the same `readFile`, so it is validated and imported exactly like an upload.
- Dropping a sample replaces the current transcript, as slide drops do.
- The UI marks samples "Synthetic sample".

**Disclaimer:** the scenarios and answer keys are synthetic and AI-written. They need review by a pathologist before being used to score anything (issue 3). The UI and the key files say so.

## Non-goals
Running the fact checker, showing answer-key verdicts in the UI, audio, more than one transcript per scenario, non-English samples.

## Testing
- **Backend:** every sample imports through `service.import_transcript` with the doctor suggested correctly. Every key `span` appears verbatim, exactly once, in a doctor turn of the imported transcript. Every scenario has all three verdicts. The generator output is up to date (the existing staleness test pattern).
- **Frontend:** the manifest lists 9 samples in 4 folders and every file exists and parses. Clicking and dragging a sample both call the import with the sample's bytes, and a sample drop replaces the current transcript. No web storage is used.

## Issues
**1. Scenario scripts, answer keys and generator output**
- [ ] 9 scenarios in `scenarios.py`, each with ≥1 accurate, ≥1 incomplete and ≥1 inaccurate keyed doctor statement, each key with `correct` and `source_hint`.
- [ ] `generate.py` writes the transcripts, `index.json` and the answer keys. Re-running it changes nothing, and the staleness test covers the new outputs.
- [ ] Every sample imports through the service: 2 speakers, doctor suggested, no errors.
- [ ] Every key span is found exactly once in a doctor turn after import.
- [ ] The key files carry a "synthetic, AI-written, needs pathologist review" notice. `backend/evals/factcheck/README.md` explains the key format.

**2. Frontend sample folders**
- [ ] `TranscriptFolders.vue` lists the folders and samples from `index.json`. If the manifest can't load, the panel is hidden and the rest of the tab is unaffected.
- [ ] Clicking or dragging a sample imports it through the existing `readFile` path. A sample drop replaces the current transcript. Keyboard users can select a sample.
- [ ] Samples are labelled synthetic. The tests cover the manifest, click, drag and replace, and confirm no storage is used.
- [ ] `npm test`, `npm run type-check` and `npm run build` pass.

**3. (Human) Pathologist review of answer keys**
- [ ] A pathologist checks every key's `verdict`, `issue` and `correct` text, and the review date and reviewer role are recorded in the evals README.
- [ ] Corrections are made in `scenarios.py` and regenerated.
