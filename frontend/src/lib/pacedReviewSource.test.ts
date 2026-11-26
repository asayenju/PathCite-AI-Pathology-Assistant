import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReviewComment, ReviewEvent, ReviewSource, Role, RoledTurn } from '@/types'
import { pacedReviewSource } from './pacedReviewSource'
import { MARKER_TRANSITION_MS, animationMinimumMs } from './reviewMotion'

function turn(id: string, role: Role | null = 'doctor'): RoledTurn {
  return { id, speaker: 'S1', start: null, end: null, text: 'Some text.', is_final: true, words: [], min_confidence: null, role }
}

const turns = (n: number) => Array.from({ length: n }, (_, i) => turn(`t${i + 1}`))

function comment(turnId: string, n = 0): ReviewComment {
  return {
    id: `${turnId}:${n}`,
    turn_id: turnId,
    quote: 'Some',
    start: 0,
    end: 4,
    verdict: 'claim',
    issue: null,
    suggestion: null,
    source: null,
  } as ReviewComment
}

const started = (id: string): ReviewEvent => ({ type: 'turn_started', turn_id: id })
const done = (id: string): ReviewEvent => ({ type: 'turn_done', turn_id: id })
const commented = (id: string, n = 0): ReviewEvent => ({ type: 'comment', comment: comment(id, n) })
const skipped = (id: string): ReviewEvent => ({ type: 'span_skipped', turn_id: id, start: 0, end: 4 })

/** An inner source that yields all its events at once (like Jev: everything within a second). */
function instant(events: ReviewEvent[]): ReviewSource & { returned: () => boolean } {
  let returned = false
  const source = async function* () {
    try {
      for (const e of events) yield e
    } finally {
      returned = true
    }
  }
  return Object.assign(source, { returned: () => returned })
}

/** An inner source the test feeds by hand, to model a slow agent. */
function manual() {
  const queue: ReviewEvent[] = []
  let ended = false
  let returned = false
  let wake: (() => void) | null = null
  const source: ReviewSource = () => ({
    [Symbol.asyncIterator]() {
      return {
        async next(): Promise<IteratorResult<ReviewEvent>> {
          while (!queue.length && !ended) await new Promise<void>((r) => (wake = r))
          if (queue.length) return { done: false, value: queue.shift()! }
          return { done: true, value: undefined }
        },
        async return(): Promise<IteratorResult<ReviewEvent>> {
          returned = true
          ended = true
          wake?.()
          return { done: true, value: undefined }
        },
      }
    },
  })
  return {
    source,
    push(...events: ReviewEvent[]) {
      queue.push(...events)
      wake?.()
    },
    end() {
      ended = true
      wake?.()
    },
    returned: () => returned,
  }
}

/** Consumes a source in the background, logging each event with the fake time it arrived at. */
function record(source: ReviewSource, input: RoledTurn[], signal = new AbortController().signal) {
  const log: { t: number; e: ReviewEvent }[] = []
  const t0 = Date.now()
  const finished = (async () => {
    for await (const e of source(input, signal)) log.push({ t: Date.now() - t0, e })
  })()
  return { log, finished, events: () => log.map((l) => l.e), times: () => log.map((l) => l.t) }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('pacedReviewSource', () => {
  it('keeps a turn with one comment on screen for 900 ms by default, in the same order', async () => {
    const events = [started('t1'), commented('t1'), done('t1'), started('t2'), commented('t2'), done('t2'), { type: 'done' } as ReviewEvent]
    const r = record(pacedReviewSource(instant(events)), turns(2))
    await vi.advanceTimersByTimeAsync(0)
    expect(r.events()).toEqual([started('t1'), commented('t1')])
    await vi.advanceTimersByTimeAsync(899)
    expect(r.events()).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(r.events()).toEqual(events.slice(0, 5))
    await vi.advanceTimersByTimeAsync(900)
    await r.finished
    expect(r.events()).toEqual(events)
    expect(r.times()).toEqual([0, 0, 900, 900, 900, 1800, 1800])
  })

  it('yields all of a turn’s comments right after turn_started, and dwells longer for more cards', async () => {
    // 3 cards: 300 fade + 2 × 120 stagger + 200 slide = 740 ms of animation, + 400 ms reading = 1140 ms.
    const events = [started('t1'), commented('t1', 0), commented('t1', 1), commented('t1', 2), done('t1')]
    const r = record(pacedReviewSource(instant(events)), turns(1))
    await vi.advanceTimersByTimeAsync(0)
    expect(r.events()).toEqual(events.slice(0, 4))
    await vi.advanceTimersByTimeAsync(1140)
    await r.finished
    expect(r.events()).toEqual(events)
    expect(r.times()).toEqual([0, 0, 0, 0, 1140])
  })

  it('gives a turn with no comments (only skips) a 350 ms dwell', async () => {
    const events = [started('t1'), skipped('t1'), done('t1'), started('t2'), done('t2')]
    const r = record(pacedReviewSource(instant(events)), turns(2))
    await vi.advanceTimersByTimeAsync(1000)
    await r.finished
    expect(r.events()).toEqual(events)
    expect(r.times()).toEqual([0, 0, 350, 350, 700])
  })

  it('caps the reveal at maxTotalMs over the doctor turns by cutting the reading pause only', async () => {
    // 40 doctor turns: a 375 ms share, below the 500 ms animation minimum of a one-card turn, so 500 ms wins.
    const input = [...turns(40), turn('p1', 'patient'), turn('p2', 'patient')]
    const events = input.filter((t) => t.role === 'doctor').flatMap((t) => [started(t.id), commented(t.id), done(t.id)])
    const r = record(pacedReviewSource(instant(events)), input)
    await vi.advanceTimersByTimeAsync(30_000)
    await r.finished
    expect(r.events()).toEqual(events)
    expect(r.log.filter((l) => l.e.type === 'turn_done').map((l) => l.t)).toEqual(
      Array.from({ length: 40 }, (_, i) => (i + 1) * 500),
    )
  })

  it('cuts to the share when it lies between the animations and the full dwell', async () => {
    // 20 turns: a 750 ms share, between 500 (animations) and 900 (full).
    const events = turns(20).flatMap((t) => [started(t.id), commented(t.id), done(t.id)])
    const r = record(pacedReviewSource(instant(events)), turns(20))
    await vi.advanceTimersByTimeAsync(30_000)
    await r.finished
    expect(r.log.filter((l) => l.e.type === 'turn_done')[0]!.t).toBe(750)
  })

  it('never starts the next turn before the previous turn’s animations have played, even under the cap', async () => {
    const counts = [1, 3, 0, 5, 2, 0, 1, 4]
    const input = turns(80)
    const events = input.flatMap((t, i) => [
      started(t.id),
      ...Array.from({ length: counts[i % counts.length]! }, (_, n) => commented(t.id, n)),
      done(t.id),
    ])
    for (const options of [{}, { maxTotalMs: 1000 }, { maxTotalMs: 15_000 }]) {
      const r = record(pacedReviewSource(instant(events), options), input)
      await vi.advanceTimersByTimeAsync(200_000)
      await r.finished
      const startedAt = r.log.filter((l) => l.e.type === 'turn_started').map((l) => l.t)
      for (let i = 1; i < startedAt.length; i++) {
        const cards = counts[(i - 1) % counts.length]!
        expect(startedAt[i]! - startedAt[i - 1]!).toBeGreaterThanOrEqual(animationMinimumMs(cards))
        // Quiet turns still let the marker visibly land.
        expect(startedAt[i]! - startedAt[i - 1]!).toBeGreaterThanOrEqual(MARKER_TRANSITION_MS)
      }
    }
  })

  it('takes turnMs, maxTotalMs and quietTurnMs as options', async () => {
    const events = [started('t1'), commented('t1'), done('t1'), started('t2'), done('t2')]
    const r = record(pacedReviewSource(instant(events), { turnMs: 1200, maxTotalMs: 10_000, quietTurnMs: 250 }), turns(2))
    await vi.advanceTimersByTimeAsync(3000)
    await r.finished
    expect(r.times()).toEqual([0, 0, 1200, 1200, 1450])
  })

  it('never lets turnMs go below the animations plus the reading pause', async () => {
    const events = [started('t1'), commented('t1'), done('t1')]
    const r = record(pacedReviewSource(instant(events), { turnMs: 100 }), turns(1))
    await vi.advanceTimersByTimeAsync(3000)
    await r.finished
    expect(r.times()).toEqual([0, 0, 900])
  })

  it('passes events straight through with turnMs 0', async () => {
    const events = [started('t1'), commented('t1'), done('t1'), started('t2'), done('t2'), { type: 'done' } as ReviewEvent]
    const r = record(pacedReviewSource(instant(events), { turnMs: 0 }), turns(2))
    await vi.advanceTimersByTimeAsync(0)
    await r.finished
    expect(r.events()).toEqual(events)
    expect(r.times().every((t) => t === 0)).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('adds no latency when the inner source is slower than the dwell', async () => {
    const m = manual()
    const r = record(pacedReviewSource(m.source), turns(2))
    m.push(started('t1'))
    await vi.advanceTimersByTimeAsync(0)
    expect(r.events()).toEqual([started('t1')])
    await vi.advanceTimersByTimeAsync(1000)
    m.push(commented('t1'), done('t1'))
    await vi.advanceTimersByTimeAsync(0)
    expect(r.events()).toEqual([started('t1'), commented('t1'), done('t1')])
    m.push(started('t2'))
    await vi.advanceTimersByTimeAsync(0)
    expect(r.events()).toHaveLength(4)
    m.end()
    // A stream that ends mid-turn still lets that turn finish its dwell.
    await vi.advanceTimersByTimeAsync(350)
    await r.finished
  })

  it('only waits out the rest of the dwell when the inner source is a little slow', async () => {
    const m = manual()
    const r = record(pacedReviewSource(m.source), turns(1))
    m.push(started('t1'))
    await vi.advanceTimersByTimeAsync(300)
    m.push(commented('t1'), done('t1'))
    await vi.advanceTimersByTimeAsync(0)
    expect(r.events()).toEqual([started('t1'), commented('t1')])
    await vi.advanceTimersByTimeAsync(599)
    expect(r.events()).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(r.events()).toEqual([started('t1'), commented('t1'), done('t1')])
    m.end()
    await r.finished
  })

  it('yields done right after the last turn finishes its dwell', async () => {
    const events = [started('t1'), commented('t1'), done('t1'), { type: 'done', model: 'jev' } as ReviewEvent]
    const r = record(pacedReviewSource(instant(events)), turns(1))
    await vi.advanceTimersByTimeAsync(899)
    expect(r.events()).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    await r.finished
    expect(r.times()).toEqual([0, 0, 900, 900])
  })

  it('does not delay an error beyond the remaining dwell of the turn it arrived in', async () => {
    const error: ReviewEvent = { type: 'error', message: 'Jev failed.' }
    const events = [started('t1'), commented('t1'), done('t1'), started('t2'), error]
    const r = record(pacedReviewSource(instant(events)), turns(3))
    await vi.advanceTimersByTimeAsync(3000)
    await r.finished
    expect(r.events()).toEqual(events)
    // t2 has had no comment yet, so it is a quiet turn: 900 + 350.
    expect(r.times()).toEqual([0, 0, 900, 900, 1250])
  })

  it('yields an error that arrives first straight away', async () => {
    const error: ReviewEvent = { type: 'error', message: 'Jev failed.' }
    const r = record(pacedReviewSource(instant([error])), turns(3))
    await vi.advanceTimersByTimeAsync(0)
    await r.finished
    expect(r.log).toEqual([{ t: 0, e: error }])
  })

  it('rethrows an inner failure after the open turn', async () => {
    const failing: ReviewSource = async function* () {
      yield started('t1')
      throw new Error('boom')
    }
    const r = record(pacedReviewSource(failing), turns(1))
    const caught = r.finished.catch((e: Error) => e.message)
    await vi.advanceTimersByTimeAsync(1000)
    expect(await caught).toBe('boom')
    expect(r.events()).toEqual([started('t1')])
  })

  it('stops at once on abort mid-dwell: nothing more, no timers, inner iterator stopped', async () => {
    const events = [started('t1'), commented('t1'), done('t1'), started('t2'), done('t2'), { type: 'done' } as ReviewEvent]
    const inner = instant(events)
    const controller = new AbortController()
    const r = record(pacedReviewSource(inner), turns(2), controller.signal)
    await vi.advanceTimersByTimeAsync(100)
    expect(r.events()).toHaveLength(2)
    controller.abort()
    await vi.advanceTimersByTimeAsync(0)
    await r.finished
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(5000)
    expect(r.events()).toHaveLength(2)
    expect(inner.returned()).toBe(true)
  })

  it('stops at once on abort while waiting for a slow inner source', async () => {
    const m = manual()
    const controller = new AbortController()
    const r = record(pacedReviewSource(m.source), turns(2), controller.signal)
    m.push(started('t1'))
    await vi.advanceTimersByTimeAsync(1000)
    controller.abort()
    await vi.advanceTimersByTimeAsync(0)
    await r.finished
    m.push(done('t1'))
    await vi.advanceTimersByTimeAsync(1000)
    expect(r.events()).toEqual([started('t1')])
    expect(m.returned()).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('yields nothing when the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    const r = record(pacedReviewSource(instant([started('t1'), done('t1')])), turns(1), controller.signal)
    await vi.advanceTimersByTimeAsync(1000)
    await r.finished
    expect(r.events()).toEqual([])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops the inner iterator and its timers when the consumer breaks out', async () => {
    const events = [started('t1'), commented('t1'), done('t1'), started('t2'), done('t2')]
    const inner = instant(events)
    const seen: ReviewEvent[] = []
    const run = (async () => {
      for await (const e of pacedReviewSource(inner)(turns(2), new AbortController().signal)) {
        seen.push(e)
        if (e.type === 'comment') break
      }
    })()
    await vi.advanceTimersByTimeAsync(0)
    await run
    expect(seen).toEqual([started('t1'), commented('t1')])
    expect(inner.returned()).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
})
