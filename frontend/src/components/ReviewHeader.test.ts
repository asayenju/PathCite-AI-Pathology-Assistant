import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ReviewHeader from './ReviewHeader.vue'
import type { ReviewStatus } from '@/composables/useTranscriptReview'
import { DEMO_REVIEW_LABEL, JEV_REVIEW_LABEL } from '@/lib/review'

type Props = InstanceType<typeof ReviewHeader>['$props']

const mountHeader = (props: Partial<Props> = {}) =>
  mount(ReviewHeader, {
    props: {
      status: 'done' as ReviewStatus,
      counts: { open: 3, resolved: 1, disagreed: 1 },
      showResolved: false,
      hasOpen: true,
      canExport: true,
      ...props,
    },
  })

describe('ReviewHeader', () => {
  it('shows the counts', () => {
    const counts = mountHeader().get('[data-testid="review-counts"]')
    expect(counts.text()).toBe('3 open · 1 resolved · 1 disagreed')
    expect(counts.classes()).toContain('tabular-nums')
  })

  it.each([
    ['running', 'Reviewing…'],
    ['done', 'Review complete'],
    ['stopped', 'Review stopped'],
    ['idle', ''],
  ] as const)('status %s reads "%s"', (status, text) => {
    expect(mountHeader({ status }).get('[data-testid="review-status"]').text()).toBe(text)
  })

  it('shows the error message in error', () => {
    const w = mountHeader({ status: 'error', errorMessage: 'Boom. Try again.' })
    expect(w.get('[data-testid="review-status"]').text()).toBe('Boom. Try again.')
  })

  it('Previous/Next are labelled buttons that emit, and are disabled without open issues', async () => {
    const w = mountHeader()
    const prev = w.get('[data-testid="review-prev"]')
    const next = w.get('[data-testid="review-next"]')
    expect(prev.attributes('aria-label')).toBe('Previous issue')
    expect(next.attributes('aria-label')).toBe('Next issue')
    await prev.trigger('click')
    await next.trigger('click')
    expect(w.emitted('prev')).toHaveLength(1)
    expect(w.emitted('next')).toHaveLength(1)

    const none = mountHeader({ hasOpen: false })
    expect(none.get('[data-testid="review-prev"]').attributes('disabled')).toBeDefined()
    expect(none.get('[data-testid="review-next"]').attributes('disabled')).toBeDefined()
  })

  it('Show resolved is a labelled checkbox with the closed count that works with v-model', async () => {
    const w = mountHeader({ showResolved: false, 'onUpdate:showResolved': (v: boolean) => w.setProps({ showResolved: v }) })
    const box = w.get('[data-testid="review-show-resolved"]')
    const label = w.get(`label[for="${box.attributes('id')}"]`)
    expect(label.text()).toBe('Show resolved (2)')
    await box.setValue(true)
    expect(w.emitted('update:showResolved')).toEqual([[true]])
    expect(w.props('showResolved')).toBe(true)
    expect((box.element as HTMLInputElement).checked).toBe(true)
  })

  it('Stop shows only while running and emits stop', async () => {
    expect(mountHeader({ status: 'done' }).find('[data-testid="review-stop"]').exists()).toBe(false)
    expect(mountHeader({ status: 'stopped' }).find('[data-testid="review-stop"]').exists()).toBe(false)
    const w = mountHeader({ status: 'running' })
    await w.get('[data-testid="review-stop"]').trigger('click')
    expect(w.emitted('stop')).toHaveLength(1)
  })

  it('Export emits export, and is disabled when there is nothing to export', async () => {
    const w = mountHeader()
    expect(w.get('[data-testid="review-export"]').text()).toBe('Export review')
    await w.get('[data-testid="review-export"]').trigger('click')
    expect(w.emitted('export')).toHaveLength(1)
    expect(mountHeader({ canExport: false }).get('[data-testid="review-export"]').attributes('disabled')).toBeDefined()
  })

  it('every control is a native button or input', () => {
    const w = mountHeader({ status: 'running' })
    const controls = w.findAll('[data-testid^="review-"]').filter((c) =>
      ['prev', 'next', 'show-resolved', 'stop', 'export'].some((k) => c.attributes('data-testid') === `review-${k}`),
    )
    expect(controls).toHaveLength(5)
    expect(controls.every((c) => ['BUTTON', 'INPUT'].includes(c.element.tagName))).toBe(true)
    expect(w.findAll('button').every((b) => b.attributes('type') === 'button')).toBe(true)
  })

  it('renders the demo label only when given', () => {
    expect(mountHeader().find('[data-testid="review-demo-label"]').exists()).toBe(false)
    const w = mountHeader({ demoLabel: DEMO_REVIEW_LABEL })
    expect(w.get('[data-testid="review-demo-label"]').text()).toBe('Demo review: hand-written mock, not an AI agent')
  })
})

describe('ReviewHeader: Jev triage', () => {
  const triage = (claims: number, openTranscriptions: number, skipped: number) => ({
    triageCounts: { claims, openTranscriptions, skipped },
  })

  it('counts claims, open transcription checks and skipped spans instead of open/resolved/disagreed', () => {
    const w = mountHeader(triage(3, 1, 7))
    const counts = w.get('[data-testid="review-triage-counts"]')
    expect(counts.text()).toBe('3 claims · 1 open transcription check · 7 skipped spans')
    expect(counts.classes()).toContain('tabular-nums')
    expect(w.find('[data-testid="review-counts"]').exists()).toBe(false)
  })

  it('uses singular and zero forms', () => {
    expect(mountHeader(triage(1, 0, 1)).get('[data-testid="review-triage-counts"]').text()).toBe(
      '1 claim · 0 open transcription checks · 1 skipped span',
    )
  })

  it('Previous/Next move across claims and transcription checks', async () => {
    const w = mountHeader(triage(2, 1, 0))
    const prev = w.get('[data-testid="review-prev"]')
    const next = w.get('[data-testid="review-next"]')
    expect(prev.attributes('aria-label')).toBe('Previous claim or transcription check')
    expect(next.attributes('aria-label')).toBe('Next claim or transcription check')
    await next.trigger('click')
    await prev.trigger('click')
    expect(w.emitted('next')).toHaveLength(1)
    expect(w.emitted('prev')).toHaveLength(1)
  })

  it('keeps Show resolved, Stop and Export', () => {
    const w = mountHeader({ ...triage(2, 1, 0), status: 'running' })
    for (const id of ['review-show-resolved', 'review-stop', 'review-export']) {
      expect(w.find(`[data-testid="${id}"]`).exists()).toBe(true)
    }
  })

  it('shows the Jev label', () => {
    const w = mountHeader({ ...triage(0, 0, 0), demoLabel: JEV_REVIEW_LABEL })
    expect(w.get('[data-testid="review-demo-label"]').text()).toBe(
      'Triage by Jev (TypeSafe). Claims are not fact-checked yet. Doctor turns are sent to TypeSafe.',
    )
  })
})
