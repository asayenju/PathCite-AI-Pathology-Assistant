/** Hand-written mock reviews keyed by sample id (public/transcripts/index.json). Not reviewed by a pathologist. */
import { breastIdc } from './breastIdc'
import { crcAdenocarcinoma } from './crcAdenocarcinoma'
import { melanoma } from './melanoma'
import type { MockComment } from './types'

export const MOCK_REVIEWS: Record<string, MockComment[]> = {
  crc_adenocarcinoma: crcAdenocarcinoma,
  breast_idc: breastIdc,
  melanoma,
}

/** Whether a sample has a mock review. Uploaded transcripts (no sample id) and other samples don't. */
export function hasMockReview(sampleId: string | null | undefined): boolean {
  return !!sampleId && Object.hasOwn(MOCK_REVIEWS, sampleId)
}
