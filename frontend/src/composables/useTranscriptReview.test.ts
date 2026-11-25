import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { REVIEW_EXPORT_NOTICE } from '@/lib/review'
import { mockClarify, mockReviewSource } from '@/mocks/review'
import crcImport from '@/mocks/review/__fixtures__/crc_adenocarcinoma.import.json'
import type { ClarifySource, ReviewComment, ReviewEvent, ReviewSource, RoledTurn, TranscriptImport } from '@/types'
import { REPLY_ERROR_MESSAGE, REVIEW_ERROR_MESSAGE, useTranscriptReview } from './useTranscriptReview'

function crcTurns(): RoledTurn[] {
  // The saved backend import of the sample; roles from the importer's suggestions (S1 doctor, S2 patient).
  const imp = crcImport as TranscriptImport
  const roles = new Map(imp.speakers.map((s) => [s.id, s.suggested_role]))
  return imp.turns.map((t) => ({ ...t, role: roles.get(t.speaker) ?? null }))
}

const turn = (id: string): RoledTurn => ({
  id,
  speaker: 'S1',
  start: null,
  end: null,
  text: 'text',
  is_final: true,
  words: [],
  min_confidence: null,
  role: 'doctor',
})

const comment = (id: string, over: Partial<ReviewComment> = {}): ReviewComment => ({
  id,
  turn_id: 't1',
  quote: 'q',
  start: 0,
  end: 1,
  verdict: 'inaccurate',
  issue: 'issue',
  suggestion: 'suggestion',
  source: 'src',
  ...over,
})

/** A source that yields the given events at once. */
function listSource(events: ReviewEvent[]): ReviewSource {
  return async function* () {
    for (const e of events) yield e
  }
}

/** A source the test drives: push() delivers an event, end() finishes. It ignores the signal, like a slow server. */
function manualSource() {
  const queue: (ReviewEvent | null)[] = []
  let wake: (() => void) | null = null
  const signals: AbortSignal[] = []
  const source: ReviewSource = async function* (_turns, signal) {
    signals.push(signal)
    for (;;) {
      while (queue.length === 0) await new Promise<void>((r) => (wake = r))
      const e = queue.shift()!
      if (e === null) return
      yield e
    }
  }
  const flush = () => new Promise((r) => setTimeout(r, 0))
  return {
    source,
    signals,
    async push(...events: ReviewEvent[]) {
      queue.push(...events)
      wake?.()
      await flush()
    },
    async end() {
      queue.push(null)
      wake?.()
      await flush()
    },
  }
}

const tick = () => new Promise((r) => setTimeout(r, 0))

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useTranscriptReview with the crc mock', () => {
  beforeEach(() => vi.useFakeTimers())

  it('streams comments turn by turn, then finishes', async () => {
    const turns = crcTurns()
    const r = useTranscriptReview({ source: mockReviewSource('crc_adenocarcinoma', { delayMs: 100 }) })
    const done = r.start(turns, { sampleId: 'crc_adenocarcinoma' })
    expect(r.status.value).toBe('running')

    await vi.advanceTimersByTimeAsync(0)
    expect(r.currentTurnId.value).toBe('t0001')
    expect(r.comments.value).toHaveLength(0)

    // t0001, t0003 have no comments; t0005 has crc-1 (accurate).
    await vi.advanceTimersByTimeAsync(100)
    expect(r.doneTurnIds.value).toEqual(['t0001'])
    expect(r.currentTurnId.value).toBe('t0003')
    await vi.advanceTimersByTimeAsync(200)
    expect(r.comments.value.map((c) => c.id)).toEqual(['crc-1'])
    expect(r.currentTurnId.value).toBe('t0007')
    await vi.advanceTimersByTimeAsync(100)
    expect(r.comments.value.map((c) => c.id)).toEqual(['crc-1', 'crc-2', 'crc-3'])

    await vi.advanceTimersByTimeAsync(10_000)
    await done
    expect(r.status.value).toBe('done')
    expect(r.currentTurnId.value).toBeNull()
    expect(r.doneTurnIds.value).toHaveLength(9)
    expect(r.comments.value).toHaveLength(8)
    expect(r.comments.value.every((c) => c.status === 'open' && c.thread.length === 0)).toBe(true)
    expect(r.counts.value).toEqual({ open: 6, resolved: 0, disagreed: 0 })
    // crc-8 is the orphan in the last doctor turn with comments; orphans sort first only within their turn.
    expect(r.issueOrder.value).toEqual(['crc-2', 'crc-3', 'crc-4', 'crc-6', 'crc-7', 'crc-8'])
    expect(r.comments.value.find((c) => c.id === 'crc-8')!.start).toBeNull()
  })

  it('stop mid-run keeps the partial results and ignores later timers', async () => {
    const r = useTranscriptReview({ source: mockReviewSource('crc_adenocarcinoma', { delayMs: 100 }) })
    const done = r.start(crcTurns(), { sampleId: 'crc_adenocarcinoma' })
    await vi.advanceTimersByTimeAsync(450)
    const partial = r.comments.value.map((c) => c.id)
    expect(partial).toEqual(['crc-1', 'crc-2', 'crc-3'])
    r.stop()
    expect(r.status.value).toBe('stopped')
    expect(r.currentTurnId.value).toBeNull()
    await vi.advanceTimersByTimeAsync(10_000)
    await done
    expect(r.status.value).toBe('stopped')
    expect(r.comments.value.map((c) => c.id)).toEqual(partial)
  })

  it('reply shows a pending agent message, then the canned answer', async () => {
    const clarify: ClarifySource = (c, q, s) => mockClarify(c, q, s, { delayMs: 800 })
    const r = useTranscriptReview({ source: mockReviewSource('crc_adenocarcinoma', { delayMs: 0 }), clarify })
    const done = r.start(crcTurns(), { sampleId: 'crc_adenocarcinoma' })
    await vi.advanceTimersByTimeAsync(100)
    await done

    const replied = r.reply('crc-2', '  Why?  ')
    const thread = () => r.comments.value.find((c) => c.id === 'crc-2')!.thread
    expect(thread().map(({ author, text, pending }) => ({ author, text, pending }))).toEqual([
      { author: 'you', text: 'Why?', pending: false },
      { author: 'agent', text: '', pending: true },
    ])
    await vi.advanceTimersByTimeAsync(800)
    await replied
    expect(thread()[1]!.pending).toBe(false)
    expect(thread()[1]!.text.length).toBeGreaterThan(0)
    expect(thread()[1]!.text).not.toBe(REPLY_ERROR_MESSAGE)
  })

  it('reset aborts a pending reply and leaves nothing pending', async () => {
    const clarify: ClarifySource = (c, q, s) => mockClarify(c, q, s, { delayMs: 800 })
    const r = useTranscriptReview({ source: mockReviewSource('crc_adenocarcinoma', { delayMs: 0 }), clarify })
    const done = r.start(crcTurns(), { sampleId: 'crc_adenocarcinoma' })
    await vi.advanceTimersByTimeAsync(100)
    await done
    const replied = r.reply('crc-2', 'Why?')
    r.reset()
    await vi.advanceTimersByTimeAsync(1000)
    await replied
    expect(r.comments.value).toEqual([])
    expect(r.status.value).toBe('idle')
  })

  it('a reply works while the review is still running', async () => {
    const clarify: ClarifySource = (c, q, s) => mockClarify(c, q, s, { delayMs: 50 })
    const r = useTranscriptReview({ source: mockReviewSource('crc_adenocarcinoma', { delayMs: 100 }), clarify })
    void r.start(crcTurns(), { sampleId: 'crc_adenocarcinoma' })
    await vi.advanceTimersByTimeAsync(400)
    expect(r.status.value).toBe('running')
    const replied = r.reply('crc-2', 'Why?')
    await vi.advanceTimersByTimeAsync(50)
    await replied
    expect(r.comments.value.find((c) => c.id === 'crc-2')!.thread[1]!.pending).toBe(false)
    expect(r.status.value).toBe('running')
  })
})

describe('useTranscriptReview run lifecycle', () => {
  it('starts idle and empty', () => {
    const r = useTranscriptReview()
    expect(r.status.value).toBe('idle')
    expect(r.comments.value).toEqual([])
    expect(r.hasResults.value).toBe(false)
    expect(r.counts.value).toEqual({ open: 0, resolved: 0, disagreed: 0 })
  })

  it('an error event ends the run with its message and keeps comments', async () => {
    const r = useTranscriptReview({
      source: listSource([
        { type: 'turn_started', turn_id: 't1' },
        { type: 'comment', comment: comment('a') },
        { type: 'error', message: 'Agent failed.' },
      ]),
    })
    await r.start([turn('t1')], { sampleId: null })
    expect(r.status.value).toBe('error')
    expect(r.errorMessage.value).toBe('Agent failed.')
    expect(r.currentTurnId.value).toBeNull()
    expect(r.comments.value.map((c) => c.id)).toEqual(['a'])
  })

  it('a thrown error ends the run with a generic message', async () => {
    const source: ReviewSource = async function* () {
      yield { type: 'turn_started', turn_id: 't1' }
      throw new Error('boom')
    }
    const r = useTranscriptReview({ source })
    await r.start([turn('t1')], { sampleId: null })
    expect(r.status.value).toBe('error')
    expect(r.errorMessage.value).toBe(REVIEW_ERROR_MESSAGE)
  })

  it('errors without a source', async () => {
    const r = useTranscriptReview()
    await r.start([turn('t1')], { sampleId: null })
    expect(r.status.value).toBe('error')
  })

  it('reset aborts the run and late events from a slow source change nothing', async () => {
    const m = manualSource()
    const r = useTranscriptReview({ source: m.source })
    const done = r.start([turn('t1')], { sampleId: 'x' })
    await m.push({ type: 'turn_started', turn_id: 't1' }, { type: 'comment', comment: comment('a') })
    expect(r.hasResults.value).toBe(true)

    r.reset()
    expect(m.signals[0]!.aborted).toBe(true)
    await m.push({ type: 'comment', comment: comment('late') }, { type: 'turn_done', turn_id: 't1' }, { type: 'done' })
    await done
    expect(r.status.value).toBe('idle')
    expect(r.comments.value).toEqual([])
    expect(r.doneTurnIds.value).toEqual([])
    expect(r.currentTurnId.value).toBeNull()
    expect(r.sampleId.value).toBeNull()
  })

  it('a restart replaces the previous results and ignores the old run', async () => {
    const first = manualSource()
    const r = useTranscriptReview({ source: first.source })
    const done1 = r.start([turn('t1')], { sampleId: 'x' })
    await first.push({ type: 'comment', comment: comment('old') })
    r.resolve('old')

    const done2 = r.start([turn('t1')], {
      sampleId: 'y',
      source: listSource([{ type: 'comment', comment: comment('new') }, { type: 'done' }]),
    })
    await done2
    await first.push({ type: 'comment', comment: comment('late') }, { type: 'done' })
    await done1
    expect(first.signals[0]!.aborted).toBe(true)
    expect(r.comments.value.map((c) => [c.id, c.status])).toEqual([['new', 'open']])
    expect(r.status.value).toBe('done')
    expect(r.sampleId.value).toBe('y')
  })

  it('aborts when the owning component unmounts', async () => {
    const m = manualSource()
    let review!: ReturnType<typeof useTranscriptReview>
    const wrapper = mount(
      defineComponent({
        setup() {
          review = useTranscriptReview({ source: m.source })
          return () => h('div')
        },
      }),
    )
    void review.start([turn('t1')], { sampleId: null })
    await tick()
    wrapper.unmount()
    expect(m.signals[0]!.aborted).toBe(true)
  })
})

describe('useTranscriptReview comment actions', () => {
  async function withComments(events: ReviewEvent[], clarify?: ClarifySource) {
    const r = useTranscriptReview({ source: listSource([...events, { type: 'done' }]), clarify })
    await r.start(
      ['t1', 't2', 't3'].map((id) => turn(id)),
      { sampleId: 'crc_adenocarcinoma' },
    )
    return r
  }
  const c = (cm: ReviewComment): ReviewEvent => ({ type: 'comment', comment: cm })
  const byId = (r: ReturnType<typeof useTranscriptReview>, id: string) => r.comments.value.find((x) => x.id === id)!

  it('resolve, reopen and disagree (reason trimmed, blank is null)', async () => {
    const r = await withComments([c(comment('a'))])
    r.resolve('a')
    expect(byId(r, 'a').status).toBe('resolved')
    r.reopen('a')
    expect(byId(r, 'a').status).toBe('open')

    r.disagree('a', '  Source is outdated.  ')
    expect(byId(r, 'a')).toMatchObject({ status: 'disagreed', disagreeReason: 'Source is outdated.' })
    r.reopen('a')
    expect(byId(r, 'a')).toMatchObject({ status: 'open', disagreeReason: null })
    r.disagree('a', '   ')
    expect(byId(r, 'a')).toMatchObject({ status: 'disagreed', disagreeReason: null })
    r.disagree('a')
    expect(byId(r, 'a').disagreeReason).toBeNull()
  })

  it('counts exclude accurate comments', async () => {
    const r = await withComments([
      c(comment('ok', { verdict: 'accurate', issue: null, suggestion: null })),
      c(comment('a')),
      c(comment('b', { verdict: 'incomplete' })),
      c(comment('d')),
    ])
    r.resolve('a')
    r.disagree('b', 'no')
    r.resolve('ok')
    expect(r.counts.value).toEqual({ open: 1, resolved: 1, disagreed: 1 })
  })

  it('setActive only accepts known ids or null', async () => {
    const r = await withComments([c(comment('a'))])
    r.setActive('a')
    expect(r.activeCommentId.value).toBe('a')
    r.setActive('missing')
    expect(r.activeCommentId.value).toBeNull()
  })

  it('a failed reply becomes an error message; a blank reply is ignored', async () => {
    const r = await withComments([c(comment('a'))], () => Promise.reject(new Error('down')))
    await r.reply('a', '   ')
    expect(byId(r, 'a').thread).toEqual([])
    await r.reply('a', 'Why?')
    expect(byId(r, 'a').thread.map((m) => [m.author, m.text, m.pending])).toEqual([
      ['you', 'Why?', false],
      ['agent', REPLY_ERROR_MESSAGE, false],
    ])
  })

  it('passes the plain comment to clarify', async () => {
    const clarify = vi.fn<ClarifySource>(async () => 'answer')
    const r = await withComments([c(comment('a'))], clarify)
    await r.reply('a', 'Why?')
    expect(clarify.mock.calls[0]![0]).toEqual(comment('a'))
    expect(clarify.mock.calls[0]![1]).toBe('Why?')
  })

  it('issueOrder is transcript order (orphans first in a turn); next/prev cycle over open issues', async () => {
    const r = await withComments([
      c(comment('t2-late', { turn_id: 't2', start: 20, end: 25 })),
      c(comment('t1-ok', { turn_id: 't1', verdict: 'accurate', start: 0, end: 3 })),
      c(comment('t1-b', { turn_id: 't1', start: 10, end: 12 })),
      c(comment('t2-orphan', { turn_id: 't2', start: null, end: null })),
      c(comment('t1-a', { turn_id: 't1', start: 4, end: 8 })),
      c(comment('t3', { turn_id: 't3', start: 0, end: 2 })),
    ])
    expect(r.issueOrder.value).toEqual(['t1-a', 't1-b', 't2-orphan', 't2-late', 't3'])

    r.resolve('t1-b')
    r.disagree('t2-late')
    expect(r.nextIssue()).toBe('t1-a')
    expect(r.activeCommentId.value).toBe('t1-a')
    expect(r.nextIssue()).toBe('t2-orphan')
    expect(r.nextIssue()).toBe('t3')
    expect(r.nextIssue()).toBe('t1-a') // wraps
    expect(r.prevIssue()).toBe('t3') // wraps back
    expect(r.prevIssue()).toBe('t2-orphan')

    // From a resolved (or accurate) active comment, navigation continues from its position.
    r.setActive('t1-b')
    expect(r.nextIssue()).toBe('t2-orphan')
    r.setActive(null)
    expect(r.prevIssue()).toBe('t3')

    for (const id of ['t1-a', 't2-orphan', 't3']) r.resolve(id)
    expect(r.nextIssue()).toBeNull()
    expect(r.prevIssue()).toBeNull()
  })

  it('exports comments with statuses and threads, but no pending messages', async () => {
    let answer!: (text: string) => void
    const r = await withComments(
      [c(comment('a')), c(comment('b', { verdict: 'accurate', issue: null, suggestion: null, source: null }))],
      () => new Promise<string>((res) => (answer = res)),
    )
    const first = r.reply('a', 'Why?')
    answer('Because.')
    await first
    void r.reply('a', 'And?') // left pending
    r.disagree('a', ' outdated ')

    const now = new Date(2026, 10, 25, 9, 30)
    const { filename, data } = r.exportReview({ sampleId: 'crc_adenocarcinoma', now })
    expect(filename).toBe('review-crc_adenocarcinoma-20261125.json')
    expect(data).toEqual({
      notice: REVIEW_EXPORT_NOTICE,
      source: 'mock',
      sample_id: 'crc_adenocarcinoma',
      exported_at: now.toISOString(),
      comments: [
        {
          id: 'a',
          turn_id: 't1',
          quote: 'q',
          verdict: 'inaccurate',
          issue: 'issue',
          suggestion: 'suggestion',
          source: 'src',
          status: 'disagreed',
          disagree_reason: 'outdated',
          thread: [
            { author: 'you', text: 'Why?' },
            { author: 'agent', text: 'Because.' },
            { author: 'you', text: 'And?' },
          ],
        },
        {
          id: 'b',
          turn_id: 't1',
          quote: 'q',
          verdict: 'accurate',
          issue: null,
          suggestion: null,
          source: null,
          status: 'open',
          disagree_reason: null,
          thread: [],
        },
      ],
    })
    // Defaults to the sample of the last start(); null becomes "transcript".
    expect(r.exportReview({ now }).filename).toBe('review-crc_adenocarcinoma-20261125.json')
    expect(r.exportReview({ sampleId: null, now }).filename).toBe('review-transcript-20261125.json')
  })
})

describe('useTranscriptReview storage', () => {
  it('never writes to localStorage or sessionStorage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    vi.useFakeTimers()
    const clarify: ClarifySource = (c, q, s) => mockClarify(c, q, s, { delayMs: 0 })
    const r = useTranscriptReview({ source: mockReviewSource('crc_adenocarcinoma', { delayMs: 0 }), clarify })
    const done = r.start(crcTurns(), { sampleId: 'crc_adenocarcinoma' })
    await vi.advanceTimersByTimeAsync(100)
    await done
    r.resolve('crc-2')
    r.disagree('crc-3', 'x')
    const replied = r.reply('crc-4', 'Why?')
    await vi.advanceTimersByTimeAsync(10)
    await replied
    r.nextIssue()
    r.exportReview()
    r.reset()
    expect(setItem).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
