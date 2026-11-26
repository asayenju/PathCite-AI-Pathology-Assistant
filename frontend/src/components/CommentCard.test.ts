import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
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
    issue: 'A 2.1 mm tumour without ulceration is stage IIA at most, not stage II overall.',
    suggestion: 'This is stage IIA melanoma.',
    source: 'AJCC 8th edition melanoma',
    status: 'open',
    disagreeReason: null,
    thread: [],
    ...EMPTY_TRIAGE_STATE,
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

const triage = (kind: 'claim' | 'transcription', base: 'claim' | 'skip' = 'claim'): Partial<ReviewThread> => ({
  verdict: kind,
  issue: null,
  suggestion: null,
  source: null,
  triage: { kind, base_kind: base, p_claim: 0.9, p_asr: kind === 'transcription' ? 0.8 : 0.05 },
})

describe('CommentCard: Jev claim', () => {
  it('is labelled Claim in hematoxylin blue, with an icon', () => {
    const w = mountCard({ ...triage('claim'), checkStatus: 'checking' }, true)
    const verdict = w.get('[data-testid="comment-verdict"]')
    expect(verdict.text()).toBe('Claim')
    expect(verdict.classes()).toContain('text-hema')
    expect(verdict.find('svg').exists()).toBe(true)
    expect(w.get('article').classes()).toContain('border-l-hema')
  })

  it('says Checking… in a polite status while the checker runs', () => {
    const w = mountCard({ ...triage('claim'), checkStatus: 'checking' })
    const status = w.get('[data-testid="claim-check"]')
    expect(status.attributes('role')).toBe('status')
    expect(status.attributes('aria-live')).toBe('polite')
    expect(status.text()).toBe('Checking…')
    expect(status.find('svg').classes()).toContain('motion-safe:animate-spin')
  })

  it('then says it is not fact-checked yet', () => {
    const w = mountCard({ ...triage('claim'), checkStatus: 'not_checked' })
    expect(w.get('[data-testid="claim-check"]').text()).toBe("Not fact-checked yet (the checker isn't built yet)")
  })

  it('shows the check error', () => {
    const w = mountCard({ ...triage('claim'), checkStatus: 'error', checkError: "This claim couldn't be checked." })
    expect(w.get('[data-testid="claim-check"]').text()).toBe("This claim couldn't be checked.")
  })

  it('shows what the claim was checked as when the doctor corrected it', () => {
    const w = mountCard({ ...triage('transcription'), verdict: 'claim', checkedAs: 'colon', checkStatus: 'not_checked' })
    expect(w.get('[data-testid="comment-verdict"]').text()).toBe('Claim')
    expect(w.get('[data-testid="claim-checked-as"]').text()).toBe('Checked as: “colon”')
    const plain = mountCard({ ...triage('claim'), checkStatus: 'checking' })
    expect(plain.find('[data-testid="claim-checked-as"]').exists()).toBe(false)
  })

  it('does not mention replies in its hint', () => {
    const w = mountCard({ ...triage('claim'), checkStatus: 'checking' })
    expect(w.text()).not.toContain('reply')
  })
})

describe('CommentCard: Jev transcription check', () => {
  it('asks whether the transcription is correct, with a ? icon', () => {
    const w = mountCard(triage('transcription'))
    const verdict = w.get('[data-testid="comment-verdict"]')
    expect(verdict.text()).toBe('Possible transcription error')
    expect(verdict.find('svg').exists()).toBe(true)
    expect(w.get('[data-testid="transcription-question"]').text()).toBe('Is this correct?')
    expect(w.find('[data-testid="claim-check"]').exists()).toBe(false)
  })

  it('renders the answer slot in the details while unanswered', () => {
    const w = mountCard(triage('transcription'), true, { answer: '<button type="button">Yes</button>' })
    expect(w.get('[data-testid="comment-details"] button').text()).toBe('Yes')
  })

  it('collapses to "Not a claim" with the correction once a correction is small talk', () => {
    const w = mountCard(
      { ...triage('transcription', 'skip'), status: 'resolved', confirmed: false, correction: 'colon' },
      false,
      { answer: '<button type="button">Yes</button>' },
    )
    expect(w.get('[data-testid="comment-status"]').text()).toBe('Not a claim')
    expect(w.get('[data-testid="comment-correction"]').text()).toBe('Correction: “colon”')
    expect(w.find('button').exists()).toBe(false)
  })

  it('collapses to "Not a claim" without a correction after Yes', () => {
    const w = mountCard({ ...triage('transcription', 'skip'), status: 'resolved', confirmed: true })
    expect(w.get('[data-testid="comment-status"]').text()).toBe('Not a claim')
    expect(w.find('[data-testid="comment-correction"]').exists()).toBe(false)
  })

  it('a transcription card resolved by hand keeps the Resolved badge', () => {
    const w = mountCard({ ...triage('transcription'), status: 'resolved' })
    expect(w.get('[data-testid="comment-status"]').text()).toBe('Resolved')
  })
})
