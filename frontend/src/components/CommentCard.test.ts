import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CommentCard from './CommentCard.vue'
import type { ReviewThread } from '@/composables/useTranscriptReview'

function thread(over: Partial<ReviewThread> = {}): ReviewThread {
  return {
    id: 'c1',
    turn_id: 't0001',
    quote: 'stage two melanoma',
    start: 10,
    end: 28,
    verdict: 'inaccurate',
    issue: 'A 2.1 mm tumour without ulceration is stage IIA at most, not stage II overall.',
    suggestion: 'This is stage IIA melanoma.',
    source: 'AJCC 8th edition melanoma',
    status: 'open',
    disagreeReason: null,
    thread: [],
    ...over,
  }
}

const mountCard = (over: Partial<ReviewThread> = {}, active = false, slots = {}) =>
  mount(CommentCard, { props: { comment: thread(over), active }, slots })

describe('CommentCard', () => {
  it('renders verdict, quote, issue, suggestion and source', () => {
    const w = mountCard()
    expect(w.get('[data-testid="comment-verdict"]').text()).toBe('Inaccurate')
    expect(w.get('[data-testid="comment-verdict"]').classes()).toContain('text-eosin')
    expect(w.text()).toContain('“stage two melanoma”')
    expect(w.get('[data-testid="comment-issue"]').text()).toContain('stage IIA at most')
    const suggestion = w.get('[data-testid="comment-suggestion"]').text()
    expect(suggestion).toContain('Suggested wording')
    expect(suggestion).toContain('This is stage IIA melanoma.')
    expect(w.get('[data-testid="comment-source"]').text()).toBe('Source: AJCC 8th edition melanoma')
    expect(w.find('[data-testid="comment-orphan"]').exists()).toBe(false)
  })

  it('labels incomplete comments in amber', () => {
    const w = mountCard({ verdict: 'incomplete' })
    const verdict = w.get('[data-testid="comment-verdict"]')
    expect(verdict.text()).toBe('Incomplete')
    expect(verdict.classes()).toContain('text-amber')
    expect(verdict.find('svg').exists()).toBe(true)
  })

  it('has the id the highlight points at and is focusable', () => {
    const el = mountCard().get('article')
    expect(el.attributes('id')).toBe('comment-c1')
    expect(el.attributes('tabindex')).toBe('0')
  })

  it('omits missing suggestion and source', () => {
    const w = mountCard({ suggestion: null, source: null })
    expect(w.find('[data-testid="comment-suggestion"]').exists()).toBe(false)
    expect(w.find('[data-testid="comment-source"]').exists()).toBe(false)
  })

  it('explains an orphan', () => {
    const w = mountCard({ start: null, end: null })
    expect(w.get('[data-testid="comment-orphan"]').text()).toBe("Couldn't find this text in the turn.")
  })

  it('collapses a resolved comment to one line with a Resolved badge', () => {
    const w = mountCard({ status: 'resolved' })
    expect(w.get('[data-testid="comment-collapsed"]').text()).toContain('“stage two melanoma”')
    expect(w.get('[data-testid="comment-status"]').text()).toBe('Resolved')
    expect(w.find('[data-testid="comment-issue"]').exists()).toBe(false)
    expect(w.find('[data-testid="comment-suggestion"]').exists()).toBe(false)
  })

  it('collapses a disagreed comment with a Disagreed badge and its reason', () => {
    const w = mountCard({ status: 'disagreed', disagreeReason: 'Pathology report says IIA.' })
    expect(w.get('[data-testid="comment-status"]').text()).toBe('Disagreed')
    expect(w.text()).toContain('Pathology report says IIA.')
  })

  it('raises the active card with a border in the verdict colour', () => {
    const inactive = mountCard().get('article')
    expect(inactive.classes()).not.toContain('border-l-eosin')
    const active = mountCard({}, true).get('article')
    expect(active.classes()).toEqual(expect.arrayContaining(['border-l-4', 'border-l-eosin', 'shadow-lg']))
    expect(active.attributes('aria-current')).toBe('true')
  })

  it('emits activate on click and on Enter', async () => {
    const w = mountCard()
    await w.get('article').trigger('click')
    await w.get('article').trigger('keydown', { key: 'Enter' })
    expect(w.emitted('activate')).toEqual([['c1'], ['c1']])
  })

  it('does not re-activate on Enter from a control inside the card', async () => {
    const w = mountCard({}, false, { actions: '<button type="button">Resolve</button>' })
    await w.get('button').trigger('keydown', { key: 'Enter' })
    expect(w.emitted('activate')).toBeUndefined()
  })

  it('renders the actions and thread slots', () => {
    const w = mountCard({ status: 'resolved' }, false, {
      actions: '<button type="button">Reopen</button>',
      thread: '<p>thread here</p>',
    })
    expect(w.get('button').text()).toBe('Reopen')
    expect(w.text()).toContain('thread here')
  })
})
