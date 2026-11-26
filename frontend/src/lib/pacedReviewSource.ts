import type { ReviewEvent, ReviewSource, RoledTurn } from '@/types'
import { MARKER_TRANSITION_MS, READING_PAUSE_MS, animationMinimumMs } from './reviewMotion'
import { wait } from './wait'

export interface PaceOptions {
  /**
   * How long a turn with comments stays "Reviewing…" at least, in ms (raised to its animations plus a reading pause
   * when that's longer). 0 passes events straight through.
   */
  turnMs?: number
  /**
   * Cap on the whole reveal: the dwell shrinks towards maxTotalMs / number of doctor turns, but never below the
   * turn's animation minimum (only the reading pause is cut).
   */
  maxTotalMs?: number
  /** Dwell of a turn that produced no comment (only skips): enough for the marker to visibly land on it. */
  quietTurnMs?: number
}

export const DEFAULT_PACE = { turnMs: 900, maxTotalMs: 15_000, quietTurnMs: 350 } as const

/**
 * How long a turn stays on screen. With n comments: max(turnMs, animations + reading pause), cut to the cap's share
 * but never below the animations. Without: quietTurnMs, cut to the share but never below the marker transition.
 */
export function dwellMs(comments: number, share: number, { turnMs, quietTurnMs }: { turnMs: number; quietTurnMs: number }) {
  if (comments <= 0) return Math.max(MARKER_TRANSITION_MS, Math.min(quietTurnMs, share))
  const minimum = animationMinimumMs(comments)
  const full = Math.max(turnMs, minimum + READING_PAUSE_MS)
  return Math.max(minimum, Math.min(full, share))
}

/** Turns the inner source will review: the doctor turns, or every turn when none is marked doctor. */
function turnCount(turns: RoledTurn[]): number {
  return turns.filter((t) => t.role === 'doctor').length || turns.length
}

/**
 * Paces any ReviewSource for display (issue #39): Jev answers every turn within a second, so without this every
 * highlight and card would land at once. The inner source is read eagerly at full speed into a queue; each turn's
 * turn_started and comments are yielded as soon as they arrive, and its turn_done is held until the turn has been on
 * screen for its dwell (dwellMs): never shorter than its own highlight and card animations, so the next turn can't
 * start before they have played.
 *
 * Pacing never adds latency beyond the dwell (a slow inner source passes straight through) and never reorders.
 * `error` and `done` wait only for the rest of the open turn's dwell. Abort, or the consumer breaking out, ends it at
 * once with no timer left and stops the inner iterator.
 */
export function pacedReviewSource(source: ReviewSource, options: PaceOptions = {}): ReviewSource {
  const { turnMs, maxTotalMs, quietTurnMs } = { ...DEFAULT_PACE, ...options }
  if (turnMs <= 0) return source

  return async function* (turns: RoledTurn[], signal: AbortSignal): AsyncGenerator<ReviewEvent> {
    if (signal.aborted) return
    const share = maxTotalMs / Math.max(1, turnCount(turns))
    const inner = source(turns, signal)[Symbol.asyncIterator]()

    const queue: ReviewEvent[] = []
    let ended = false
    let failure: { error: unknown } | null = null
    let wake: (() => void) | null = null
    const notify = () => {
      const w = wake
      wake = null
      w?.()
    }
    signal.addEventListener('abort', notify)

    // Eager pump: drain the inner source at its own speed.
    void (async () => {
      try {
        for (;;) {
          const result = await inner.next()
          if (result.done || signal.aborted) break
          queue.push(result.value)
          notify()
        }
      } catch (error) {
        failure = { error }
      } finally {
        ended = true
        notify()
      }
    })()

    /** The next queued event, or null once the inner source has ended (or the signal aborted). */
    async function next(): Promise<ReviewEvent | null> {
      while (!queue.length && !ended && !signal.aborted) await new Promise<void>((resolve) => (wake = resolve))
      if (signal.aborted) return null
      if (queue.length) return queue.shift()!
      if (failure) throw failure.error
      return null
    }

    /** Started at (Date.now()) of the open turn, and how many comments it has produced. */
    let openSince: number | null = null
    let comments = 0

    /** Waits out the open turn's remaining dwell. False if the signal aborted meanwhile. */
    async function finishDwell(): Promise<boolean> {
      if (openSince === null) return true
      const remaining = dwellMs(comments, share, { turnMs, quietTurnMs }) - (Date.now() - openSince)
      openSince = null
      if (remaining > 0) {
        try {
          await wait(remaining, signal)
        } catch {
          return false
        }
      }
      return !signal.aborted
    }

    try {
      for (;;) {
        let event: ReviewEvent | null
        try {
          event = await next()
        } catch (error) {
          if (!(await finishDwell())) return
          throw error
        }
        if (!event || signal.aborted) {
          if (event === null && !signal.aborted) await finishDwell()
          return
        }
        switch (event.type) {
          case 'turn_started':
            openSince = Date.now()
            comments = 0
            break
          case 'comment':
            comments++
            break
          case 'turn_done':
          case 'error':
          case 'done':
            if (!(await finishDwell())) return
            break
        }
        yield event
        if (signal.aborted) return
      }
    } finally {
      signal.removeEventListener('abort', notify)
      void Promise.resolve(inner.return?.()).catch(() => {})
    }
  }
}
