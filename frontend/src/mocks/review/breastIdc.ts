/**
 * Hand-written mock for UX testing, not reviewed by a pathologist.
 * Sample: breast_idc (OpenAI diarized: text only, no word list). Comments target doctor turns only.
 * Overlap: brc-3 contains brc-5 (t0009). Orphan: brc-8 (quote says "test results", the turn says "results").
 */
import type { MockComment } from './types'

export const breastIdc: MockComment[] = [
  {
    id: 'brc-1',
    turn_id: 't0005',
    quote:
      'Invasive means the cancer cells have grown out of the milk duct into the surrounding breast tissue.',
    verdict: 'accurate',
    issue: null,
    suggestion: null,
    source: 'WHO Classification of Tumours: Breast Tumours, 5th edition',
    clarification: null,
  },
  {
    id: 'brc-2',
    turn_id: 't0007',
    quote: "It's estrogen receptor and progesterone receptor positive, and HER2 negative.",
    verdict: 'incomplete',
    issue: 'The receptor results are given as jargon with no explanation of what they mean for treatment.',
    suggestion:
      "It's estrogen and progesterone receptor positive, which means hormones help it grow and hormone-blocking treatment is likely to help. It's HER2 negative, so drugs that target HER2 aren't needed.",
    source: 'CAP Template for Reporting Results of Biomarker Testing of Specimens From Patients With Carcinoma of the Breast',
    clarification:
      'ER, PR and HER2 are the three markers that most shape breast cancer treatment. The patient said she did not understand them, so a one-line meaning for each is worth adding.',
  },
  {
    id: 'brc-3',
    turn_id: 't0009',
    quote: "Because it's HER2 negative",
    verdict: 'inaccurate',
    issue: 'HER2 status does not decide whether hormone therapy works. Hormone therapy is guided by the estrogen and progesterone receptors.',
    suggestion: "Because it's estrogen receptor positive",
    source: 'ASCO/CAP Guideline: Estrogen and Progesterone Receptor Testing in Breast Cancer',
    clarification:
      'HER2 negative means HER2-targeted drugs such as trastuzumab are not expected to help. It says nothing against hormone therapy; that decision rests on ER and PR, which are positive here.',
  },
  {
    id: 'brc-4',
    turn_id: 't0009',
    quote: "hormone-blocking pills won't work for you.",
    verdict: 'inaccurate',
    issue: 'The tumour is hormone receptor positive, so hormone-blocking (endocrine) therapy is expected to help, not to fail.',
    suggestion: 'hormone-blocking pills are likely to be an important part of your treatment.',
    source: 'ASCO/CAP Guideline: Estrogen and Progesterone Receptor Testing in Breast Cancer',
    clarification:
      'Endocrine therapy such as tamoxifen or an aromatase inhibitor is a standard option for ER-positive breast cancer. Telling the patient it will not work could discourage her from a treatment she is likely to be offered.',
  },
  {
    id: 'brc-5',
    turn_id: 't0009',
    quote: 'HER2 negative',
    verdict: 'incomplete',
    issue: 'The turn mentions HER2 negative without saying what it actually means for treatment.',
    suggestion: 'Being HER2 negative means drugs that target HER2 are not part of the plan.',
    source: 'ASCO/CAP Guideline: HER2 Testing in Breast Cancer',
    clarification:
      'HER2 is a growth signal protein. When the tumour does not overexpress it, HER2-targeted drugs are not expected to help, and the treatment plan relies on other options.',
  },
  {
    id: 'brc-6',
    turn_id: 't0011',
    quote: "It's a grade 2 tumor.",
    verdict: 'incomplete',
    issue: 'Grade is stated without saying what it describes.',
    suggestion:
      "It's a grade 2 tumour. Grade describes how different the cancer cells look from normal cells and how quickly they seem to be dividing, on a scale of 1 to 3.",
    source: 'CAP Protocol: Invasive Carcinoma of the Breast (Nottingham histologic grade)',
    clarification:
      'The Nottingham grade combines gland formation, how abnormal the nuclei look and how many cells are dividing. Grade 2 is intermediate. A short explanation answers the question the patient asks next.',
  },
  {
    id: 'brc-7',
    turn_id: 't0013',
    quote: 'It sits in the middle of the scale.',
    verdict: 'accurate',
    issue: null,
    suggestion: null,
    source: 'CAP Protocol: Invasive Carcinoma of the Breast (Nottingham histologic grade)',
    clarification: null,
  },
  {
    id: 'brc-8',
    turn_id: 't0015',
    quote: 'That depends on a few more test results',
    verdict: 'incomplete',
    issue: 'Does not say which results the chemotherapy decision depends on.',
    suggestion:
      'That depends on a few more results, such as the size of the tumour and whether lymph nodes are involved after surgery, and sometimes a test of the tumour genes.',
    source: 'NCCN Guidelines: Breast Cancer (adjuvant systemic therapy)',
    clarification:
      'For hormone receptor positive, HER2 negative cancer, the chemotherapy decision usually depends on tumour size, lymph node status and, in some patients, a genomic assay of the tumour. Naming them helps the patient know what she is waiting for.',
  },
]
