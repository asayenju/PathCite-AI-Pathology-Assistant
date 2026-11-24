import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TranscriptDrop from './TranscriptDrop.vue'
import { SAMPLE_LOAD_ERROR, TRANSCRIPT_MIME } from '@/lib/transcriptSamples'
import { transcriptMessageFor } from '@/lib/transcripts'
import type { TranscriptImport, TranscriptManifest } from '@/types'

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
const confirmButton = (w: ReturnType<typeof mount>) => w.findAll('button').find((b) => b.text() === 'Confirm roles')!

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

  it('assigns roles, confirms, edits and clears', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(imported))))
    const wrapper = mount(TranscriptDrop)
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json')))
    await flushPromises()

    expect(wrapper.find('[data-testid="speaker-S1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="speaker-S2"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="transcript-summary"]').text()).toContain('Overlapping speech found.')
    expect(wrapper.get('[data-testid="turn-t0002"] [data-testid="turn-role"]').text()).toBe('Patient')

    // Suggested doctor -> Confirm enabled. Unassign it: disabled.
    await wrapper.get('#role-S1').setValue('')
    expect(confirmButton(wrapper).attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="turn-t0001"] [data-testid="turn-role"]').text()).toBe('Unassigned')
    await wrapper.get('#role-S1').setValue('doctor')
    await confirmButton(wrapper).trigger('click')

    expect(wrapper.text()).toContain('Roles confirmed.')
    expect(wrapper.get('#role-S1').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="transcript-summary"]').exists()).toBe(true)

    await wrapper.findAll('button').find((b) => b.text() === 'Edit roles')!.trigger('click')
    expect(wrapper.get('#role-S1').attributes('disabled')).toBeUndefined()

    await wrapper.findAll('button').find((b) => b.text() === 'Clear transcript')!.trigger('click')
    expect(wrapper.find('[data-testid="speaker-roles"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label^="Drop zone."]').exists()).toBe(true)
  })

  it('never touches web storage through import, roles, confirm and clear', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(imported))))
    const wrapper = mount(TranscriptDrop)
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json')))
    await flushPromises()
    await wrapper.get('#role-S2').setValue('other')
    await confirmButton(wrapper).trigger('click')
    await wrapper.findAll('button').find((b) => b.text() === 'Edit roles')!.trigger('click')
    await wrapper.findAll('button').find((b) => b.text() === 'Clear transcript')!.trigger('click')
    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })
})

describe('TranscriptDrop with sample transcripts', () => {
  const manifest: TranscriptManifest = {
    notice: 'Synthetic, AI-written sample conversations.',
    folders: [
      {
        name: 'GI',
        slug: 'gi',
        items: [
          { id: 'crc', title: 'Colorectal results', vendor: 'deepgram', file: 'transcripts/gi/crc.json' },
          { id: 'polyp', title: 'Benign polyp', vendor: 'whisperx', file: 'transcripts/gi/polyp.json' },
        ],
      },
    ],
  }
  const sampleBody: Record<string, string> = {
    '/transcripts/gi/crc.json': '{"sample":"crc"}',
    '/transcripts/gi/polyp.json': '{"sample":"polyp"}',
  }
  /** polyp's import suggests the opposite roles, so a reset is visible. */
  const polypImport: TranscriptImport = {
    ...imported,
    source: 'whisperx',
    speakers: [
      { ...imported.speakers[0]!, suggested_role: 'patient' },
      { ...imported.speakers[1]!, suggested_role: 'doctor' },
    ],
  }

  /** Routes by URL: sample files are served from public/, the import endpoint answers per uploaded sample. */
  function routeFetch({ sampleStatus = 200 } = {}) {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/transcripts/import') {
        const file = (init!.body as FormData).get('file') as File
        const text = await file.text()
        return new Response(JSON.stringify(text.includes('polyp') ? polypImport : imported))
      }
      if (url in sampleBody) {
        // A Response-like object: jsdom's File only accepts jsdom's Blob, not the one Node's Response.blob() makes.
        const ok = sampleStatus === 200
        return { ok, status: sampleStatus, blob: async () => new Blob([ok ? sampleBody[url]! : 'nope']) } as Response
      }
      throw new TypeError(`unexpected fetch ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  const mountWith = () => mount(TranscriptDrop, { props: { manifest } })
  const sampleButton = (w: ReturnType<typeof mountWith>, index: number) => w.findAll('#tfolder-gi button')[index]!
  const sampleDrop = (file: string) => ({
    dataTransfer: { types: [TRANSCRIPT_MIME], files: [], getData: (type: string) => (type === TRANSCRIPT_MIME ? file : '') },
  })

  /** The upload sent to the import endpoint: same path, name and bytes as a user's file would take. */
  async function uploads(fetchMock: ReturnType<typeof routeFetch>) {
    const posts = fetchMock.mock.calls.filter(([url]) => url === '/api/transcripts/import')
    return Promise.all(
      posts.map(async ([, init]) => {
        const file = (init!.body as FormData).get('file') as File
        return { name: file.name, type: file.type, text: await file.text() }
      }),
    )
  }

  it('hides the sample panel without a manifest, and the drop zone still works', async () => {
    const fetchMock = routeFetch()
    const wrapper = mount(TranscriptDrop)
    expect(wrapper.find('[data-testid="transcript-folders"]').exists()).toBe(false)
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json', { type: 'application/json' })))
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="transcript-summary"]').exists()).toBe(true)
  })

  it('imports a clicked sample: fetches its file, then posts it like an upload', async () => {
    const fetchMock = routeFetch()
    const wrapper = mountWith()
    expect(wrapper.text()).toContain(manifest.notice)
    await sampleButton(wrapper, 0).trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/transcripts/gi/crc.json', '/api/transcripts/import'])
    expect(await uploads(fetchMock)).toEqual([{ name: 'crc.json', type: 'application/json', text: sampleBody['/transcripts/gi/crc.json'] }])
    expect(wrapper.get('[data-testid="transcript-summary"]').text()).toContain('crc.json')
    expect(wrapper.get('[data-testid="sample-notice"]').text()).toContain('Synthetic sample: Colorectal results')
    expect(sampleButton(wrapper, 0).attributes('aria-pressed')).toBe('true')
  })

  it('imports a sample dropped on the drop zone with the custom type', async () => {
    const fetchMock = routeFetch()
    const wrapper = mountWith()
    await zone(wrapper).trigger('drop', sampleDrop('transcripts/gi/polyp.json'))
    await flushPromises()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/transcripts/gi/polyp.json', '/api/transcripts/import'])
    expect((await uploads(fetchMock))[0]!.text).toBe(sampleBody['/transcripts/gi/polyp.json'])
    expect(wrapper.find('[data-testid="transcript-summary"]').exists()).toBe(true)
  })

  it('ignores a dropped path that is not in the manifest', async () => {
    const fetchMock = routeFetch()
    const wrapper = mountWith()
    await zone(wrapper).trigger('drop', sampleDrop('transcripts/gi/other.json'))
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('replaces a confirmed transcript with a clicked sample and resets the roles', async () => {
    routeFetch()
    const wrapper = mountWith()
    await sampleButton(wrapper, 0).trigger('click')
    await flushPromises()
    expect((wrapper.get('#role-S1').element as HTMLSelectElement).value).toBe('doctor')
    await wrapper.get('#role-S2').setValue('other')
    await confirmButton(wrapper).trigger('click')
    expect(wrapper.text()).toContain('Roles confirmed.')

    await sampleButton(wrapper, 1).trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('Roles confirmed.')
    expect(wrapper.get('[data-testid="transcript-summary"]').text()).toContain('polyp.json')
    expect((wrapper.get('#role-S1').element as HTMLSelectElement).value).toBe('patient')
    expect((wrapper.get('#role-S2').element as HTMLSelectElement).value).toBe('doctor')
    expect(sampleButton(wrapper, 1).attributes('aria-pressed')).toBe('true')
    expect(sampleButton(wrapper, 0).attributes('aria-pressed')).toBe('false')
  })

  it('replaces a transcript in review with a sample dropped on it', async () => {
    const fetchMock = routeFetch()
    const wrapper = mountWith()
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json', { type: 'application/json' })))
    await flushPromises()
    await wrapper.get('#role-S1').setValue('other')

    const review = wrapper.get('[data-testid="transcript-review"]')
    await review.trigger('dragover', sampleDrop('transcripts/gi/polyp.json'))
    await review.trigger('drop', sampleDrop('transcripts/gi/polyp.json'))
    await flushPromises()
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain('/transcripts/gi/polyp.json')
    expect(wrapper.get('[data-testid="transcript-summary"]').text()).toContain('polyp.json')
    expect((wrapper.get('#role-S1').element as HTMLSelectElement).value).toBe('patient')
  })

  it('leaves a transcript in review alone when a plain file is dropped on it', async () => {
    const fetchMock = routeFetch()
    const wrapper = mountWith()
    await zone(wrapper).trigger('drop', drop(new File(['{}'], 'visit.json', { type: 'application/json' })))
    await flushPromises()
    await wrapper.get('[data-testid="transcript-review"]').trigger('drop', drop(new File(['{}'], 'other.json')))
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-testid="transcript-summary"]').text()).toContain('visit.json')
  })

  it('replaces an error with a sample', async () => {
    routeFetch()
    const wrapper = mountWith()
    await zone(wrapper).trigger('drop', drop(new File(['x'], 'slide.png', { type: 'image/png' })))
    await flushPromises()
    expect(wrapper.text()).toContain(transcriptMessageFor('unsupported_transcript_format'))
    await sampleButton(wrapper, 0).trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="transcript-summary"]').exists()).toBe(true)
  })

  it('shows a clear error when the sample file cannot be fetched, without posting', async () => {
    const fetchMock = routeFetch({ sampleStatus: 404 })
    const wrapper = mountWith()
    await sampleButton(wrapper, 0).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(SAMPLE_LOAD_ERROR)
    expect(SAMPLE_LOAD_ERROR).toBe("Couldn't load the sample transcript. Try again.")
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/transcripts/gi/crc.json'])
  })

  it('never touches web storage through samples, roles, confirm and clear', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    routeFetch()
    const wrapper = mountWith()
    await sampleButton(wrapper, 0).trigger('click')
    await flushPromises()
    await confirmButton(wrapper).trigger('click')
    await sampleButton(wrapper, 1).trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text() === 'Clear transcript')!.trigger('click')
    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })
})
