import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h, nextTick } from 'vue'
import CommentActions from './CommentActions.vue'
import CommentCard from './CommentCard.vue'
import { EMPTY_TRIAGE_STATE, type ReviewThread } from '@/composables/useTranscriptReview'

function thread(over: Partial<ReviewThread> = {}): ReviewThread {
  return {
    id: 'c1',
    turn_id: 't0001',
    quote: 'stage two melanoma',
    start: 10,
    end: 28,
    verdict: 'inaccurate',
    issue: 'Stage IIA at most.',
    suggestion: null,
    source: null,
    status: 'open',
    disagreeReason: null,
    thread: [],
    ...EMPTY_TRIAGE_STATE,
    ...over,
  }
}

const mounted: ReturnType<typeof mount>[] = []
function mountActions(over: Partial<ReviewThread> = {}) {
  const w = mount(CommentActions, { props: { comment: thread(over) }, attachTo: document.body })
  mounted.push(w)
  return w
}

afterEach(() => {
  for (const w of mounted.splice(0)) w.unmount()
})

describe('CommentActions', () => {
  it('open: Resolve emits resolve, all buttons are type=button', async () => {
    const w = mountActions()
    expect(w.findAll('button').every((b) => b.attributes('type') === 'button')).toBe(true)
    await w.get('[data-testid="resolve"]').trigger('click')
    expect(w.emitted('resolve')).toHaveLength(1)
    expect(w.find('[data-testid="reopen"]').exists()).toBe(false)
  })

  it('Disagree opens the reason form, focuses it, and submits the raw text', async () => {
    const w = mountActions()
    await w.get('[data-testid="disagree"]').trigger('click')
    await nextTick()
    const reason = w.get('[data-testid="disagree-reason"]')
    expect(document.activeElement).toBe(reason.element)
    expect(w.text()).toContain('Why is the agent wrong? (optional)')
    expect(w.find('[data-testid="resolve"]').exists()).toBe(false)
    await reason.setValue('  Guideline says IIA  ')
    await w.get('[data-testid="disagree-form"]').trigger('submit')
    expect(w.emitted('disagree')).toEqual([['  Guideline says IIA  ']])
    expect(w.find('[data-testid="disagree-form"]').exists()).toBe(false)
  })

  it('submitting a blank reason emits null', async () => {
    const w = mountActions()
    await w.get('[data-testid="disagree"]').trigger('click')
    await w.get('[data-testid="disagree-reason"]').setValue('   ')
    await w.get('[data-testid="disagree-submit"]').trigger('click')
    expect(w.emitted('disagree')).toEqual([[null]])
  })

  it('Cancel closes the form without emitting and returns focus to Disagree', async () => {
    const w = mountActions()
    await w.get('[data-testid="disagree"]').trigger('click')
    await w.get('[data-testid="disagree-reason"]').setValue('draft')
    await w.get('[data-testid="disagree-cancel"]').trigger('click')
    await nextTick()
    expect(w.find('[data-testid="disagree-form"]').exists()).toBe(false)
    expect(w.emitted('disagree')).toBeUndefined()
    expect(document.activeElement).toBe(w.get('[data-testid="disagree"]').element)
    // The draft is discarded.
    await w.get('[data-testid="disagree"]').trigger('click')
    expect((w.get('[data-testid="disagree-reason"]').element as HTMLTextAreaElement).value).toBe('')
  })

  it('Escape cancels the form', async () => {
    const w = mountActions()
    await w.get('[data-testid="disagree"]').trigger('click')
    await w.get('[data-testid="disagree-reason"]').trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(w.find('[data-testid="disagree-form"]').exists()).toBe(false)
    expect(w.emitted('disagree')).toBeUndefined()
    expect(document.activeElement).toBe(w.get('[data-testid="disagree"]').element)
  })

  it.each(['resolved', 'disagreed'] as const)('%s: only Reopen, which emits reopen', async (status) => {
    const w = mountActions({ status })
    expect(w.find('[data-testid="resolve"]').exists()).toBe(false)
    expect(w.find('[data-testid="disagree"]').exists()).toBe(false)
    await w.get('[data-testid="reopen"]').trigger('click')
    expect(w.emitted('reopen')).toHaveLength(1)
  })

  it('clicks and Enter on the buttons do not activate the surrounding card', async () => {
    const comment = thread()
    const w = mount(CommentCard, {
      props: { comment, active: false },
      slots: { actions: () => h(CommentActions, { comment }) },
    })
    await w.get('[data-testid="resolve"]').trigger('click')
    await w.get('[data-testid="resolve"]').trigger('keydown', { key: 'Enter' })
    await w.get('[data-testid="disagree"]').trigger('click')
    await w.get('[data-testid="disagree-reason"]').trigger('keydown', { key: 'Enter' })
    expect(w.emitted('activate')).toBeUndefined()
    // The card itself still activates.
    await w.get('[data-testid="comment-card"]').trigger('click')
    expect(w.emitted('activate')).toEqual([['c1']])
  })
})

