import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReviewMargin from './ReviewMargin.vue'
import { EMPTY_TRIAGE_STATE, type ReviewThread } from '@/composables/useTranscriptReview'
import { CARD_SLIDE_MS } from '@/lib/reviewMotion'
import { stubReducedMotion, unstubMatchMedia } from '@/test-utils/matchMedia'

function thread(id: string, over: Partial<ReviewThread> = {}): ReviewThread {
  return {
    id,
    turn_id: 't0001',
    quote: `quote ${id}`,
    start: 0,
    end: 5,
    verdict: 'inaccurate',
    issue: `issue ${id}`,
    suggestion: null,
    source: null,
    status: 'open',
    disagreeReason: null,
    thread: [],
    ...EMPTY_TRIAGE_STATE,
    ...over,
  }
}

const comments = [
  thread('a'),
  thread('ok', { verdict: 'accurate', issue: null }),
  thread('b', { verdict: 'incomplete' }),
  thread('orphan', { start: null, end: null }),
  thread('done', { status: 'resolved' }),
  thread('nope', { status: 'disagreed', disagreeReason: 'Wrong guideline' }),
]
const order = ['a', 'b', 'orphan', 'done', 'nope']

/** A transcript column with highlight anchors at the given tops (px from the margin's top, which jsdom puts at 0). */
function transcript(anchorTops: Record<string, number>) {
  const el = document.createElement('div')
  for (const [id, top] of Object.entries(anchorTops)) {
    const mark = document.createElement('mark')
    mark.dataset.anchorFor = id
    mark.getBoundingClientRect = () => ({ top, bottom: top + 20, left: 0, right: 0, width: 0, height: 20, x: 0, y: top, toJSON: () => ({}) })
    el.appendChild(mark)
  }
  return el
}

const CARD_HEIGHT = 100
const CONTAINER_HEIGHT = 600

function stubHeights() {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
    if (this.dataset.cardFor) return CARD_HEIGHT
    if (this.dataset.testid === 'container') return CONTAINER_HEIGHT
    return 0
  })
}

function mountMargin(props: Partial<InstanceType<typeof ReviewMargin>['$props']> = {}) {
  return mount(ReviewMargin, {
    props: { comments, order, activeCommentId: null, showResolved: false, container: null, mode: 'list', ...props },
  })
}

const ids = (w: ReturnType<typeof mount>) => w.findAll('[data-card-for]').map((el) => el.attributes('data-card-for'))
const topOf = (w: ReturnType<typeof mount>, id: string) =>
  w.get(`[data-card-for="${id}"]`).attributes('style')?.match(/translateY\((-?[\d.]+)px\)/)?.[1]

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('ReviewMargin', () => {
  it('shows no card for accurate comments and hides resolved/disagreed by default, orphans first', () => {
    const w = mountMargin()
    expect(ids(w)).toEqual(['orphan', 'a', 'b'])
    expect(w.find('#comment-ok').exists()).toBe(false)
  })

  it('shows resolved and disagreed collapsed when showResolved is on', () => {
    const w = mountMargin({ showResolved: true })
    expect(ids(w)).toEqual(['orphan', 'a', 'b', 'done', 'nope'])
    expect(w.get('#comment-done [data-testid="comment-status"]').text()).toBe('Resolved')
    expect(w.get('#comment-nope [data-testid="comment-status"]').text()).toBe('Disagreed')
  })

  it('follows the given transcript order', () => {
    const w = mountMargin({ order: ['b', 'a', 'orphan'] })
    expect(ids(w)).toEqual(['orphan', 'b', 'a'])
  })

  it('renders an ordered list below lg', () => {
    const w = mountMargin({ mode: 'list' })
    expect(w.find('ol[data-testid="review-margin-list"]').exists()).toBe(true)
    expect(w.find('[data-testid="review-margin-positioned"]').exists()).toBe(false)
  })

  it('marks the active card and emits activate', async () => {
    const w = mountMargin({ activeCommentId: 'b' })
    expect(w.get('#comment-b').attributes('aria-current')).toBe('true')
    expect(w.get('#comment-a').attributes('aria-current')).toBeUndefined()
    await w.get('#comment-a').trigger('click')
    expect(w.emitted('activate')).toEqual([['a']])
  })

  it('passes each comment to the actions and thread slots', () => {
    const w = mount(ReviewMargin, {
      props: { comments, order, activeCommentId: null, showResolved: false, container: null, mode: 'list' },
      slots: {
        actions: `<template #actions="{ comment }"><button type="button">Resolve {{ comment.id }}</button></template>`,
        thread: `<template #thread="{ comment }"><p class="t">{{ comment.thread.length }} replies</p></template>`,
      },
    })
    expect(w.get('#comment-a button').text()).toBe('Resolve a')
    expect(w.get('#comment-b .t').text()).toBe('0 replies')
  })

  describe('positioned (lg+)', () => {
    it('places cards next to their anchors and pushes overlaps down, orphan pinned at the top', async () => {
      stubHeights()
      const container = transcript({ a: 50, b: 120 })
      container.dataset.testid = 'container'
      const w = mountMargin({ mode: 'margin', container })
      await flushPromises()
      expect(w.find('[data-testid="review-margin-positioned"]').exists()).toBe(true)
      // orphan 0..100, a wants 50 -> 108..208, b wants 120 -> 216.
      expect(topOf(w, 'orphan')).toBe('0')
      expect(topOf(w, 'a')).toBe('108')
      expect(topOf(w, 'b')).toBe('216')
      expect(w.get('[data-testid="review-margin-positioned"]').attributes('style')).toContain(
        `min-height: ${CONTAINER_HEIGHT}px`,
      )
    })

    it('snaps the active card to its anchor', async () => {
      stubHeights()
      const container = transcript({ a: 300, b: 320 })
      container.dataset.testid = 'container'
      const w = mountMargin({ mode: 'margin', container })
      await flushPromises()
      expect(topOf(w, 'b')).toBe('408')
      await w.setProps({ activeCommentId: 'b' })
      await flushPromises()
      expect(topOf(w, 'b')).toBe('320')
      expect(topOf(w, 'a')).toBe('212')
      expect(topOf(w, 'orphan')).toBe('0')
    })

    it('grows past the transcript when the cards need more room', async () => {
      stubHeights()
      const container = transcript({ a: 550, b: 560 })
      container.dataset.testid = 'container'
      const w = mountMargin({ mode: 'margin', container })
      await flushPromises()
      expect(w.get('[data-testid="review-margin-positioned"]').attributes('style')).toContain('min-height: 758px')
    })

    it('re-lays out when showResolved reveals more cards', async () => {
      stubHeights()
      const container = transcript({ a: 0, b: 0, done: 0, nope: 0 })
      container.dataset.testid = 'container'
      const w = mountMargin({ mode: 'margin', container })
      await flushPromises()
      await w.setProps({ showResolved: true })
      await flushPromises()
      expect(topOf(w, 'nope')).toBe(String(4 * (CARD_HEIGHT + 8)))
    })
  })

  describe('auto mode', () => {
    function mockMatchMedia(wide: boolean) {
      vi.stubGlobal(
        'matchMedia',
        vi.fn((query: string) => ({
          matches: query.includes('min-width') ? wide : false,
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          onchange: null,
          dispatchEvent: () => false,
        })),
      )
    }

    it('positions cards at lg and up', () => {
      mockMatchMedia(true)
      const w = mountMargin({ mode: 'auto' })
      expect(w.find('[data-testid="review-margin-positioned"]').exists()).toBe(true)
    })

    it('lists cards below lg', () => {
      mockMatchMedia(false)
      const w = mountMargin({ mode: 'auto' })
      expect(w.find('[data-testid="review-margin-list"]').exists()).toBe(true)
    })
  })
})

describe('ReviewMargin: card reveal animation (issue #39)', () => {
  afterEach(() => unstubMatchMedia())

  // Two cards in t0001 (plus an accurate comment, which has no card) and one in t0002.
  const staggered = [
    thread('a'),
    thread('ok', { verdict: 'accurate', issue: null }),
    thread('b', { verdict: 'incomplete', start: 10, end: 15 }),
    thread('c', { turn_id: 't0002' }),
  ]
  const staggerOf = (el: { attributes: (name: string) => string | undefined }) =>
    el.attributes('style')?.match(/--review-stagger:\s*(\d+)/)?.[1]
  const frame = () => new Promise((resolve) => setTimeout(resolve, 50))

  it('slides placed cards in on their inner element, staggered by order within the turn', async () => {
    stubHeights()
    const container = transcript({ a: 0, b: 200, c: 400 })
    container.dataset.testid = 'container'
    const w = mountMargin({ comments: staggered, order: ['a', 'b', 'c'], mode: 'margin', container })
    await flushPromises()
    await frame()
    for (const [id, n] of [['a', '0'], ['b', '1'], ['c', '0']] as const) {
      const outer = w.get(`[data-card-for="${id}"]`)
      const inner = outer.get('[data-testid="card-reveal"]')
      expect(inner.classes()).toContain('review-card-in')
      expect(staggerOf(inner)).toBe(n)
      // The positioned wrapper keeps only its translateY: the animation never touches layout.
      expect(outer.classes()).not.toContain('review-card-in')
      expect(inner.find(`#comment-${id}`).exists()).toBe(true)
    }
    expect(w.get('[data-testid="review-margin"]').attributes('style')).toContain(`--review-card-ms: ${CARD_SLIDE_MS}ms`)
  })

  it('fades list cards in with the same stagger below lg', () => {
    const w = mountMargin({ comments: staggered, order: ['a', 'b', 'c'], mode: 'list' })
    expect(staggerOf(w.get('li[data-card-for="b"]'))).toBe('1')
    expect(staggerOf(w.get('li[data-card-for="c"]'))).toBe('0')
  })

  it('does not animate or stagger with prefers-reduced-motion', async () => {
    stubReducedMotion(true)
    stubHeights()
    const container = transcript({ a: 0, b: 200, c: 400 })
    container.dataset.testid = 'container'
    const w = mountMargin({ comments: staggered, order: ['a', 'b', 'c'], mode: 'margin', container })
    await flushPromises()
    await frame()
    const inner = w.get('[data-card-for="b"] [data-testid="card-reveal"]')
    expect(inner.classes()).not.toContain('review-card-in')
    expect(staggerOf(inner)).toBeUndefined()
    const list = mountMargin({ comments: staggered, order: ['a', 'b', 'c'], mode: 'list' })
    expect(staggerOf(list.get('li[data-card-for="b"]'))).toBeUndefined()
  })
})

describe('ReviewMargin: Jev transcription cards', () => {
  const triage = { kind: 'transcription', base_kind: 'skip', p_claim: 0.1, p_asr: 0.9 } as const
  const open = thread('x', { verdict: 'transcription', issue: null, triage })
  const answered = thread('x', { verdict: 'transcription', issue: null, triage, status: 'resolved', confirmed: true })

  it('passes the answer slot through for each card', () => {
    const w = mount(ReviewMargin, {
      props: { comments: [open], order: ['x'], activeCommentId: 'x', showResolved: false, container: null, mode: 'list' },
      slots: { answer: ({ comment }: { comment: ReviewThread }) => `answer for ${comment.id}` },
    })
    expect(w.get('#comment-x').text()).toContain('answer for x')
  })

  it('keeps the active card visible, collapsed, right after it was answered and resolved', async () => {
    const w = mountMargin({ comments: [answered], order: ['x'], activeCommentId: 'x' })
    expect(ids(w)).toEqual(['x'])
    expect(w.get('[data-testid="comment-status"]').text()).toBe('Not a claim')
    // Once another card (or none) is active it is hidden like any resolved card.
    await w.setProps({ activeCommentId: null })
    expect(ids(w)).toEqual([])
  })

  it('does not keep an active fact-check comment that was resolved', () => {
    const w = mountMargin({ comments: [thread('a', { status: 'resolved' })], order: ['a'], activeCommentId: 'a' })
    expect(ids(w)).toEqual([])
  })
})
