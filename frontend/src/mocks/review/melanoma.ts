/**
 * Hand-written mock for UX testing, not reviewed by a pathologist.
 * Sample: melanoma (AWS Transcribe: punctuation attached to words). Comments target doctor turns only.
 * Overlap: mel-2 and mel-3 share "1.2 millimeters," (t0007). Orphan: mel-6 (quote says "millimetres").
 */
import type { MockComment } from './types'

export const melanoma: MockComment[] = [
  {
    id: 'mel-1',
    turn_id: 't0005',
    quote: 'Melanoma is a cancer of the pigment-making cells in the skin.',
    verdict: 'accurate',
    issue: null,
    suggestion: null,
    source: 'WHO Classification of Tumours: Skin Tumours, 5th edition',
    clarification: null,
  },
  {
    id: 'mel-2',
    turn_id: 't0007',
    quote: 'The Breslow thickness was 1.2 millimeters,',
    verdict: 'incomplete',
    issue: 'The number is given without what it means. Other key report items (such as ulceration and margins) are not mentioned.',
    suggestion:
      'The Breslow thickness was 1.2 millimetres. That puts it in the T2 group, and the report also tells us whether the surface was ulcerated, which affects the stage.',
    source: 'AJCC Cancer Staging Manual, 8th edition: melanoma of the skin',
    clarification:
      'In AJCC 8th edition, melanomas thicker than 1.0 mm and up to 2.0 mm are T2, subdivided by ulceration. Thickness is the main driver of the next steps, so the patient benefits from hearing roughly where 1.2 mm sits.',
  },
  {
    id: 'mel-3',
    turn_id: 't0007',
    quote: '1.2 millimeters, which is how wide the spot was.',
    verdict: 'inaccurate',
    issue: 'Breslow thickness measures depth, not width: how far down into the skin the melanoma reaches.',
    suggestion: '1.2 millimetres, which is how deep the melanoma reaches into the skin.',
    source: 'AJCC Cancer Staging Manual, 8th edition: melanoma of the skin',
    clarification:
      'Breslow thickness is measured under the microscope from the top of the skin down to the deepest melanoma cells. The width of the spot is a different measurement and matters much less for prognosis.',
  },
  {
    id: 'mel-4',
    turn_id: 't0009',
    quote: "It's a standard measurement pathologists use for melanoma.",
    verdict: 'incomplete',
    issue: "The patient asked what Breslow is, and the answer doesn't say what it measures or why it matters.",
    suggestion:
      "It's how deep the melanoma goes into the skin, measured under the microscope. It's one of the most important numbers for planning treatment.",
    source: 'AJCC Cancer Staging Manual, 8th edition: melanoma of the skin',
    clarification:
      'Breslow thickness is the strongest single predictor in the T category and guides the excision margin and whether a sentinel lymph node biopsy is discussed. A plain answer here also corrects the earlier "width" description.',
  },
  {
    id: 'mel-5',
    turn_id: 't0011',
    quote: "Next we'll remove a wider area of skin around the scar.",
    verdict: 'incomplete',
    issue: 'For a melanoma of this thickness a sentinel lymph node biopsy is usually discussed, and it is not mentioned.',
    suggestion:
      "Next we'll remove a wider area of skin around the scar. We'll also talk about checking the nearest lymph nodes with a sentinel lymph node biopsy.",
    source: 'NCCN Guidelines: Melanoma: Cutaneous (wide excision and sentinel node biopsy)',
    clarification:
      'Guidelines recommend discussing and offering sentinel lymph node biopsy for melanomas thicker than about 0.8 mm. It is usually done at the same operation as the wider excision, so it belongs in this conversation.',
  },
  {
    id: 'mel-6',
    turn_id: 't0007',
    quote: 'The Breslow thickness was 1.2 millimetres, which is how wide the spot was.',
    verdict: 'inaccurate',
    issue: 'Describes Breslow thickness as a width. It is a depth.',
    suggestion: 'The Breslow thickness was 1.2 millimetres, which is how deep the melanoma reaches into the skin.',
    source: 'AJCC Cancer Staging Manual, 8th edition: melanoma of the skin',
    clarification:
      'This comment quotes the sentence with British spelling, so it cannot be matched to the transcript text. The point is the same as the highlighted one: Breslow thickness is a depth measurement.',
  },
]
