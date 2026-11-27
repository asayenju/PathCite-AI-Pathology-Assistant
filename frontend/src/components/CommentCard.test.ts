import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CommentCard from './CommentCard.vue'
import { NOT_CHECKED_TEXT } from '@/lib/check'
import { EMPTY_TRIAGE_STATE, type ReviewThread } from '@/composables/useTranscriptReview'
import type { CheckedResult, CheckSource } from '@/types'

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

  it('says it was not fact-checked when the check was stopped', () => {
    const w = mountCard({ ...triage('claim'), checkStatus: 'not_checked' })
    expect(w.get('[data-testid="claim-check"]').text()).toBe(NOT_CHECKED_TEXT)
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

const checkSource = (over: Partial<CheckSource> = {}): CheckSource => ({
  chunk_id: 1,
  title: 'Colorectal cancer (NG151)',
  section: 'Recommendations > Staging',
  url: 'https://www.nice.org.uk/guidance/ng151',
  trust: 'high',
  year: 2021,
  quote: 'Grade does not determine stage.',
  stance: 'contradicts',
  luna_stance: 'contradicts',
  jev_stance: 'contradicts',
  jev_p: 0.9,
  ...over,
})

function checkedResult(over: Partial<CheckedResult> = {}): CheckedResult {
  return {
    status: 'checked',
    verdict: 'inaccurate',
    issue: 'Grade describes the cells, not spread.',
    suggestion: 'Moderately differentiated describes how the cells look.',
    needs_review: false,
    review_reasons: [],
    sources: [checkSource()],
    model: 'gpt-6-luna',
    jev_model: 'jev-1.14.0',
    ...over,
  }
}

const mountChecked = (over: Partial<CheckedResult> = {}, active = true, thread: Partial<ReviewThread> = {}) =>
  mountCard({ ...triage('claim'), checkStatus: 'checked', check: checkedResult(over), ...thread }, active)

describe('CommentCard: checked claim', () => {
  it.each([
    ['accurate', 'Accurate', 'text-moss', 'border-l-moss', 'Accurate: the sources support this.'],
    ['inaccurate', 'Inaccurate', 'text-eosin', 'border-l-eosin', 'Inaccurate: the sources contradict this.'],
    [
      'incomplete',
      'Incomplete',
      'text-amber',
      'border-l-amber',
      'Incomplete: the sources support this, but it leaves out a qualifier.',
    ],
    ['unverified', 'Not verified', 'text-quiet', 'border-l-quiet', "Not verified — the corpus doesn't cover this"],
    ['disputed', 'Sources disagree', 'text-ink', 'border-l-ink', 'Sources disagree'],
  ] as const)('shows the %s verdict with its label, colour and status text', (verdict, label, text, border, status) => {
    const w = mountChecked({ verdict })
    const head = w.get('[data-testid="comment-verdict"]')
    expect(head.text()).toBe(label)
    expect(head.classes()).toContain(text)
    expect(head.find('svg').exists()).toBe(true)
    expect(w.get('article').classes()).toContain(border)
    expect(w.get('[data-testid="claim-check"]').text()).toBe(status)
    // The DOM contract and the comment's own verdict are unchanged.
    expect(w.get('article').attributes('id')).toBe('comment-c1')
    expect(w.get('article').attributes('data-verdict')).toBe('claim')
    expect(w.get('article').attributes('data-check-verdict')).toBe(verdict)
  })

  it("shows the checker's issue and suggestion", () => {
    const w = mountChecked()
    expect(w.get('[data-testid="comment-issue"]').text()).toBe('Grade describes the cells, not spread.')
    expect(w.get('[data-testid="comment-suggestion"]').text()).toContain(
      'Moderately differentiated describes how the cells look.',
    )
  })

  it('omits a missing issue and suggestion', () => {
    const w = mountChecked({ verdict: 'accurate', issue: null, suggestion: null })
    expect(w.find('[data-testid="comment-issue"]').exists()).toBe(false)
    expect(w.find('[data-testid="comment-suggestion"]').exists()).toBe(false)
  })

  it('lists the sources grouped by stance in the details, with links', () => {
    const w = mountChecked({
      verdict: 'disputed',
      sources: [
        checkSource({ chunk_id: 1, stance: 'supports', title: 'Pro' }),
        checkSource({ chunk_id: 2, stance: 'contradicts', title: 'Con' }),
        checkSource({
          chunk_id: 3,
          stance: 'unclear',
          title: 'Huh',
          luna_stance: 'supports',
          jev_stance: 'irrelevant',
          jev_p: 0.4,
        }),
        checkSource({ chunk_id: 4, stance: 'irrelevant', title: 'Off topic' }),
      ],
    })
    const details = w.get('[data-testid="comment-details"]')
    expect(details.find('[data-testid="check-sources"]').exists()).toBe(true)
    expect(details.findAll('[data-testid^="sources-"] h4').map((h) => h.text())).toEqual([
      'Supports (1)',
      'Contradicts (1)',
      'Unclear (1)',
    ])
    expect(details.get('[data-testid="sources-irrelevant"] summary').text()).toBe('Not relevant (1)')
    expect(details.get('[data-testid="source-opinions"]').text()).toBe('Luna: supports · Jev: irrelevant (0.40)')
    expect(details.findAll('a')).toHaveLength(4)
    for (const a of details.findAll('a')) {
      expect(a.attributes('target')).toBe('_blank')
      expect(a.attributes('rel')).toBe('noopener noreferrer')
    }
  })

  it('names the models that checked it', () => {
    expect(mountChecked().get('[data-testid="check-models"]').text()).toBe('Checked with gpt-6-luna and jev-1.14.0')
    expect(mountChecked({ jev_model: null }).get('[data-testid="check-models"]').text()).toBe(
      'Checked with gpt-6-luna (Jev unavailable)',
    )
  })

  it('has no review banner when no review is needed', () => {
    expect(mountChecked().find('[data-testid="check-review-banner"]').exists()).toBe(false)
  })

  it('shows a "Needs your review" banner with the reasons in plain words, even when not selected', () => {
    const w = mountChecked(
      {
        verdict: 'disputed',
        needs_review: true,
        review_reasons: ['sources_disagree', 'models_disagree', 'jev_unavailable'],
      },
      false,
    )
    const banner = w.get('[data-testid="check-review-banner"]')
    expect(banner.get('[data-testid="check-review-title"]').text()).toBe('Needs your review')
    expect(banner.findAll('li').map((li) => li.text())).toEqual([
      'The sources disagree with each other.',
      'OpenAI (Luna) and Jev read at least one source differently.',
      "Jev didn't answer, so only OpenAI (Luna) judged the sources.",
    ])
  })

  it('shows an unknown review reason generically', () => {
    const w = mountChecked({ needs_review: true, review_reasons: ['stale_guideline'] })
    expect(w.get('[data-testid="check-review-banner"] li').text()).toBe('Other reason: stale guideline')
  })

  it('shows the banner without a list when there are no reasons', () => {
    const w = mountChecked({ needs_review: true, review_reasons: [] })
    expect(w.get('[data-testid="check-review-banner"]').text()).toBe('Needs your review')
    expect(w.find('[data-testid="check-review-banner"] li').exists()).toBe(false)
  })

  it('collapses with the check verdict icon once resolved', () => {
    const w = mountChecked({ verdict: 'inaccurate' }, false, { status: 'resolved' })
    const line = w.get('[data-testid="comment-collapsed"]')
    expect(line.text()).toContain('Inaccurate:')
    expect(line.get('svg').classes()).toContain('text-eosin')
  })

  it('still shows what it was checked as', () => {
    const w = mountChecked({}, true, { checkedAs: 'colon' })
    expect(w.get('[data-testid="claim-checked-as"]').text()).toBe('Checked as: “colon”')
  })
})
