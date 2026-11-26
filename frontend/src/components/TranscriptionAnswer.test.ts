import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import TranscriptionAnswer from './TranscriptionAnswer.vue'
import { EMPTY_TRIAGE_STATE, type ReviewThread } from '@/composables/useTranscriptReview'

function thread(over: Partial<ReviewThread> = {}): ReviewThread {
  return {
    id: 'x1',
    turn_id: 't0005',
    quote: 'The growth in your sigmoid colin.',
    start: 0,
    end: 33,
    verdict: 'transcription',
    issue: null,
    suggestion: null,
    source: null,
    triage: { kind: 'transcription', base_kind: 'claim', p_claim: 0.6, p_asr: 0.9 },
    status: 'open',
    disagreeReason: null,
    thread: [],
    ...EMPTY_TRIAGE_STATE,
    ...over,
  }
}

let wrapper: ReturnType<typeof mount> | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const mountAnswer = (over: Partial<ReviewThread> = {}) =>
  (wrapper = mount(TranscriptionAnswer, { props: { comment: thread(over) }, attachTo: document.body }))

describe('TranscriptionAnswer', () => {
  it('asks with labelled native Yes and No buttons', () => {
    const w = mountAnswer()
    const group = w.get('[role="group"]')
    expect(group.attributes('aria-label')).toBe('Is this correct?')
    const yes = w.get('[data-testid="transcription-yes"]')
    const no = w.get('[data-testid="transcription-no"]')
    expect([yes.element.tagName, no.element.tagName]).toEqual(['BUTTON', 'BUTTON'])
    expect(yes.text()).toBe('Yes')
    expect(no.text()).toBe('No')
    expect(w.find('[data-testid="correction-form"]').exists()).toBe(false)
  })

  it('Yes emits confirm', async () => {
    const w = mountAnswer()
    await w.get('[data-testid="transcription-yes"]').trigger('click')
    expect(w.emitted('confirm')).toHaveLength(1)
  })

  it('No opens a labelled "What was said?" input and focuses it', async () => {
    const w = mountAnswer()
    await w.get('[data-testid="transcription-no"]').trigger('click')
    const input = w.get('[data-testid="correction-input"]')
    const label = w.get(`label[for="${input.attributes('id')}"]`)
    expect(label.text()).toBe('What was said?')
    expect(document.activeElement).toBe(input.element)
  })

  it('Submit is disabled for blank input and emits the text otherwise', async () => {
    const w = mountAnswer()
    await w.get('[data-testid="transcription-no"]').trigger('click')
    const submit = w.get('[data-testid="correction-submit"]')
    expect(submit.attributes('disabled')).toBeDefined()
    await w.get('[data-testid="correction-input"]').setValue('   ')
    expect(submit.attributes('disabled')).toBeDefined()
    await w.get('[data-testid="correction-form"]').trigger('submit')
    expect(w.emitted('correct')).toBeUndefined()

    await w.get('[data-testid="correction-input"]').setValue('The growth in your sigmoid colon.')
    expect(submit.attributes('disabled')).toBeUndefined()
    await w.get('[data-testid="correction-form"]').trigger('submit')
    expect(w.emitted('correct')).toEqual([['The growth in your sigmoid colon.']])
  })

  it('Escape or Cancel closes the form and returns focus to No', async () => {
    const w = mountAnswer()
    await w.get('[data-testid="transcription-no"]').trigger('click')
    await w.get('[data-testid="correction-input"]').trigger('keydown', { key: 'Escape' })
    expect(w.find('[data-testid="correction-form"]').exists()).toBe(false)
    expect(document.activeElement).toBe(w.get('[data-testid="transcription-no"]').element)

    await w.get('[data-testid="transcription-no"]').trigger('click')
    await w.get('[data-testid="correction-cancel"]').trigger('click')
    expect(w.find('[data-testid="correction-form"]').exists()).toBe(false)
  })

  it('shows a pending state and blocks answering while the correction is checked', async () => {
    const w = mountAnswer()
    await w.get('[data-testid="transcription-no"]').trigger('click')
    await w.get('[data-testid="correction-input"]').setValue('colon')
    await w.setProps({ comment: thread({ correctionPending: true }) })
    const pending = w.get('[data-testid="correction-pending"]')
    expect(pending.attributes('role')).toBe('status')
    expect(pending.text()).toBe('Checking your correction…')
    expect(w.get('[data-testid="correction-submit"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="transcription-yes"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="transcription-no"]').attributes('disabled')).toBeDefined()
    await w.get('[data-testid="correction-form"]').trigger('submit')
    expect(w.emitted('correct')).toBeUndefined()
  })

  it('shows the correction error and stays answerable with the text kept', async () => {
    const w = mountAnswer()
    await w.get('[data-testid="transcription-no"]').trigger('click')
    await w.get('[data-testid="correction-input"]').setValue('colon')
    await w.setProps({ comment: thread({ correctionError: "Couldn't check your correction. Try again." }) })
    expect(w.get('[data-testid="correction-error"]').text()).toBe("Couldn't check your correction. Try again.")
    expect((w.get('[data-testid="correction-input"]').element as HTMLInputElement).value).toBe('colon')
    expect(w.get('[data-testid="correction-submit"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-testid="transcription-yes"]').attributes('disabled')).toBeUndefined()
  })

  it('clicks do not bubble to the card', async () => {
    let bubbled = 0
    const host = document.createElement('div')
    host.addEventListener('click', () => bubbled++)
    document.body.appendChild(host)
    const w = (wrapper = mount(TranscriptionAnswer, { props: { comment: thread() }, attachTo: host }))
    await w.get('[data-testid="transcription-yes"]').trigger('click')
    await w.get('[data-testid="transcription-no"]').trigger('click')
    expect(bubbled).toBe(0)
    host.remove()
  })
})
