/**
 * Hand-written mock for UX testing, not reviewed by a pathologist.
 * Sample: crc_adenocarcinoma (Deepgram, word-level). Comments target doctor turns only.
 * Overlap: crc-5 contains crc-6 (t0011). Orphan: crc-8 (quote says "plans", the turn says "options").
 */
import type { MockComment } from './types'

export const crcAdenocarcinoma: MockComment[] = [
  {
    id: 'crc-1',
    turn_id: 't0005',
    quote: "That's a cancer that starts in the gland cells lining the colon.",
    verdict: 'accurate',
    issue: null,
    suggestion: null,
    source: 'WHO Classification of Tumours: Digestive System Tumours, 5th edition',
    clarification: null,
  },
  {
    id: 'crc-2',
    turn_id: 't0007',
    quote: "Because it's moderately differentiated",
    verdict: 'inaccurate',
    issue: 'Grade (differentiation) describes how abnormal the cells look. It is not evidence about whether the cancer has spread.',
    suggestion:
      "Moderately differentiated describes how the cells look under the microscope. It doesn't tell us whether the cancer has spread.",
    source: 'AJCC Cancer Staging Manual, 8th edition: colon and rectum',
    clarification:
      'Grade and stage are separate. Grade comes from how much the tumour still resembles normal glands; stage comes from how deep it grows and whether lymph nodes or other organs are involved. A moderately differentiated tumour can be at any stage.',
  },
  {
    id: 'crc-3',
    turn_id: 't0007',
    quote: "that tells us it hasn't spread.",
    verdict: 'inaccurate',
    issue: 'Spread is not known yet: the staging CT has not been done and no lymph nodes have been examined.',
    suggestion: "We don't know yet whether it has spread. The CT scan and, later, the surgery will tell us that.",
    source: 'NCCN Guidelines: Colon Cancer (workup)',
    clarification:
      'A biopsy samples only part of the tumour and contains no lymph nodes, so it cannot show spread. Saying it has not spread before staging may falsely reassure the patient and need to be walked back later.',
  },
  {
    id: 'crc-4',
    turn_id: 't0009',
    quote: "We'll still do a CT scan of your chest, abdomen and pelvis to stage it.",
    verdict: 'incomplete',
    issue: 'The usual workup also includes a CEA blood test, which is not mentioned.',
    suggestion:
      "We'll do a CT scan of your chest, abdomen and pelvis to stage it, and a blood test called CEA that we can compare against later.",
    source: 'NCCN Guidelines: Colon Cancer (workup)',
    clarification:
      'CEA is a tumour marker measured before treatment. On its own it does not diagnose or stage the cancer, but a baseline value helps when it is followed during and after treatment.',
  },
  {
    id: 'crc-5',
    turn_id: 't0011',
    quote:
      'Staging tells us how deep the tumor goes and whether the cancer is in the lymph nodes or other organs.',
    verdict: 'accurate',
    issue: null,
    suggestion: null,
    source: 'AJCC Cancer Staging Manual, 8th edition: colon and rectum',
    clarification: null,
  },
  {
    id: 'crc-6',
    turn_id: 't0011',
    quote: 'how deep the tumor goes',
    verdict: 'incomplete',
    issue:
      'The CT gives a first (clinical) stage. Depth of invasion and lymph node status are confirmed by the pathologist on the surgical specimen.',
    suggestion:
      'The scan gives us a first picture. The final stage comes from the pathologist examining the tumour and lymph nodes after surgery.',
    source: 'CAP Cancer Protocol: Colon and Rectum Resection',
    clarification:
      'CT is good at finding spread to organs like the liver or lungs but cannot measure exactly how deep the tumour grows into the bowel wall. The pathological stage from the resection is what guides decisions such as chemotherapy after surgery.',
  },
  {
    id: 'crc-7',
    turn_id: 't0013',
    quote: "We'll also test the tumor for mismatch repair proteins.",
    verdict: 'incomplete',
    issue: 'No reason is given. The patient is not told the result can point to an inherited condition (Lynch syndrome) or affect treatment.',
    suggestion:
      "We'll also test the tumour for mismatch repair proteins. This checks for a pattern that can run in families, called Lynch syndrome, and it can also affect which treatments work best.",
    source: 'NCCN Guidelines: Colon Cancer (universal MMR/MSI testing)',
    clarification:
      'Mismatch repair testing is recommended for every colorectal cancer. An abnormal result may lead to genetic counselling for the patient and relatives, and in some settings it changes treatment options such as immunotherapy.',
  },
  {
    id: 'crc-8',
    turn_id: 't0015',
    quote: 'the surgeon and the oncologist will meet with you together to go over treatment plans',
    verdict: 'incomplete',
    issue: 'Does not say that whether chemotherapy is needed usually depends on the stage found at surgery.',
    suggestion:
      'Once the scan is done, the surgeon and the oncologist will go over treatment with you. Surgery usually comes first, and whether you need chemotherapy depends on what we find then.',
    source: 'NCCN Guidelines: Colon Cancer (primary treatment)',
    clarification:
      'For colon cancer without distant spread, surgery is usually the first treatment, and the need for chemotherapy afterwards depends on the pathological stage. Setting that expectation now helps the patient understand why the plan may change after surgery.',
  },
]
