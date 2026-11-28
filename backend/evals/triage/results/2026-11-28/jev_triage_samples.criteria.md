# Jev triage eval: sample transcripts vs gold

Model `jev-1.14.0`, skip_min 0.8, asr_flag 0.5, 107 gold spans in 9 samples. Synthetic, AI-written gold labels (not pathologist-reviewed).

- **Claim recall: 34/34 = 100.0%** (a gold claim predicted `transcription` with base_kind `claim` counts as caught)
- **Transcription recall: 8/9 = 88.9%**
- Skip precision: 62/63 = 98.4% (predicted skip that is gold skip)
- Skip recall: 62/64 = 96.9% (gold small talk kept away from the checker)
- False transcription flags on non-error spans: 0/98 = 0.0%
- False-flag rate on spans with the correct low-confidence term: 0/19 = 0.0%

## Confusion matrix (rows = gold, columns = predicted kind)

| gold \ pred | skip | claim | transcription |
|---|---:|---:|---:|
| skip | 62 | 2 | 0 |
| claim | 0 | 34 | 0 |
| transcription | 1 | 0 | 8 |

## Correct low-confidence terms

| sample | term | quote | predicted | p_asr |
|---|---|---|---|---:|
| tubular_adenoma | adenoma | It was a tubular adenoma. | skip | 0.160 |
| tubular_adenoma | adenoma | A tubular adenoma is a benign polyp, but it could slowly turn into cancer over years if it were left alone. | claim | 0.140 |
| tubular_adenoma | adenoma | Adenoma, yes. | skip | 0.170 |
| tubular_adenoma | adenoma | Once an adenoma has been removed completely, there's no need for another colonoscopy. | claim | 0.160 |
| ulcerative_colitis | colitis | The biopsies show chronic active colitis, and the pattern fits ulcerative colitis. | claim | 0.120 |
| ulcerative_colitis | colitis | Ulcerative colitis mostly affects your small intestine. | claim | 0.360 |
| ulcerative_colitis | colitis | Over many years, ulcerative colitis can raise the risk of colon cancer. | claim | 0.200 |
| breast_idc | ductal | The biopsy of the lump in your left breast shows an invasive ductal carcinoma. | skip | 0.040 |
| fibroadenoma | fibroadenoma | A fibroadenoma is a benign, non-cancerous lump made of glandular and connective tissue, and it's common in younger women. | claim | 0.160 |
| melanoma | breslow | The Breslow thickness was 1.2 millimeters, which is how wide the spot was. | claim | 0.350 |
| dlbcl | lymphoma | The lymph node biopsy shows a diffuse large B-cell lymphoma. | skip | 0.150 |
| dlbcl | lymphoma | Diffuse large B-cell lymphoma is a fast-growing lymphoma, but it's often curable with treatment. | claim | 0.120 |
| dlbcl | lymphoma | Lymphoma only lives in the lymph nodes, so the rest of your body is fine. | claim | 0.180 |
| dlbcl | lymphoma | This type of lymphoma is usually treated with chemotherapy. | claim | 0.120 |
| dlbcl | lymphoma | I'm referring you to the lymphoma team, and they'll see you this week. | skip | 0.200 |
| prostate_gg2 | gleason | The pathologist gave it a Gleason score of 3 plus 4 equals 7, which is Grade Group 2. | claim | 0.080 |
| prostate_gg2 | gleason | A Gleason 7 is the most aggressive kind there is. | claim | 0.050 |
| thyroid_bethesda4 | follicular | The result is Bethesda category 4, which means a follicular neoplasm. | claim | 0.170 |
| thyroid_bethesda4 | follicular | A follicular neoplasm on a needle test means it's cancer. | claim | 0.260 |

## Misses (predicted kind differs from gold)

### crc_adenocarcinoma (0)

### tubular_adenoma (0)

### ulcerative_colitis (1)

| turn | quote | gold | predicted | p_claim | p_asr |
|---:|---|---|---|---:|---:|
| 2 | The biopsies show chronic active colitis, and the pattern fits ulcerative colitis. | skip | claim | 0.260 | 0.120 |

### breast_idc (1)

| turn | quote | gold | predicted | p_claim | p_asr |
|---:|---|---|---|---:|---:|
| 6 | It sits in the middle of the scale. | skip | claim | 0.530 | 0.050 |

### fibroadenoma (0)

### melanoma (0)

### dlbcl (0)

### prostate_gg2 (1)

| turn | quote | gold | predicted | p_claim | p_asr |
|---:|---|---|---|---:|---:|
| 1 | The prostrate biopsy did find cancer in a few of the samples. | transcription (skip) | skip | 0.010 | 0.460 |

### thyroid_bethesda4 (0)

## Threshold sweep (replayed from the collected probabilities)

skip_min (asr_flag fixed at the configured value):

| skip_min | claim recall | skip precision | skip recall |
|---:|---:|---:|---:|
| 0.5 | 33/34 = 97.1% | 63/65 = 96.9% | 63/64 = 98.4% |
| 0.6 | 34/34 = 100.0% | 63/64 = 98.4% | 63/64 = 98.4% |
| 0.7 | 34/34 = 100.0% | 63/64 = 98.4% | 63/64 = 98.4% |
| 0.8 | 34/34 = 100.0% | 62/63 = 98.4% | 62/64 = 96.9% |
| 0.9 | 34/34 = 100.0% | 59/60 = 98.3% | 59/64 = 92.2% |
| 0.95 | 34/34 = 100.0% | 55/56 = 98.2% | 55/64 = 85.9% |

asr_flag (skip_min fixed at the configured value):

| asr_flag | transcription recall | false flags | low-confidence term flagged |
|---:|---:|---:|---:|
| 0.3 | 9/9 = 100.0% | 2/98 = 2.0% | 2/19 = 10.5% |
| 0.4 | 9/9 = 100.0% | 0/98 = 0.0% | 0/19 = 0.0% |
| 0.5 | 8/9 = 88.9% | 0/98 = 0.0% | 0/19 = 0.0% |
| 0.6 | 8/9 = 88.9% | 0/98 = 0.0% | 0/19 = 0.0% |
| 0.7 | 8/9 = 88.9% | 0/98 = 0.0% | 0/19 = 0.0% |
| 0.8 | 8/9 = 88.9% | 0/98 = 0.0% | 0/19 = 0.0% |

## All spans

| sample | turn | gold | pred | p_claim | p_asr | quote |
|---|---:|---|---|---:|---:|---|
| crc_adenocarcinoma | 0 | skip | skip | 0.000 | 0.020 | Good morning, thanks for coming back in. |
| crc_adenocarcinoma | 0 | skip | skip | 0.000 | 0.020 | Please, have a seat. |
| crc_adenocarcinoma | 1 | skip | skip | 0.000 | 0.030 | That's completely understandable. |
| crc_adenocarcinoma | 1 | skip | skip | 0.010 | 0.040 | We have the biopsy results from your colonoscopy, so let's go through them together. |
| crc_adenocarcinoma | 2 | transcription | transcription | 0.080 | 0.950 | The growth in your sigmoid colin is an adenocarcinoma. |
| crc_adenocarcinoma | 2 | claim | claim | 1.000 | 0.030 | That's a cancer that starts in the gland cells lining the colon. |
| crc_adenocarcinoma | 3 | skip | skip | 0.060 | 0.030 | The pathologist described the tumor as moderately differentiated. |
| crc_adenocarcinoma | 3 | claim | claim | 1.000 | 0.040 | Because it's moderately differentiated, that tells us it hasn't spread. |
| crc_adenocarcinoma | 4 | skip | skip | 0.010 | 0.050 | We'll still do a CT scan of your chest, abdomen and pelvis to stage it. |
| crc_adenocarcinoma | 5 | claim | claim | 1.000 | 0.040 | Staging tells us how deep the tumor goes and whether the cancer is in the lymph nodes or other organs. |
| crc_adenocarcinoma | 6 | skip | skip | 0.000 | 0.030 | We'll also test the tumor for mismatch repair proteins. |
| crc_adenocarcinoma | 6 | claim | claim | 1.000 | 0.030 | Mismatch repair testing is a check for Lynch syndrome, an inherited condition. |
| crc_adenocarcinoma | 7 | skip | skip | 0.010 | 0.030 | Once the scan is done, the surgeon and the oncologist will meet with you together to go over treatment options. |
| crc_adenocarcinoma | 8 | skip | skip | 0.000 | 0.020 | Of course. |
| crc_adenocarcinoma | 8 | skip | skip | 0.000 | 0.030 | The front desk will call you this week to book the scan. |
| tubular_adenoma | 0 | skip | skip | 0.000 | 0.020 | Hi there, nice to see you again. |
| tubular_adenoma | 0 | skip | skip | 0.000 | 0.030 | How have you been feeling since the procedure? |
| tubular_adenoma | 1 | skip | skip | 0.050 | 0.030 | Good, that's normal. |
| tubular_adenoma | 1 | transcription | transcription | 0.030 | 0.910 | I have the pathology report on the pollen we removed during your colonoscopy. |
| tubular_adenoma | 2 | skip | skip | 0.020 | 0.050 | The pathologist didn't see anything worrying. |
| tubular_adenoma | 3 | skip | skip | 0.050 | 0.160 | It was a tubular adenoma. |
| tubular_adenoma | 3 | claim | claim | 1.000 | 0.140 | A tubular adenoma is a benign polyp, but it could slowly turn into cancer over years if it were left alone. |
| tubular_adenoma | 4 | skip | skip | 0.090 | 0.170 | Adenoma, yes. |
| tubular_adenoma | 4 | skip | skip | 0.040 | 0.040 | The good news is it was removed completely and showed no high-grade dysplasia. |
| tubular_adenoma | 4 | claim | claim | 1.000 | 0.040 | Dysplasia means the cells look abnormal under the microscope. |
| tubular_adenoma | 5 | skip | skip | 0.020 | 0.020 | That's it. |
| tubular_adenoma | 5 | claim | claim | 0.990 | 0.160 | Once an adenoma has been removed completely, there's no need for another colonoscopy. |
| tubular_adenoma | 6 | skip | skip | 0.030 | 0.060 | Nobody does. |
| tubular_adenoma | 6 | skip | skip | 0.080 | 0.040 | Keep up the fiber and let us know if you notice any bleeding or changes in your bowel habits. |
| tubular_adenoma | 7 | skip | skip | 0.000 | 0.020 | You're welcome. |
| tubular_adenoma | 7 | skip | skip | 0.000 | 0.020 | Take care. |
| ulcerative_colitis | 0 | skip | skip | 0.000 | 0.030 | Hello, come on in. |
| ulcerative_colitis | 0 | skip | skip | 0.000 | 0.020 | How are the symptoms this week? |
| ulcerative_colitis | 1 | skip | skip | 0.000 | 0.020 | I'm sorry, that sounds exhausting. |
| ulcerative_colitis | 1 | skip | skip | 0.150 | 0.040 | Your biopsy results from the colonoscopy came back, and they help explain it. |
| ulcerative_colitis | 2 | skip | claim | 0.260 | 0.120 | The biopsies show chronic active colitis, and the pattern fits ulcerative colitis. |
| ulcerative_colitis | 3 | claim | claim | 1.000 | 0.360 | Ulcerative colitis mostly affects your small intestine. |
| ulcerative_colitis | 4 | claim | claim | 0.990 | 0.030 | It's a long-term inflammatory condition, but most people manage it well with medication. |
| ulcerative_colitis | 5 | skip | skip | 0.030 | 0.040 | There was no dysplasia in the samples. |
| ulcerative_colitis | 5 | claim | claim | 1.000 | 0.200 | Over many years, ulcerative colitis can raise the risk of colon cancer. |
| ulcerative_colitis | 6 | claim | claim | 0.950 | 0.040 | Dysplasia means precancerous changes in the lining cells, and there weren't any. |
| ulcerative_colitis | 7 | transcription | transcription | 0.000 | 0.910 | We'll start you on a medication called mesa lamine and see you back in the clinic in about 8 weeks. |
| ulcerative_colitis | 8 | skip | skip | 0.090 | 0.040 | If the bleeding gets worse or you get a fever, call us right away. |
| breast_idc | 0 | skip | skip | 0.000 | 0.020 | Hi, thanks for coming in. |
| breast_idc | 0 | skip | skip | 0.000 | 0.030 | Is it okay if your sister stays for this? |
| breast_idc | 1 | skip | skip | 0.000 | 0.030 | Of course. |
| breast_idc | 1 | skip | skip | 0.010 | 0.040 | The biopsy of the lump in your left breast shows an invasive ductal carcinoma. |
| breast_idc | 2 | claim | claim | 1.000 | 0.040 | Invasive means the cancer cells have grown out of the milk duct into the surrounding breast tissue. |
| breast_idc | 3 | transcription | transcription | 0.130 | 0.940 | The pathologist also tested the humor for hormone receptors. |
| breast_idc | 3 | skip | skip | 0.040 | 0.100 | It's estrogen receptor and progesterone receptor positive, and HER2 negative. |
| breast_idc | 4 | claim | claim | 0.910 | 0.140 | Because it's HER2 negative, hormone-blocking pills won't work for you. |
| breast_idc | 5 | skip | skip | 0.050 | 0.030 | It's a grade 2 tumor. |
| breast_idc | 5 | claim | claim | 1.000 | 0.040 | Breast cancers are graded from 1 to 3. |
| breast_idc | 6 | skip | claim | 0.530 | 0.050 | It sits in the middle of the scale. |
| breast_idc | 6 | skip | skip | 0.000 | 0.040 | The next step is to meet with the breast surgeon and the oncologist to plan treatment. |
| breast_idc | 7 | skip | skip | 0.010 | 0.030 | That depends on a few more results, and we'll go through all of it at that visit. |
| fibroadenoma | 0 | skip | skip | 0.000 | 0.030 | Hi, good to see you. |
| fibroadenoma | 0 | skip | skip | 0.000 | 0.030 | Come on in and have a seat. |
| fibroadenoma | 1 | skip | skip | 0.010 | 0.020 | Well, I have good news. |
| fibroadenoma | 1 | transcription | transcription | 0.010 | 0.970 | The court biopsy of the lump in your breast shows a fibroadenoma. |
| fibroadenoma | 2 | claim | claim | 1.000 | 0.160 | A fibroadenoma is a benign, non-cancerous lump made of glandular and connective tissue, and it's common in younger women. |
| fibroadenoma | 3 | claim | claim | 1.000 | 0.030 | Sometimes they shrink. |
| fibroadenoma | 3 | claim | claim | 0.960 | 0.040 | Most fibroadenomas can simply be watched over time. |
| fibroadenoma | 4 | claim | claim | 0.980 | 0.050 | Fibroadenomas usually turn into cancer, so we have to remove it at some point. |
| fibroadenoma | 5 | skip | skip | 0.000 | 0.020 | I know it's a lot. |
| fibroadenoma | 5 | skip | skip | 0.020 | 0.030 | The surgeon can talk you through the operation and the timing. |
| fibroadenoma | 6 | skip | skip | 0.010 | 0.030 | Call the clinic if the lump gets bigger or you notice anything new. |
| melanoma | 0 | skip | skip | 0.000 | 0.020 | Hello, thanks for coming in. |
| melanoma | 0 | skip | skip | 0.000 | 0.040 | How's the spot on your back healing? |
| melanoma | 1 | skip | skip | 0.020 | 0.030 | That's normal. |
| melanoma | 1 | skip | skip | 0.010 | 0.040 | I have the pathology report from the skin biopsy, and I want to go over it carefully. |
| melanoma | 2 | skip | skip | 0.030 | 0.030 | Yes, it's a melanoma. |
| melanoma | 2 | claim | claim | 1.000 | 0.040 | Melanoma is a cancer of the pigment-making cells in the skin. |
| melanoma | 3 | skip | skip | 0.190 | 0.020 | The report gives a few measurements. |
| melanoma | 3 | claim | claim | 0.460 | 0.350 | The Breslow thickness was 1.2 millimeters, which is how wide the spot was. |
| melanoma | 4 | transcription | transcription | 0.990 | 0.970 | Breast low thickness is a standard measurement pathologists use for melanoma. |
| melanoma | 5 | claim | claim | 0.920 | 0.070 | For a melanoma like this, the standard treatment is to remove a wider area of skin around the biopsy scar. |
| melanoma | 6 | skip | skip | 0.200 | 0.030 | It will be a longer scar, but the surgeon will close it neatly. |
| melanoma | 7 | skip | skip | 0.000 | 0.030 | The surgery team will call you in the next week or two to schedule it. |
| dlbcl | 0 | skip | skip | 0.000 | 0.020 | Hi, come on in. |
| dlbcl | 0 | skip | skip | 0.000 | 0.030 | How's the neck feeling since the biopsy? |
| dlbcl | 1 | skip | skip | 0.000 | 0.020 | I understand. |
| dlbcl | 1 | skip | skip | 0.020 | 0.150 | The lymph node biopsy shows a diffuse large B-cell lymphoma. |
| dlbcl | 2 | transcription | transcription | 0.990 | 0.980 | Yes, it's a cancer of the lymphocytes, which are a type of white blood sell. |
| dlbcl | 2 | claim | claim | 1.000 | 0.120 | Diffuse large B-cell lymphoma is a fast-growing lymphoma, but it's often curable with treatment. |
| dlbcl | 3 | claim | claim | 0.990 | 0.180 | Lymphoma only lives in the lymph nodes, so the rest of your body is fine. |
| dlbcl | 4 | claim | claim | 0.980 | 0.120 | This type of lymphoma is usually treated with chemotherapy. |
| dlbcl | 5 | claim | claim | 0.990 | 0.050 | With most of these regimens, yes, hair loss is common, but it grows back after treatment ends. |
| dlbcl | 6 | skip | skip | 0.000 | 0.030 | Soon. |
| dlbcl | 6 | skip | skip | 0.000 | 0.200 | I'm referring you to the lymphoma team, and they'll see you this week. |
| prostate_gg2 | 0 | skip | skip | 0.000 | 0.020 | Good afternoon, thanks for coming in. |
| prostate_gg2 | 0 | skip | skip | 0.000 | 0.030 | Your wife is welcome to stay. |
| prostate_gg2 | 1 | transcription | skip | 0.010 | 0.460 | The prostrate biopsy did find cancer in a few of the samples. |
| prostate_gg2 | 2 | claim | claim | 0.550 | 0.080 | The pathologist gave it a Gleason score of 3 plus 4 equals 7, which is Grade Group 2. |
| prostate_gg2 | 3 | claim | claim | 1.000 | 0.050 | A Gleason 7 is the most aggressive kind there is. |
| prostate_gg2 | 4 | claim | claim | 0.970 | 0.040 | For this grade of prostate cancer, the standard treatment is surgery to remove the prostate. |
| prostate_gg2 | 5 | skip | skip | 0.000 | 0.040 | I'll refer you to a urologist and an oncologist to talk through the details. |
| prostate_gg2 | 6 | skip | skip | 0.000 | 0.030 | Absolutely, and bring any questions you have written down. |
| thyroid_bethesda4 | 0 | skip | skip | 0.000 | 0.030 | Hi, thanks for waiting. |
| thyroid_bethesda4 | 0 | skip | skip | 0.000 | 0.030 | Come on in. |
| thyroid_bethesda4 | 1 | transcription | transcription | 0.010 | 0.970 | The fine needle aspiration of your thyroid noodle came back, and the pathologist reviewed the cytology. |
| thyroid_bethesda4 | 2 | claim | claim | 0.870 | 0.170 | The result is Bethesda category 4, which means a follicular neoplasm. |
| thyroid_bethesda4 | 3 | claim | claim | 1.000 | 0.040 | Neoplasm means a growth. |
| thyroid_bethesda4 | 3 | claim | claim | 1.000 | 0.260 | A follicular neoplasm on a needle test means it's cancer. |
| thyroid_bethesda4 | 4 | claim | claim | 0.930 | 0.050 | For a Bethesda 4 nodule, the usual next step is surgery to take out half of the thyroid. |
| thyroid_bethesda4 | 5 | claim | claim | 0.790 | 0.150 | Many people don't after removing one side, but we'll check your thyroid levels with a blood test after surgery. |
| thyroid_bethesda4 | 6 | claim | claim | 0.740 | 0.040 | Most people go home the same day or the next morning, and the surgeon will go over that with you. |
