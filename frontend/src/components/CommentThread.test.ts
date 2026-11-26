import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CommentThread from './CommentThread.vue'
import { EMPTY_TRIAGE_STATE, type ReviewThread, type ThreadMessage } from '@/composables/useTranscriptReview'

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

const msg = (id: string, author: 'you' | 'agent', text: string, pending = false): ThreadMessage => ({
  id,
  author,
  text,
  pending,
})

const mountThread = (over: Partial<ReviewThread> = {}, disabled?: boolean) =>
  mount(CommentThread, { props: { comment: thread(over), disabled } })

describe('CommentThread', () => {
  it('lists messages with You / Agent labels in a polite live region', () => {
    const w = mountThread({ thread: [msg('m1', 'you', 'Why IIA?'), msg('m2', 'agent', 'Because of thickness.')] })
    const items = w.findAll('[data-testid="thread-message"]')
    expect(items.map((i) => i.text())).toEqual(['YouWhy IIA?', 'AgentBecause of thickness.'])
    expect(w.get('ol').attributes('aria-live')).toBe('polite')
  })

  it('shows "Agent is thinking…" with a reduced-motion-safe spinner while pending, and blocks sending', async () => {
    const w = mountThread({ thread: [msg('m1', 'you', 'Why?'), msg('m2', 'agent', '', true)] })
    const pending = w.get('[data-testid="thread-pending"]')
    expect(pending.text()).toBe('Agent is thinking…')
    expect(pending.get('svg').classes()).toContain('motion-safe:animate-spin')
    const input = w.get('[data-testid="reply-input"]')
    await input.setValue('Another question')
    expect(w.get('[data-testid="reply-send"]').attributes('disabled')).toBeDefined()
    await input.trigger('keydown', { key: 'Enter' })
    await w.get('[data-testid="reply-form"]').trigger('submit')
    expect(w.emitted('reply')).toBeUndefined()
  })

  it('blocks blank and whitespace-only replies', async () => {
    const w = mountThread()
    const send = w.get('[data-testid="reply-send"]')
    expect(send.attributes('disabled')).toBeDefined()
    await w.get('[data-testid="reply-input"]').setValue('   \n ')
    expect(send.attributes('disabled')).toBeDefined()
    await w.get('[data-testid="reply-input"]').trigger('keydown', { key: 'Enter' })
    expect(w.emitted('reply')).toBeUndefined()
  })

  it('Enter submits the question and clears the box', async () => {
    const w = mountThread()
    const input = w.get('[data-testid="reply-input"]')
    await input.setValue('Which guideline?')
    expect(w.get('[data-testid="reply-send"]').attributes('disabled')).toBeUndefined()
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('reply')).toEqual([['Which guideline?']])
    expect((input.element as HTMLTextAreaElement).value).toBe('')
  })

  it('the Ask button submits too', async () => {
    const w = mountThread()
    await w.get('[data-testid="reply-input"]').setValue('Source?')
    await w.get('[data-testid="reply-form"]').trigger('submit')
    expect(w.emitted('reply')).toEqual([['Source?']])
  })

  it('Shift+Enter does not submit', async () => {
    const w = mountThread()
    const input = w.get('[data-testid="reply-input"]')
    await input.setValue('line one')
    await input.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(w.emitted('reply')).toBeUndefined()
    expect((input.element as HTMLTextAreaElement).value).toBe('line one')
  })

  it('disabled turns the reply box off', async () => {
    const w = mountThread({}, true)
    expect(w.get('[data-testid="reply-input"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="reply-send"]').attributes('disabled')).toBeDefined()
  })

  it('collapsed comments show only a reply count (or nothing)', () => {
    const w = mountThread({
      status: 'resolved',
      thread: [msg('m1', 'you', 'a'), msg('m2', 'agent', 'b'), msg('m3', 'you', 'c'), msg('m4', 'agent', 'd')],
    })
    expect(w.find('[data-testid="reply-form"]').exists()).toBe(false)
    expect(w.find('[data-testid="thread-message"]').exists()).toBe(false)
    expect(w.get('[data-testid="thread-count"]').text()).toBe('2 replies')
    expect(mountThread({ status: 'disagreed' }).html()).not.toContain('thread-count')
  })
})
