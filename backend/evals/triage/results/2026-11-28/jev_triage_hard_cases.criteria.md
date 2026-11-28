# Jev triage eval: hard cases

Variant `criteria`, model `jev-1.14.0`, skip_min 0.8, asr_flag 0.5. 76 hard cases (`evals/triage/hard_cases.jsonl`) through `router._triage_turn`, plus 66 spans of the 6 checker-test transcripts through POST /triage. Jev calls this run: 123. Synthetic, AI-written gold labels (not pathologist-reviewed).

## This run

- **Hard cases claim recall: 37/37 = 100.0%**
- Hard cases skip precision: 29/29 = 100.0%
- Hard cases skip recall: 29/39 = 74.4%
- Hard cases flagged as transcription: 0
- **Checker tests claim recall: 26/26 = 100.0%**, transcription recall 5/5 = 100.0%, skip precision 35/35 = 100.0%, skip recall 35/35 = 100.0%

| category | gold | correct |
|---|---|---:|
| context_reply | skip | 10/15 = 66.7% |
| patient_specific | skip | 11/12 = 91.7% |
| plan_as_fact | skip | 8/12 = 66.7% |
| hedged_fact | claim | 12/12 = 100.0% |
| short_fact | claim | 13/13 = 100.0% |
| fact_in_reply | claim | 12/12 = 100.0% |

## All saved runs of `criteria` (3 hard, 3 checker): mean (range)

| metric | hard cases | checker tests |
|---|---:|---:|
| claim recall | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) |
| claim precision | 78.7% (78.7%-78.7%) | 100.0% (100.0%-100.0%) |
| skip precision | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) |
| skip recall | 74.4% (74.4%-74.4%) | 100.0% (100.0%-100.0%) |
| context_reply | 66.7% (66.7%-66.7%) | n/a |
| patient_specific | 91.7% (91.7%-91.7%) | n/a |
| plan_as_fact | 66.7% (66.7%-66.7%) | n/a |
| hedged_fact | 100.0% (100.0%-100.0%) | n/a |
| short_fact | 100.0% (100.0%-100.0%) | n/a |
| fact_in_reply | 100.0% (100.0%-100.0%) | n/a |
| transcription recall | n/a | 100.0% (100.0%-100.0%) |

## Hard-case misses across saved runs

| id | category | gold | missed | p_claim per run | span |
|---|---|---|---:|---|---|
| cr-most-people-do | context_reply | skip | 3/3 | 0.28, 0.25, 0.23 | Most people do. |
| cr-it-can-be | context_reply | skip | 3/3 | 0.22, 0.25, 0.22 | It can be. |
| cr-not-always | context_reply | skip | 3/3 | 0.44, 0.36, 0.35 | Not always. |
| cr-for-some-people | context_reply | skip | 3/3 | 0.48, 0.50, 0.49 | It is for some people. |
| cr-thats-a-myth | context_reply | skip | 3/3 | 0.60, 0.56, 0.63 | That's a myth. |
| ps-gleason | patient_specific | skip | 3/3 | 0.35, 0.34, 0.34 | Yours is three plus four, which makes it a seven. |
| pf-repeat-scan | plan_as_fact | skip | 3/3 | 0.68, 0.64, 0.67 | We usually repeat the scan in a year. |
| pf-normally-offer | plan_as_fact | skip | 3/3 | 0.71, 0.71, 0.70 | We normally offer tablets for five years after surgery for this. |
| pf-next-step | plan_as_fact | skip | 3/3 | 0.56, 0.58, 0.57 | The usual next step is to remove that half of the thyroid. |
| pf-no-ct | plan_as_fact | skip | 3/3 | 0.83, 0.85, 0.87 | We don't usually do a CT scan at this stage. |

## Checker-test misses (this run)

| sample | quote | gold | predicted | p_claim | p_asr |
|---|---|---|---|---:|---:|

## Threshold sweep (all saved runs, replayed): mean (range)

| skip_min | hard claim recall | hard skip precision | hard skip recall | checker claim recall |
|---:|---:|---:|---:|---:|
| 0.5 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 87.2% (87.2%-87.2%) | 100.0% (100.0%-100.0%) |
| 0.6 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 83.8% (82.1%-84.6%) | 100.0% (100.0%-100.0%) |
| 0.7 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 79.5% (79.5%-79.5%) | 100.0% (100.0%-100.0%) |
| 0.8 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 74.4% (74.4%-74.4%) | 100.0% (100.0%-100.0%) |
| 0.9 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 67.5% (66.7%-69.2%) | 100.0% (100.0%-100.0%) |
| 0.95 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | 64.1% (64.1%-64.1%) | 100.0% (100.0%-100.0%) |

## All hard cases (this run)

| id | category | gold | pred | p_claim | p_asr | span |
|---|---|---|---|---:|---:|---|
| cr-nobody-does | context_reply | skip | skip | 0.020 | 0.090 | Nobody does. |
| cr-exactly | context_reply | skip | skip | 0.010 | 0.020 | Exactly. |
| cr-thats-normal | context_reply | skip | skip | 0.010 | 0.020 | That's normal. |
| cr-most-people-do | context_reply | skip | claim | 0.230 | 0.040 | Most people do. |
| cr-same-here | context_reply | skip | skip | 0.000 | 0.030 | Same here. |
| cr-it-can-be | context_reply | skip | claim | 0.220 | 0.040 | It can be. |
| cr-not-always | context_reply | skip | claim | 0.350 | 0.030 | Not always. |
| cr-that-happens | context_reply | skip | skip | 0.040 | 0.020 | That happens. |
| cr-very-common | context_reply | skip | skip | 0.190 | 0.030 | That's very common. |
| cr-no-it-doesnt | context_reply | skip | skip | 0.150 | 0.030 | No, it doesn't. |
| cr-you-should-be | context_reply | skip | skip | 0.000 | 0.040 | You should be. |
| cr-for-some-people | context_reply | skip | claim | 0.490 | 0.060 | It is for some people. |
| cr-the-idea | context_reply | skip | skip | 0.000 | 0.020 | That's the idea. |
| cr-good-question | context_reply | skip | skip | 0.000 | 0.020 | Good question. |
| cr-thats-a-myth | context_reply | skip | claim | 0.630 | 0.020 | That's a myth. |
| ps-biopsy-shows | patient_specific | skip | skip | 0.000 | 0.030 | Your biopsy shows an adenocarcinoma in the sigmoid colon. |
| ps-probably-early | patient_specific | skip | skip | 0.010 | 0.040 | Yours looks like it's probably early. |
| ps-margins-clear | patient_specific | skip | skip | 0.030 | 0.030 | Your margins were clear on every side. |
| ps-gleason | patient_specific | skip | claim | 0.340 | 0.040 | Yours is three plus four, which makes it a seven. |
| ps-nodes-negative | patient_specific | skip | skip | 0.010 | 0.030 | None of your lymph nodes had cancer in them. |
| ps-its-benign | patient_specific | skip | skip | 0.120 | 0.030 | It's benign. |
| ps-receptor | patient_specific | skip | skip | 0.010 | 0.030 | Yours is estrogen receptor positive. |
| ps-polyps-removed | patient_specific | skip | skip | 0.010 | 0.030 | We removed three polyps, and all of them were tubular adenomas. |
| ps-suspect-lymphoma | patient_specific | skip | skip | 0.090 | 0.040 | I suspect yours is a lymphoma, but we need the biopsy to be sure. |
| ps-bethesda | patient_specific | skip | skip | 0.010 | 0.060 | Your result came back as Bethesda category four. |
| ps-mild-colitis | patient_specific | skip | skip | 0.020 | 0.040 | The inflammation in your colon is mild and only in the rectum. |
| ps-breslow | patient_specific | skip | skip | 0.010 | 0.040 | Yours measured 0.8 millimetres. |
| pf-repeat-scan | plan_as_fact | skip | claim | 0.670 | 0.030 | We usually repeat the scan in a year. |
| pf-start-surgery | plan_as_fact | skip | skip | 0.000 | 0.020 | We'll start with surgery. |
| pf-colonoscopy-5y | plan_as_fact | skip | skip | 0.000 | 0.040 | We'll see you for another colonoscopy in five years. |
| pf-surveillance | plan_as_fact | skip | skip | 0.000 | 0.040 | We'll keep an eye on it with a PSA test every six months instead of treating it now. |
| pf-mdt | plan_as_fact | skip | skip | 0.000 | 0.030 | Your case goes to the team meeting on Thursday. |
| pf-normally-offer | plan_as_fact | skip | claim | 0.700 | 0.050 | We normally offer tablets for five years after surgery for this. |
| pf-sentinel | plan_as_fact | skip | skip | 0.000 | 0.040 | We'll book you in for a sentinel node biopsy next month. |
| pf-rchop | plan_as_fact | skip | skip | 0.000 | 0.040 | We'll give you six cycles of R-CHOP, one every three weeks. |
| pf-bloods | plan_as_fact | skip | skip | 0.000 | 0.060 | We'll check your bloods in a fortnight to see how you're responding. |
| pf-next-step | plan_as_fact | skip | claim | 0.570 | 0.030 | The usual next step is to remove that half of the thyroid. |
| pf-referral | plan_as_fact | skip | skip | 0.010 | 0.030 | I'll refer you to the breast clinic so they can confirm it with an ultrasound. |
| pf-no-ct | plan_as_fact | skip | claim | 0.870 | 0.030 | We don't usually do a CT scan at this stage. |
| hf-usually-means | hedged_fact | claim | claim | 1.000 | 0.040 | That usually means it grows at a moderate pace. |
| hf-most-polyps | hedged_fact | claim | claim | 1.000 | 0.030 | Most polyps never turn into cancer, but some can over many years. |
| hf-lobular | hedged_fact | claim | claim | 1.000 | 0.040 | Lobular breast cancers tend to be harder to see on a mammogram. |
| hf-generally | hedged_fact | claim | claim | 0.990 | 0.040 | These generally respond well to chemotherapy, and many people are cured. |
| hf-colitis-may | hedged_fact | claim | claim | 1.000 | 0.040 | Ulcerative colitis may slightly raise the risk of bowel cancer over many years. |
| hf-i-think | hedged_fact | claim | claim | 1.000 | 0.040 | I think they can shrink after the menopause, as hormone levels fall. |
| hf-papillary-often | hedged_fact | claim | claim | 1.000 | 0.040 | Papillary thyroid cancer often spreads to the lymph nodes in the neck, but it still has an excellent outlook. |
| hf-as-far-as-we-know | hedged_fact | claim | claim | 1.000 | 0.030 | As far as we know, most prostate cancers aren't inherited. |
| hf-sunburn | hedged_fact | claim | claim | 1.000 | 0.040 | Sunburn in childhood probably matters more for melanoma than sun exposure later on. |
| hf-seeding | hedged_fact | claim | claim | 1.000 | 0.040 | That's not usually a concern, because needle biopsies very rarely seed cancer cells. |
| hf-transform | hedged_fact | claim | claim | 1.000 | 0.030 | A low-grade lymphoma can sometimes change into a more aggressive type. |
| hf-one-in-eight | hedged_fact | claim | claim | 1.000 | 0.030 | Roughly one in eight women will get breast cancer at some point in their life. |
| sf-melanoma-spread | short_fact | claim | claim | 1.000 | 0.020 | Melanoma can spread. |
| sf-her2 | short_fact | claim | claim | 1.000 | 0.030 | HER2 is a protein. |
| sf-polyps-slow | short_fact | claim | claim | 1.000 | 0.030 | Polyps grow slowly. |
| sf-smoking-does | short_fact | claim | claim | 0.950 | 0.030 | Smoking does. |
| sf-psa | short_fact | claim | claim | 1.000 | 0.040 | PSA is made by the prostate. |
| sf-curable | short_fact | claim | claim | 0.990 | 0.030 | It's often curable. |
| sf-colitis-contagious | short_fact | claim | claim | 0.990 | 0.040 | Colitis isn't contagious. |
| sf-tamoxifen | short_fact | claim | claim | 1.000 | 0.040 | Tamoxifen blocks estrogen. |
| sf-moles | short_fact | claim | claim | 1.000 | 0.040 | Moles rarely become melanoma. |
| sf-sunscreen | short_fact | claim | claim | 1.000 | 0.040 | Sunscreen lowers melanoma risk. |
| sf-nodules-common | short_fact | claim | claim | 1.000 | 0.020 | Nodules are very common. |
| sf-adenomas | short_fact | claim | claim | 1.000 | 0.030 | Adenomas are precancerous. |
| sf-families | short_fact | claim | claim | 1.000 | 0.020 | It runs in families. |
| fr-polyps-benign | fact_in_reply | claim | claim | 1.000 | 0.020 | Most polyps are benign. |
| fr-not-catching | fact_in_reply | claim | claim | 1.000 | 0.040 | No, colitis isn't something you can catch from someone else. |
| fr-come-back | fact_in_reply | claim | claim | 0.950 | 0.030 | Yes, melanoma can come back even after a complete removal, so we'll check your skin regularly. |
| fr-spicy | fact_in_reply | claim | claim | 1.000 | 0.060 | Spicy food doesn't cause bowel cancer, as far as we know. |
| fr-men | fact_in_reply | claim | claim | 1.000 | 0.040 | Actually, men can get breast cancer too, though it's rare. |
| fr-lymph-nodes | fact_in_reply | claim | claim | 1.000 | 0.030 | Lymph nodes are small glands that filter fluid and help fight infection. |
| fr-biopsy-myth | fact_in_reply | claim | claim | 1.000 | 0.030 | Biopsies don't make cancer spread. |
| fr-her2-depends | fact_in_reply | claim | claim | 1.000 | 0.060 | It depends, but HER2-positive cancers usually respond well to targeted drugs like trastuzumab. |
| fr-papillary-common | fact_in_reply | claim | claim | 0.990 | 0.040 | Yes, papillary thyroid cancer is the most common type, and the outlook is very good. |
| fr-psa-age | fact_in_reply | claim | claim | 1.000 | 0.040 | No, PSA naturally goes up a little as men get older. |
| fr-sunbeds | fact_in_reply | claim | claim | 1.000 | 0.040 | Right, sunbeds raise the risk of melanoma, especially before age thirty-five. |
| fr-nausea | fact_in_reply | claim | claim | 1.000 | 0.060 | Anti-sickness tablets prevent most of the nausea from chemo these days. |
