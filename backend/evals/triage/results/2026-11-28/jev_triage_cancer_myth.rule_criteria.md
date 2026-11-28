# Jev triage eval: Cancer-Myth statements

`Cancer-Myth/Cancer-Myth` (CC-BY-4.0), split validation. Each `source_myth` statement triaged alone as one span (ask_asr on), skip_min 0.8, asr_flag 0.5. Excluded non-statements: 'From physicians.'. 583 rows, 367 distinct statements sent.

- **Claim recall (rows): 583/583 = 100.0%**
- Claim recall (distinct statements): 367/367 = 100.0%
- Predicted kinds (rows): skip 0, claim 583, transcription 0

## By category

| category | rows | caught | recall | flagged transcription |
|---|---:|---:|---:|---:|
| only/standard treatment | 129 | 129 | 100.0% | 0 |
| no treatment | 120 | 120 | 100.0% | 0 |
| inevitable side effect | 104 | 104 | 100.0% | 0 |
| underestimate risk | 71 | 71 | 100.0% | 0 |
| causal misattribution | 70 | 70 | 100.0% | 0 |
| no symptoms means no disease | 47 | 47 | 100.0% | 0 |
| other | 42 | 42 | 100.0% | 0 |

## Misses (0 distinct statements)

| statement | predicted | p_claim | p_asr |
|---|---|---:|---:|

## Closest to skip (10 lowest p_claim)

| statement | predicted | p_claim | p_asr |
|---|---|---:|---:|
| If my colon cancer has spread, I probably won’t live very long. My cancer will never be cured. | claim | 0.450 | 0.030 |
| I can't have children now that I've had cervical cancer. | claim | 0.480 | 0.040 |
| I don't need to get screened because I don't have any symptoms. | claim | 0.570 | 0.030 |
| I am too young to be diagnosed with cancer. | claim | 0.610 | 0.040 |
| Insurance will not cover clinical trials. | claim | 0.710 | 0.040 |
| Once treatment is completed, the child is completely cured and no further monitoring is needed. | claim | 0.900 | 0.050 |
| Clinical trials are too expensive for patients to consider. | claim | 0.940 | 0.040 |
| Patients must wait for a fully matched bone marrow donor. | claim | 0.950 | 0.040 |
| Life goes back to 'normal' once a patient’s treatment plan is complete. | claim | 0.950 | 0.050 |
| Pregnant women with breast cancer should always terminate the pregnancy to improve their own survival chances. | claim | 0.960 | 0.040 |

## Threshold sweep (rows; replayed from the collected probabilities)

| skip_min | claim recall |
|---:|---:|
| 0.5 | 577/583 = 99.0% |
| 0.6 | 583/583 = 100.0% |
| 0.7 | 583/583 = 100.0% |
| 0.8 | 583/583 = 100.0% |
| 0.9 | 583/583 = 100.0% |
| 0.95 | 583/583 = 100.0% |

| asr_flag | statements flagged as transcription (all are false flags) |
|---:|---:|
| 0.3 | 0/583 = 0.0% |
| 0.4 | 0/583 = 0.0% |
| 0.5 | 0/583 = 0.0% |
| 0.6 | 0/583 = 0.0% |
| 0.7 | 0/583 = 0.0% |
| 0.8 | 0/583 = 0.0% |
