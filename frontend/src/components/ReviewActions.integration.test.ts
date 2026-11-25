import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import CommentActions from './CommentActions.vue'
import CommentThread from './CommentThread.vue'
import ReviewHeader from './ReviewHeader.vue'
import ReviewMargin from './ReviewMargin.vue'
import { useTranscriptReview, type ReviewThread } from '@/composables/useTranscriptReview'
import type { ClarifySource, ReviewComment, ReviewEvent, ReviewSource, RoledTurn } from '@/types'

const comment = (id: string, start: number | null, over: Partial<ReviewComment> = {}): ReviewComment => ({
  id,
  turn_id: 't0',
  quote: `quote ${id}`,
  start,
  end: start === null ? null : start + 5,
  verdict: 'inaccurate',
  issue: `issue ${id}`,
  suggestion: null,
  source: null,
  ...over,
})

const COMMENTS = [comment('a', 0), comment('b', 10, { verdict: 'incomplete' }), comment('ok', 20, { verdict: 'accurate' })]

const source: ReviewSource = async function* () {
  const events: ReviewEvent[] = [
    { type: 'turn_started', turn_id: 't0' },
    ...COMMENTS.map((c) => ({ type: 'comment' as const, comment: c })),
    { type: 'turn_done', turn_id: 't0' },
    { type: 'done' },
  ]
  for (const e of events) yield e
}

const CANNED = 'Canned clarification from the fake agent.'
const clarify: ClarifySource = (_c, _q, signal) =>
  new Promise((resolve, reject) => {
    const t = setTimeout(() => resolve(CANNED), 800)
    signal.addEventListener('abort', () => {
      clearTimeout(t)
      reject(new DOMException('aborted', 'AbortError'))
    })
  })

const TURNS = [{ id: 't0', speaker: 'S0', role: 'doctor', text: 'x'.repeat(40) }] as unknown as RoledTurn[]

/** The same wiring #31 uses in TranscriptDrop, minus the transcript. */
function mountHost(mode: 'list' | 'margin' = 'list', container: HTMLElement | null = null) {
  let review!: ReturnType<typeof useTranscriptReview>
  const Host = defineComponent({
    setup() {
      review = useTranscriptReview({ source, clarify })
      const showResolved = ref(false)
      return () => [
        h(ReviewHeader, {
          status: review.status.value,
          counts: review.counts.value,
          showResolved: showResolved.value,
          hasOpen: review.counts.value.open > 0,
          canExport: review.hasResults.value,
          'onUpdate:showResolved': (v: boolean) => (showResolved.value = v),
          onNext: review.nextIssue,
          onPrev: review.prevIssue,
          onStop: review.stop,
        }),
        h(
          ReviewMargin,
          {
            comments: review.comments.value,
            order: review.issueOrder.value,
            activeCommentId: review.activeCommentId.value,
            showResolved: showResolved.value,
            container,
            mode,
            onActivate: review.setActive,
          },
          {
            actions: ({ comment: c }: { comment: ReviewThread }) =>
              h(CommentActions, {
                comment: c,
                onResolve: () => review.resolve(c.id),
                onReopen: () => review.reopen(c.id),
                onDisagree: (reason: string | null) => review.disagree(c.id, reason),
              }),
            thread: ({ comment: c }: { comment: ReviewThread }) =>
              h(CommentThread, { comment: c, onReply: (q: string) => review.reply(c.id, q) }),
          },
        ),
      ]
    },
  })
  const w = mount(Host, { attachTo: document.body })
  return { w, review: () => review }
}

const card = (w: ReturnType<typeof mount>, id: string) => w.find(`#comment-${id}`)
const counts = (w: ReturnType<typeof mount>) => w.get('[data-testid="review-counts"]').text()

let wrapper: ReturnType<typeof mount> | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('review actions with useTranscriptReview', () => {
  beforeEach(() => vi.useFakeTimers())

  async function started(mode: 'list' | 'margin' = 'list', container: HTMLElement | null = null) {
    const host = mountHost(mode, container)
    wrapper = host.w
    await host.review().start(TURNS, { sampleId: 'demo' })
    await flushPromises()
    return host
  }

  it('resolve hides the card; Show resolved reveals it collapsed with Reopen; Reopen brings it back', async () => {
    const { w } = await started()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review complete')
    expect(counts(w)).toBe('2 open · 0 resolved · 0 disagreed')
    expect(card(w, 'ok').exists()).toBe(false)

    await card(w, 'a').get('[data-testid="resolve"]').trigger('click')
    expect(card(w, 'a').exists()).toBe(false)
    expect(counts(w)).toBe('1 open · 1 resolved · 0 disagreed')

    await w.get('[data-testid="review-show-resolved"]').setValue(true)
    const a = card(w, 'a')
    expect(a.attributes('data-status')).toBe('resolved')
    expect(a.get('[data-testid="comment-status"]').text()).toBe('Resolved')
    await a.get('[data-testid="reopen"]').trigger('click')
    expect(card(w, 'a').attributes('data-status')).toBe('open')
    expect(card(w, 'a').find('[data-testid="resolve"]').exists()).toBe(true)
    expect(counts(w)).toBe('2 open · 0 resolved · 0 disagreed')
  })

  it('disagree with a reason collapses the card with a Disagreed badge and the trimmed reason', async () => {
    const { w } = await started()
    await w.get('[data-testid="review-show-resolved"]').setValue(true)
    const b = card(w, 'b')
    await b.get('[data-testid="disagree"]').trigger('click')
    await b.get('[data-testid="disagree-reason"]').setValue('  The guideline allows this.  ')
    await b.get('[data-testid="disagree-form"]').trigger('submit')
    const collapsed = card(w, 'b')
    expect(collapsed.attributes('data-status')).toBe('disagreed')
    expect(collapsed.get('[data-testid="comment-status"]').text()).toBe('Disagreed')
    expect(collapsed.text()).toContain('The guideline allows this.')
    expect(collapsed.find('[data-testid="reopen"]').exists()).toBe(true)
    expect(counts(w)).toBe('1 open · 0 resolved · 1 disagreed')
  })

  it('reply shows the question and "Agent is thinking…", then the canned answer', async () => {
    const { w } = await started()
    const a = card(w, 'a')
    await a.get('[data-testid="reply-input"]').setValue('Which source?')
    await a.get('[data-testid="reply-input"]').trigger('keydown', { key: 'Enter' })
    expect(a.findAll('[data-testid="thread-message"]').map((m) => m.text())).toEqual([
      'YouWhich source?',
      'Agent Agent is thinking…',
    ])
    expect(a.get('[data-testid="reply-send"]').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    expect(a.findAll('[data-testid="thread-message"]').map((m) => m.text())).toEqual([
      'YouWhich source?',
      `Agent${CANNED}`,
    ])
    expect(a.find('[data-testid="thread-pending"]').exists()).toBe(false)
  })

  it('header Next/Previous move between open issues and skip resolved ones', async () => {
    const { w, review } = await started()
    await w.get('[data-testid="review-next"]').trigger('click')
    expect(review().activeCommentId.value).toBe('a')
    expect(card(w, 'a').attributes('aria-current')).toBe('true')
    await card(w, 'a').get('[data-testid="resolve"]').trigger('click')
    await w.get('[data-testid="review-next"]').trigger('click')
    expect(review().activeCommentId.value).toBe('b')
    await card(w, 'b').get('[data-testid="resolve"]').trigger('click')
    expect(w.get('[data-testid="review-next"]').attributes('disabled')).toBeDefined()
  })

  it('clicking Resolve does not also activate the card', async () => {
    const { w, review } = await started()
    await card(w, 'b').get('[data-testid="resolve"]').trigger('click')
    expect(review().activeCommentId.value).toBeNull()
  })

  it('margin mode re-lays out when a card grows (disagree form opens) via ResizeObserver', async () => {
    let callback: (() => void) | null = null
    const observed = new Set<Element>()
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: () => void) {
          callback = cb
        }
        observe(el: Element) {
          observed.add(el)
        }
        disconnect() {
          observed.clear()
        }
        unobserve() {}
      },
    )
    const heights: Record<string, number> = { a: 100, b: 100 }
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.dataset.cardFor ? (heights[this.dataset.cardFor] ?? 0) : 0
    })
    const container = document.createElement('div')
    for (const [id, top] of [
      ['a', 0],
      ['b', 50],
    ] as const) {
      const mark = document.createElement('mark')
      mark.dataset.anchorFor = id
      mark.getBoundingClientRect = () => ({ top, bottom: top + 20, left: 0, right: 0, width: 0, height: 20, x: 0, y: top, toJSON: () => ({}) })
      container.appendChild(mark)
    }
    const { w } = await started('margin', container)
    await nextTick()
    const topOf = (id: string) =>
      w.get(`[data-card-for="${id}"]`).attributes('style')?.match(/translateY\((-?[\d.]+)px\)/)?.[1]
    expect(topOf('b')).toBe('108')

    const wrapperA = w.get('[data-card-for="a"]').element
    expect(observed.has(wrapperA)).toBe(true)
    await card(w, 'a').get('[data-testid="disagree"]').trigger('click')
    // The real browser fires the observer because the wrapper grew; simulate that.
    heights.a = 220
    callback!()
    await nextTick()
    expect(topOf('b')).toBe('228')
  })
})
