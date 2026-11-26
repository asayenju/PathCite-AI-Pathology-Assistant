import { anchorComment } from '@/lib/review'
import { wait } from '@/lib/wait'
import type { ReviewComment, ReviewEvent, ReviewSource, RoledTurn, TranscriptTurn } from '@/types'
import { MOCK_REVIEWS } from './reviews'
import type { MockComment } from './types'

export const DEFAULT_TURN_DELAY_MS = 900
export const DEFAULT_CLARIFY_DELAY_MS = 800

const FALLBACK_CLARIFICATION =
  "This is a demo review, so there is no further detail for this comment. Check the cited source for the exact wording."

/** A mock comment as emitted: anchored against its turn (start/end null for an orphan), without the canned answer. */
export function toReviewComment(mock: MockComment, turn: Pick<TranscriptTurn, 'text'>): ReviewComment {
  const { clarification: _clarification, ...rest } = mock
  const anchor = anchorComment(turn, mock.quote)
  return { ...rest, start: anchor?.start ?? null, end: anchor?.end ?? null }
}

/**
 * Streams the hand-written comments for a sample: for each doctor turn in order, turn_started, then (after delayMs)
 * its comment events, then turn_done; finally done. Aborting stops it with no further events, including mid-delay.
 * A sample with no mock yields a single error event.
 */
export function mockReviewSource(sampleId: string, { delayMs = DEFAULT_TURN_DELAY_MS } = {}): ReviewSource {
  return async function* (turns: RoledTurn[], signal: AbortSignal): AsyncGenerator<ReviewEvent> {
    const comments = MOCK_REVIEWS[sampleId]
    if (!comments) {
      if (!signal.aborted) yield { type: 'error', message: "Agent review isn't available for this transcript yet." }
      return
    }
    for (const turn of turns) {
      if (turn.role !== 'doctor') continue
      if (signal.aborted) return
      yield { type: 'turn_started', turn_id: turn.id }
      try {
        await wait(delayMs, signal)
      } catch {
        return
      }
      for (const mock of comments) {
        if (mock.turn_id !== turn.id) continue
        if (signal.aborted) return
        yield { type: 'comment', comment: toReviewComment(mock, turn) }
      }
      if (signal.aborted) return
      yield { type: 'turn_done', turn_id: turn.id }
    }
    if (!signal.aborted) yield { type: 'done' }
  }
}

function clarificationFor(commentId: string): string | null {
  for (const comments of Object.values(MOCK_REVIEWS)) {
    const found = comments.find((c) => c.id === commentId)
    if (found) return found.clarification
  }
  return null
}

/**
 * The mock agent's answer to a reply (a ClarifySource): after delayMs, the comment's canned clarification, or a
 * generic one if it has none. The question is ignored. Rejects with an AbortError when the signal aborts.
 */
export async function mockClarify(
  comment: Pick<ReviewComment, 'id'>,
  _question: string,
  signal: AbortSignal,
  { delayMs = DEFAULT_CLARIFY_DELAY_MS }: { delayMs?: number } = {},
): Promise<string> {
  await wait(delayMs, signal)
  return clarificationFor(comment.id) ?? FALLBACK_CLARIFICATION
}
