# Testing the claim checker by hand (issue #54)

The **Checker test cases** sample folder holds six short synthetic conversations, one per cancer in the evidence corpus. Across the set, the doctor's sentences hit every claim-checker outcome, plus the small talk and plans that triage must skip and a planted speech-to-text error that must give a transcription card.

Synthetic, AI-written conversations. Not real patients. Not medical advice. Research use only.

- **Source:** `backend/tests/fixtures/transcripts/checker_tests.py` (dialogue + the expected card per sentence). Regenerate with `python tests/fixtures/transcripts/generate.py` from `backend/`.
- **Claims:** most paraphrase rows of `backend/evals/search/golden.jsonl` (the row id is given), so their evidence is known to be in the corpus.
- **Formats:** Deepgram (colorectal, prostate), OpenAI diarized (breast), AWS Transcribe Medical (melanoma), WhisperX (DLBCL, thyroid). OpenAI diarized has no word confidences; the others mark the planted error with a low word score.

## How to run

1. Backend: `uvicorn app.main:app --reload` from `backend/` (needs `JEV_KEY`, `OPEN_AI_KEY`, `OPEN_AI_VECTOR_KEY` and the Supabase keys in the repo-root `.env`).
2. Frontend: `npm run dev` from `frontend/`, open http://localhost:5173.
3. In the transcript panel open **Checker test cases**, pick a sample, and confirm speaker 1 as the **Doctor**. The review starts on its own.
4. Each claim takes 8–20 s to check. Supabase's free tier is slow: a `check_upstream_error` card means the keyword search timed out; try again later.

## Status of the expected results

Model outputs (Jev, Luna) vary between runs. Each row says where its result comes from:

- **observed** — seen in a live run on 2026-11-27 (the numbers are from that run).
- **expected (not verified live)** — live checking was stopped partway through because Supabase was too slow. The expectation comes from the golden-set label (`supported` → accurate, `contradicted` → inaccurate, `not_covered` → unverified) or from the reasoning given in the note.

Triage (skip / claim / transcription) was observed live for every sentence of all six samples. A claim row's **Triage** column refers to that; its **Check** column is separate.

Abbreviations: *Jev* = Jev's confidence (`confidence.label p`), *reasons* = `review_reasons`, *top source* = the first supporting or contradicting source.

---

## 1. Colorectal cancer: checker test (`check_colorectal`, Deepgram)

| Doctor sentence | Expected card | Triage | Check (live result) |
|---|---|---|---|
| Hi, come on in and have a seat. / How have you been since the colonoscopy? | skipped (greeting) | observed skip | — |
| That's completely normal. | skipped (empathy) | observed skip | — |
| Your biopsy shows a colorectal cancer, an adenocarcinoma in the sigmoid colon. | skipped (patient-specific result) | observed skip | — |
| We'll book a CT scan of your chest, abdomen and pelvis for next week to stage it. | skipped (plan) | observed skip | — |
| Stage three bowel cancer means it has spread to the liver. *(crc-14)* | claim → **Inaccurate** | observed claim | **observed:** inaccurate, Jev contradicted 0.89, no review. Top source: JSCCR guidelines 2019 for colorectal cancer (high). Issue: liver metastasis is stage IV, not III. |
| We don't know yet, the scan will tell us. | skipped | observed skip | — |
| Colon cancers are tested for mismatch repair proteins. | claim → **Incomplete** (MSI testing is the alternative) | observed claim | **observed:** incomplete, Jev supported 0.99, no review. Top source: NICE "Molecular testing strategies for Lynch syndrome in people with colorectal cancer" (high). Issue: testing may use MSI rather than the MMR proteins. |
| The mismatch **repeat** proteins fix mistakes when a cell copies its DNA. | **transcription** ("repeat" for "repair", word score 0.38) | observed transcription (p_asr 0.84) | — (only checked after you answer the card) |
| Eating a lot of spicy food is one of the causes of bowel cancer. *(nc-04)* | claim → **Not verified** | observed claim | **observed:** unverified, Jev insufficient 1.00, no review. All 8 passages not relevant. |
| If bowel cancer does come back, it's usually within the first three years after the operation, so that's when we watch most closely. *(crc-10)* | claim → **Accurate** | observed claim | **observed:** accurate, Jev supported 0.99, no review. Top source: JSCCR Guidelines 2014 for colorectal cancer (high). |
| You're welcome. / I know it's a lot to take in. | skipped | observed skip | — |

## 2. Breast cancer: checker test (`check_breast`, OpenAI diarized)

| Doctor sentence | Expected card | Triage | Check (live result) |
|---|---|---|---|
| Hello, thanks for coming in. / Is it okay if your husband stays for this? / Of course. | skipped | observed skip | — |
| Your biopsy shows an invasive breast cancer that is estrogen receptor positive and HER2 negative. | skipped (patient-specific result) | observed skip | — |
| Breast cancers that have hormone receptors generally have a better outlook than ones that don't. *(brc-03)* | claim → **Accurate** (golden: supported) | observed claim | **observed: Sources disagree**, reasons `sources_disagree`, Jev leans supported 0.99. 1 supports (NICE lapatinib/trastuzumab appraisal), 1 contradicts (low ER expression can do as badly as triple negative). **Varies:** a second `disputed` case, not the accurate one; see the note below. |
| HER2-positive breast cancers tend to be the slower-growing kind, so they usually do better than HER2-negative ones. *(brc-02)* | claim → **Inaccurate** | observed claim | **observed:** inaccurate, Jev contradicted 0.99, no review. Top source: "Updated UK Recommendations for HER2 assessment in breast cancer" (high). |
| Because it's HER2 negative, hormone-blocking pills won't work for you. | claim → **Inaccurate + Needs your review** (`low_confidence`) | observed claim (p_claim 0.81–0.85) | **observed:** inaccurate, Jev contradicted **0.54** → `low_confidence`. Top source: NICE lapatinib/trastuzumab + aromatase inhibitor appraisal (high). Jev's p was 0.62 in the #51 smoke run, so it sits near the 0.7 line: it could pass without review on some runs. |
| Tamoxifen is usually taken for five years, and staying on it for ten years doesn't lower the chance of the cancer coming back. | claim → **Sources disagree** (`sources_disagree`) | observed claim | **observed:** disputed, `sources_disagree`, Jev leans contradicted 0.84. 1 contradicts (NICE "Early and locally advanced breast cancer: diagnosis and management": extended tamoxifen lowers recurrence), 1 supports. Could come back **inaccurate** instead if Luna reads the supporting passage as irrelevant. |
| Wearing an underwire bra can raise your risk of breast cancer. *(nc-01)* | claim → **Not verified** | observed claim | **observed:** unverified, Jev insufficient 1.00, no review. |
| We'll set up a meeting with the breast surgeon next week to plan the operation. | skipped (plan) | observed skip | — |

No transcription error is planted here: the OpenAI diarized format has no word confidences.

## 3. Melanoma: checker test (`check_melanoma`, AWS Transcribe Medical)

| Doctor sentence | Expected card | Triage | Check (live result) |
|---|---|---|---|
| Hi, good to see you again. / How has the wound on your back been healing? | skipped | observed skip | — |
| Glad to hear it. | skipped | expected (not verified live) | — |
| The mole we removed was a melanoma, and the report gives a few measurements. | skipped (patient-specific) | observed skip | — |
| The Breslow number is basically how wide the spot was across the skin, so a small one is reassuring. *(mel-02)* | claim → **Inaccurate** | observed claim | **observed:** inaccurate, Jev contradicted 0.91, no review. Top source: "Data Set for Pathology Reporting of Cutaneous Invasive Melanoma" (high). |
| **Breast low** thickness is the main measurement pathologists use to stage melanoma. | **transcription** ("Breast low" for "Breslow", 0.39) | observed transcription (p_asr 0.95–0.96) | — |
| Sentinel lymph node biopsy is recommended for melanomas thicker than one millimetre. *(mel-05)* | claim → **Accurate** | observed claim | expected (not verified live) |
| We'll book you in for a wider excision and the sentinel node biopsy next month. | skipped (plan) | observed skip | — |
| Regular coffee drinking is linked to a lower risk of melanoma. *(nc-06)* | claim → **Not verified** | observed claim | expected (not verified live) |
| The mitotic rate is used to decide the T stage of thin melanomas. | claim → **Sources disagree** (AJCC 7th used mitoses for T1b; AJCC 8th dropped it) | observed claim | expected (not verified live). If the corpus only returns AJCC 8th-era text this gives **inaccurate**. |
| You're welcome. / Call us if anything changes with the scar. | skipped | observed skip | — |

In an earlier version of this sample, "That's normal." (a reply to "it's just a bit itchy") was triaged as a claim (p_claim 0.28–0.37) and checked as unverified. It was reworded to "Glad to hear it." That wording has not been re-run.

## 4. Diffuse large B-cell lymphoma: checker test (`check_dlbcl`, WhisperX)

| Doctor sentence | Expected card | Triage | Check (live result) |
|---|---|---|---|
| Hello, please come in. / Is your daughter joining us today? / Good. | skipped | observed skip | — |
| The biopsy of the lump in your neck shows a diffuse large B-cell lymphoma. | skipped (patient-specific; also gives later checks their DLBCL context) | observed skip | — |
| Diffuse large B-cell lymphoma is the most common type of non-Hodgkin lymphoma in adults, and it mostly turns up in older people. *(lym-03)* | claim → **Accurate** | observed claim | expected (not verified live) |
| This kind of lymphoma usually shows up as a painful, tender lump. *(lym-04)* | claim → **Inaccurate** | observed claim | expected (not verified live) |
| This lymphoma can be cured in about two out of every three people. *(lym-09)* | claim → **Accurate** | observed claim | expected (not verified live). Context ("diffuse large B-cell lymphoma" in the turns before) is what tells search which lymphoma. |
| Large **B sell** lymphoma is usually treated with a combination called R-CHOP. | **transcription** ("B sell" for "B-cell", 0.37) | observed transcription (p_asr 0.98) | — |
| Flying on a plane isn't safe while you're having R-CHOP chemotherapy. *(nc-08)* | claim → **Not verified** | observed claim | expected (not verified live) |
| We'll start the first cycle in two weeks, after a PET-CT scan. | skipped (plan) | observed skip | — |
| Take care, and call the ward straight away if you get a fever. | skipped | observed skip | — |

## 5. Prostate cancer: checker test (`check_prostate`, Deepgram)

| Doctor sentence | Expected card | Triage | Check (live result) |
|---|---|---|---|
| Hi, thanks for coming in. / Did your wife come with you today? / Okay. | skipped | observed skip | — |
| Your biopsy shows prostate cancer, Gleason three plus four, so a score of seven. | skipped (patient-specific) | observed skip | — |
| Three plus four and four plus three both add up to seven, so they behave exactly the same. *(pro-02)* | claim → **Inaccurate** | observed claim | expected (not verified live) |
| A **glisten** score is made by adding together the two most common patterns seen in the tumour. *(pro-01)* | **transcription** ("glisten" for "Gleason", 0.36). Answer **No** and type the Gleason wording to re-triage it as a claim; checking it should then give **Accurate**. | observed transcription (p_asr 0.97) | expected (not verified live) |
| Active surveillance should be offered for low-risk localised prostate cancer. *(pro-04)* | claim → **Accurate** | observed claim | expected (not verified live) |
| Men whose brothers or fathers had prostate cancer are more likely to get it themselves. *(pro-09)* | claim → **Accurate** | observed claim | expected (not verified live) |
| Prostate cancer can be passed on to a partner through sex. *(nc-09)* | claim → **Not verified** | observed claim | expected (not verified live) |
| PSA screening reduces the number of men who die from prostate cancer. | claim → **Sources disagree** (screening trials disagree on mortality) | observed claim | expected (not verified live). May give **accurate** or **incomplete** if only one side is retrieved. |
| We'll repeat your PSA and an MRI in six months. | skipped (plan) | observed skip | — |

## 6. Papillary thyroid cancer: checker test (`check_thyroid`, WhisperX)

| Doctor sentence | Expected card | Triage | Check (live result) |
|---|---|---|---|
| Morning, come on in. / How is your neck feeling after the operation? / Good. | skipped | observed skip | — |
| The operation confirmed a papillary thyroid cancer, about one and a half centimetres across. | skipped (patient-specific) | observed skip | — |
| Papillary thyroid cancer has an excellent outlook, and about ninety-nine out of a hundred people are still alive ten years on. *(thy-01)* | claim → **Accurate** | observed claim | expected (not verified live) |
| **Capillary** thyroid cancer is the most common type of thyroid cancer. | **transcription** ("Capillary" for "Papillary", 0.36) | observed transcription (p_asr 0.95) | — |
| Most papillary thyroid cancers bigger than one centimetre are treated with radioactive iodine after surgery. | claim → **Sources disagree** (GOLDEN.md thy-04: NICE advises against RAI for T1a/T1b, an older UK guideline gives it to most tumours over 1 cm) | observed claim | expected (not verified live) |
| Anaplastic thyroid cancer usually has a good outlook, much like the papillary type. *(thy-12)* | claim → **Inaccurate** | observed claim | expected (not verified live) |
| Radiation from mobile phones is a known cause of thyroid cancer. *(nc-10)* | claim → **Not verified** | observed claim | expected (not verified live) |
| You'll start on thyroid hormone tablets today, and we'll check your blood levels in six weeks. | skipped (plan) | observed skip | — |

---

## Outcome coverage

| Outcome | Where (observed live unless marked) |
|---|---|
| Accurate | Colorectal "comes back… first three years" (crc-10) |
| Inaccurate | Colorectal "stage three… liver"; Breast "HER2-positive… slower-growing"; Melanoma "Breslow… how wide" |
| Incomplete | Colorectal "tested for mismatch repair proteins" (MSI alternative) |
| Not verified | Colorectal "spicy food"; Breast "underwire bra" |
| Sources disagree | Breast "tamoxifen… ten years"; Breast "hormone receptors… better outlook" (meant to be accurate) |
| Needs review: low confidence | Breast "HER2 negative, hormone-blocking pills won't work" (Jev 0.54) |
| Needs review: Jev disagrees | not seen in any run. Nothing here is built to trigger it reliably |
| Skipped small talk / plan / patient result | every sample (triage observed) |
| Transcription card | Colorectal "mismatch repeat", Melanoma "Breast low", DLBCL "B sell", Prostate "glisten", Thyroid "Capillary" (triage observed) |
| Expected only (not verified live) | Melanoma, DLBCL, prostate and thyroid checks (accurate, inaccurate, unverified, and disputed candidates for mitotic rate, PSA screening and RAI over 1 cm) |

## What to look for

- [ ] **Skipped sentences** get no card and no highlight: greetings, empathy, plans ("we'll book a CT…") and patient-specific results ("your biopsy shows…").
- [ ] **Sources are grouped** under Supports / Contradicts / Unclear, with irrelevant ones collapsed under "Not relevant (n)". Each has a title, section, trust badge, year and an **Open source** link that opens in a new tab.
- [ ] **No bare “” quotes.** A source with no grounded quote shows no empty quote marks, and the title isn't repeated as the section.
- [ ] **Searched for (n)** expands to the raw claim followed by the queries Luna wrote (e.g. "colorectal cancer TNM stage III regional lymph nodes").
- [ ] **Needs your review banner** shows the reason in plain words: "Sources disagree" for tamoxifen, "Jev isn't confident in the verdict (under 70%)" for the HER2 / hormone pills claim.
- [ ] **Jev confidence** reads "Jev 99% confident" on confident cards and "Jev leans contradicted (84%)" on a disputed card.
- [ ] **Not verified** cards say the corpus doesn't cover the claim, with no supporting or contradicting sources.
- [ ] **Transcription flow:** the card asks "Is this correct?". **Yes** keeps the triage label (a claim then gets checked). **No** lets you type the intended words ("Gleason", "repair", "Breslow", "B-cell", "Papillary"). The text is re-triaged and checked as typed, and the transcript itself stays unchanged.
- [ ] **Export review** downloads `review-check_<cancer>-<date>.json` with each comment's `check` (verdict, `review_reasons`, sources, `search_queries`, `confidence`).
