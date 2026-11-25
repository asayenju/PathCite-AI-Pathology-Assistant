import type { ReviewComment } from '@/types'

/**
 * A hand-written mock comment. It has no start/end: those are filled by anchorComment against the imported turn when
 * the mock source emits it. `clarification` is the canned answer mockClarify gives to any reply (null for accurate).
 */
export type MockComment = Omit<ReviewComment, 'start' | 'end'> & { clarification: string | null }

export const MOCK_NOTICE = 'Hand-written mock for UX testing, not reviewed by a pathologist.'
