// @vitest-environment node
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ClarifySource, ReviewEvent, ReviewSource, RoledTurn, TranscriptImport } from '@/types'
import { anchorComment } from '@/lib/review'
import { MOCK_NOTICE, MOCK_REVIEWS, hasMockReview, mockClarify, mockReviewSource } from './index'

// The mocks must stay usable as the contract types.
const _clarify: ClarifySource = mockClarify
const _source: ReviewSource = mockReviewSource('melanoma')
void _clarify
void _source

const SAMPLES = ['crc_adenocarcinoma', 'breast_idc', 'melanoma'] as const

/** Saved backend imports of the bundled samples (backend/app/transcripts/service.py::import_transcript). */
function loadImport(id: string): TranscriptImport {
  const path = fileURLToPath(new URL(`./__fixtures__/${id}.import.json`, import.meta.url))
  return JSON.parse(readFileSync(path, 'utf8'))
}

/** Turns with roles from the importer's suggestions, as they'd be after confirming defaults. */
function roledTurns(id: string): RoledTurn[] {
  const imp = loadImport(id)
  const roles = new Map(imp.speakers.map((s) => [s.id, s.suggested_role]))
  return imp.turns.map((t) => ({ ...t, role: roles.get(t.speaker) ?? null }))
}

async function collect(source: ReviewSource, turns: RoledTurn[], signal = new AbortController().signal) {
  const events: ReviewEvent[] = []
  for await (const e of source(turns, signal)) events.push(e)
  return events
}

afterEach(() => vi.useRealTimers())

describe.each(SAMPLES)('mock review for %s', (id) => {
  const turns = roledTurns(id)
  const byId = new Map(turns.map((t) => [t.id, t]))
  const comments = MOCK_REVIEWS[id]!
  const orphans = comments.filter((c) => !anchorComment(byId.get(c.turn_id)!, c.quote))

  it('is available', () => expect(hasMockReview(id)).toBe(true))

  it('only targets existing doctor turns', () => {
    for (const c of comments) expect(byId.get(c.turn_id)?.role, c.id).toBe('doctor')
  })

  it('anchors every quote except exactly one orphan', () => {
    expect(orphans).toHaveLength(1)
    for (const c of comments) {
      if (orphans.includes(c)) continue
      const turn = byId.get(c.turn_id)!
      const a = anchorComment(turn, c.quote)!
      expect(turn.text.slice(a.start, a.end), c.id).toBe(c.quote)
    }
  })

  it('meets the verdict minimums', () => {
    const count = (v: string) => comments.filter((c) => c.verdict === v).length
    expect(count('accurate')).toBeGreaterThanOrEqual(1)
    expect(count('accurate')).toBeLessThanOrEqual(2)
    expect(count('incomplete')).toBeGreaterThanOrEqual(2)
    expect(count('inaccurate')).toBeGreaterThanOrEqual(2)
  })

  it('has exactly one pair of overlapping spans, in the same turn', () => {
    const anchored = comments
      .map((c) => ({ c, a: anchorComment(byId.get(c.turn_id)!, c.quote) }))
      .filter((x) => x.a !== null)
    const pairs: string[] = []
    for (let i = 0; i < anchored.length; i++) {
      for (let j = i + 1; j < anchored.length; j++) {
        const x = anchored[i]!
        const y = anchored[j]!
        if (x.c.turn_id === y.c.turn_id && x.a!.start < y.a!.end && y.a!.start < x.a!.end) pairs.push(`${x.c.id}/${y.c.id}`)
      }
    }
    expect(pairs).toHaveLength(1)
  })

  it('has well-formed comments', () => {
    for (const c of comments) {
      expect(c.source, c.id).toBeTruthy()
      if (c.verdict === 'accurate') {
        expect([c.issue, c.suggestion, c.clarification], c.id).toEqual([null, null, null])
      } else {
        expect(c.issue && c.suggestion && c.clarification, c.id).toBeTruthy()
      }
    }
  })

  it('streams turn_started, comments, turn_done per doctor turn, then done', async () => {
    const events = await collect(mockReviewSource(id, { delayMs: 0 }), turns)
    const expected: ReviewEvent['type'][] = []
    for (const t of turns.filter((t) => t.role === 'doctor')) {
      expected.push('turn_started', ...comments.filter((c) => c.turn_id === t.id).map(() => 'comment' as const), 'turn_done')
    }
    expected.push('done')
    expect(events.map((e) => e.type)).toEqual(expected)

    const emitted = events.flatMap((e) => (e.type === 'comment' ? [e.comment] : []))
    expect(emitted.map((c) => c.id)).toEqual(
      turns.flatMap((t) => comments.filter((c) => c.turn_id === t.id)).map((c) => c.id),
    )
    for (const c of emitted) {
      expect(c).not.toHaveProperty('clarification')
      const orphan = orphans.some((o) => o.id === c.id)
      if (orphan) expect([c.start, c.end]).toEqual([null, null])
      else expect(byId.get(c.turn_id)!.text.slice(c.start!, c.end!)).toBe(c.quote)
    }
  })
})

describe('mock review registry', () => {
  it('has unique comment ids across samples and a notice', () => {
    const ids = Object.values(MOCK_REVIEWS).flat().map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(MOCK_NOTICE).toMatch(/not reviewed by a pathologist/)
  })

  it('has no mock for other samples or uploads', () => {
    for (const id of ['fibroadenoma', 'toString', '', null, undefined]) expect(hasMockReview(id)).toBe(false)
  })

  it('yields a single error for a sample without a mock', async () => {
    const events = await collect(mockReviewSource('fibroadenoma', { delayMs: 0 }), roledTurns('melanoma'))
    expect(events).toEqual([{ type: 'error', message: expect.any(String) }])
  })
})

describe('mockReviewSource timing and abort', () => {
  const turns = roledTurns('melanoma')
  const doctorTurns = turns.filter((t) => t.role === 'doctor')

  it('skips patient turns and turns with no role', async () => {
    const unroled = turns.map((t) => ({ ...t, role: t.role === 'doctor' ? ('doctor' as const) : null }))
    const events = await collect(mockReviewSource('melanoma', { delayMs: 0 }), unroled)
    const started = events.flatMap((e) => (e.type === 'turn_started' ? [e.turn_id] : []))
    expect(started).toEqual(doctorTurns.map((t) => t.id))
  })

  it('waits delayMs per turn before its comments (default 900 ms)', async () => {
    vi.useFakeTimers()
    const it = mockReviewSource('melanoma')(turns, new AbortController().signal)[Symbol.asyncIterator]()
    expect(await it.next()).toEqual({ done: false, value: { type: 'turn_started', turn_id: doctorTurns[0]!.id } })
    let settled = false
    const next = it.next().then((r) => {
      settled = true
      return r
    })
    await vi.advanceTimersByTimeAsync(899)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect((await next).value).toEqual({ type: 'turn_done', turn_id: doctorTurns[0]!.id })
  })

  it('stops with no further events when aborted mid-delay', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const events: ReviewEvent[] = []
    const run = (async () => {
      for await (const e of mockReviewSource('melanoma', { delayMs: 1000 })(turns, controller.signal)) events.push(e)
    })()
    await vi.advanceTimersByTimeAsync(1000) // first turn done, second turn waiting
    await vi.advanceTimersByTimeAsync(500)
    controller.abort()
    await vi.advanceTimersByTimeAsync(10_000)
    await run
    expect(events.map((e) => e.type)).toEqual(['turn_started', 'turn_done', 'turn_started'])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops with no further events when aborted between comments', async () => {
    const controller = new AbortController()
    const events: ReviewEvent[] = []
    for await (const e of mockReviewSource('melanoma', { delayMs: 0 })(turns, controller.signal)) {
      events.push(e)
      if (e.type === 'comment') controller.abort()
    }
    const types = events.map((e) => e.type)
    expect(types.filter((t) => t === 'comment')).toHaveLength(1)
    expect(types.at(-1)).toBe('comment')
  })

  it('emits nothing when already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    expect(await collect(mockReviewSource('melanoma', { delayMs: 0 }), turns, controller.signal)).toEqual([])
  })
})

describe('mockClarify', () => {
  const comment = { id: 'mel-3' }

  it('answers with the canned clarification after ~800 ms', async () => {
    vi.useFakeTimers()
    let answer: string | undefined
    void mockClarify(comment, 'Why?', new AbortController().signal).then((a) => (answer = a))
    await vi.advanceTimersByTimeAsync(799)
    expect(answer).toBeUndefined()
    await vi.advanceTimersByTimeAsync(1)
    expect(answer).toBe(MOCK_REVIEWS.melanoma!.find((c) => c.id === 'mel-3')!.clarification)
  })

  it('takes a configurable delay and falls back to a generic answer', async () => {
    const answer = await mockClarify({ id: 'mel-1' }, 'Why?', new AbortController().signal, { delayMs: 0 })
    expect(answer).toMatch(/demo review/i)
    expect(await mockClarify({ id: 'nope' }, 'Why?', new AbortController().signal, { delayMs: 0 })).toBe(answer)
  })

  it('rejects with an AbortError when aborted', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const pending = mockClarify(comment, 'Why?', controller.signal)
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(vi.getTimerCount()).toBe(0)
    const already = mockClarify(comment, 'Why?', controller.signal)
    await expect(already).rejects.toMatchObject({ name: 'AbortError' })
  })
})
