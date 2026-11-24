import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TranscriptDrop from './TranscriptDrop.vue'
import { transcriptMessageFor } from '@/lib/transcripts'
import type { TranscriptImport } from '@/types'

const imported: TranscriptImport = {
  source: 'aws_transcribe',
  language: 'en-US',
  duration_s: 125,
  speakers: [
    { id: 'S1', source_label: 'spk_0', word_count: 40, talk_time_s: 70, preview: [], suggested_role: 'doctor' },
    { id: 'S2', source_label: 'spk_1', word_count: 20, talk_time_s: 40, preview: [], suggested_role: 'patient' },
  ],
  turns: [
    { id: 't0001', speaker: 'S1', start: 0, end: 2, text: 'Hi.', is_final: true, words: [], min_confidence: null },
    { id: 't0002', speaker: 'S2', start: 2, end: 4, text: 'Hello.', is_final: true, words: [], min_confidence: null },
    { id: 't0003', speaker: 'S1', start: 4, end: 6, text: 'So.', is_final: true, words: [], min_confidence: null },
  ],
  warnings: ['Overlapping speech found.'],
  notice: 'Research use only. Not a diagnosis.',
}

const drop = (file: File) => ({ dataTransfer: { types: ['Files'], files: [file] } })
const zone = (w: ReturnType<typeof mount>) => w.get('[aria-label^="Drop zone."]')

afterEach(() => vi.unstubAllGlobals())

describe('TranscriptDrop', () => {
  it('refuses a non-JSON file with the mapped message, without calling the server', async () => {
    vi.stubGlobal('fetch', vi.fn())
    const wrapper = mount(TranscriptDrop)
    await zone(wrapper).trigger('drop', drop(new File(['x'], 'slide.png', { type: 'image/png' })))
    await flushPromises()
    expect(fetch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain(transcriptMessageFor('unsupported_transcript_format'))
  })

  it('imports a dropped JSON file and shows the summary, then clears', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(imported))))
    const wrapper = mount(TranscriptDrop)
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json', { type: 'application/json' })))
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(1)
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0]![0]).toBe('/api/transcripts/import')
    const summary = wrapper.get('[data-testid="transcript-summary"]').text()
    expect(summary).toContain('AWS Transcribe')
    expect(summary).toContain('en-US')
    expect(summary).toContain('2:05')
    expect(summary).toContain('visit.json')
    expect(summary).toContain('Overlapping speech found.')

    await wrapper.findAll('button').find((b) => b.text() === 'Clear transcript')!.trigger('click')
    expect(wrapper.find('[data-testid="transcript-summary"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label^="Drop zone."]').exists()).toBe(true)
  })

  it('shows a backend error and lets the user clear it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'x', code: 'invalid_json' }), { status: 422 })),
    )
    const wrapper = mount(TranscriptDrop)
    await zone(wrapper).trigger('drop', drop(new File(['{'], 'visit.json')))
    await flushPromises()
    expect(wrapper.text()).toContain(transcriptMessageFor('invalid_json'))
    await wrapper.findAll('button').find((b) => b.text() === 'Clear')!.trigger('click')
    expect(wrapper.text()).not.toContain(transcriptMessageFor('invalid_json'))
  })

  it('also imports a file chosen with the picker, which accepts JSON only', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(imported))))
    const wrapper = mount(TranscriptDrop)
    const input = wrapper.get('input[type=file]')
    expect(input.attributes('accept')).toBe('.json,application/json')
    Object.defineProperty(input.element, 'files', { value: [new File(['{}'], 'visit.json')] })
    await input.trigger('change')
    await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('never touches web storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(imported))))
    const wrapper = mount(TranscriptDrop)
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json')))
    await flushPromises()
    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })
})
