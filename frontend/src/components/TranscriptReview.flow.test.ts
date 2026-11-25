import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TranscriptDrop from './TranscriptDrop.vue'
import { DEMO_REVIEW_LABEL, downloadJson } from '@/lib/review'
import { parseTranscriptManifest } from '@/lib/transcriptSamples'
import { DEFAULT_TURN_DELAY_MS } from '@/mocks/review'
import crcImport from '@/mocks/review/__fixtures__/crc_adenocarcinoma.import.json'
import rawManifest from '../../public/transcripts/index.json'
import type { TranscriptImport, TranscriptManifest } from '@/types'

vi.mock('@/lib/review', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/review')>()),
  downloadJson: vi.fn(),
}))

/** The real manifest, so the mocked sample ids are the ones the app ships. */
const manifest = parseTranscriptManifest(rawManifest) as TranscriptManifest
const items = manifest.folders.flatMap((f) => f.items)
const crc = items.find((i) => i.id === 'crc_adenocarcinoma')!
const unmocked = items.find((i) => i.id === 'tubular_adenoma')!

const crcImported = crcImport as TranscriptImport
/** Any import answer for the non-mocked sample and uploads: same shape, the review never runs on it. */
const otherImported: TranscriptImport = { ...crcImported, source: 'aws_transcribe' }

/** Sample files are served from public/; the import endpoint answers with the crc fixture for the crc sample. */
function routeFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/transcripts/import') {
      const file = (init!.body as FormData).get('file') as File
      return new Response(JSON.stringify(file.name === `${crc.id}.json` ? crcImported : otherImported))
    }
    if (url === `/${crc.file}` || url === `/${unmocked.file}`) {
      return { ok: true, status: 200, blob: async () => new Blob([`{"sample":"${url}"}`]) } as Response
    }
    throw new TypeError(`unexpected fetch ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

type W = ReturnType<typeof mount>
const byText = (w: W, text: string) => w.findAll('button').find((b) => b.text() === text)
const sampleButton = (w: W, title: string) => w.findAll('[data-testid="transcript-folders"] button').find((b) => b.text().includes(title))!
const reviewButton = (w: W) => w.find('[data-testid="review-button"]')
const card = (w: W, id: string) => w.find(`#comment-${id}`)
const mark = (w: W, id: string) => w.find(`[data-anchor-for="${id}"]`)

let wrapper: W | null = null

async function mountWithSample(title: string) {
  routeFetch()
  wrapper = mount(TranscriptDrop, { props: { manifest }, attachTo: document.body })
  await sampleButton(wrapper, title).trigger('click')
  await flushPromises()
  return wrapper
}

async function confirmRoles(w: W) {
  await byText(w, 'Confirm roles')!.trigger('click')
}

/** Lets the mock review finish all doctor turns. */
async function runToEnd() {
  await vi.advanceTimersByTimeAsync(DEFAULT_TURN_DELAY_MS * 20)
  await flushPromises()
}

beforeEach(() => vi.useFakeTimers())

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.mocked(downloadJson).mockClear()
})

describe('Transcripts view: agent review', () => {
  it('hides the review button until roles are confirmed', async () => {
    const w = await mountWithSample(crc.title)
    expect(reviewButton(w).exists()).toBe(false)
    await confirmRoles(w)
    expect(reviewButton(w).exists()).toBe(true)
  })

  it('is disabled with a reason for an uploaded transcript', async () => {
    routeFetch()
    const w = (wrapper = mount(TranscriptDrop, { props: { manifest } }))
    await w.get('[aria-label^="Drop zone."]').trigger('drop', {
      dataTransfer: { types: ['Files'], files: [new File(['{}'], 'visit.json', { type: 'application/json' })] },
    })
    await flushPromises()
    await confirmRoles(w)
    const button = reviewButton(w)
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('aria-describedby')).toBe('review-unavailable')
    expect(w.get('#review-unavailable').text()).toBe("Agent review isn't available for uploaded transcripts yet.")
  })

  it('is disabled with a reason for a sample without a mock review', async () => {
    const w = await mountWithSample(unmocked.title)
    await confirmRoles(w)
    expect(reviewButton(w).attributes('disabled')).toBeDefined()
    expect(reviewButton(w).attributes('aria-describedby')).toBe('review-unavailable')
    expect(w.get('#review-unavailable').text()).toBe('Agent review is only mocked for 3 samples so far.')
  })

  it('reviews a mocked sample progressively, with the demo label, header, highlights and cards', async () => {
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    const button = reviewButton(w)
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.text()).toBe('Review with agent')
    expect(w.find('#review-unavailable').exists()).toBe(false)
    // Not reviewing: the plain transcript keeps its own scroll box.
    expect(w.find('[data-testid="review-scroll"]').exists()).toBe(false)
    expect(w.get('[data-testid="transcript-turns"]').classes()).toContain('overflow-y-auto')

    await button.trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="review-demo-label"]').text()).toBe(DEMO_REVIEW_LABEL)
    expect(w.get('[data-testid="review-status"]').text()).toBe('Reviewing…')
    expect(reviewButton(w).text()).toBe('Restart review')
    // One shared scroll container; the transcript list no longer scrolls on its own.
    expect(w.findAll('[data-testid="review-scroll"]')).toHaveLength(1)
    expect(w.get('[data-testid="transcript-turns"]').classes()).not.toContain('overflow-y-auto')
    expect(w.find('[data-testid="turn-t0001"] [data-testid="reviewing-pill"]').exists()).toBe(true)
    expect(card(w, 'crc-2').exists()).toBe(false)

    // t0001, t0003, t0005 (accurate crc-1: highlight, no card), then t0007 brings crc-2 and crc-3.
    await vi.advanceTimersByTimeAsync(DEFAULT_TURN_DELAY_MS * 3)
    await flushPromises()
    expect(mark(w, 'crc-1').exists()).toBe(true)
    expect(card(w, 'crc-1').exists()).toBe(false)
    expect(card(w, 'crc-2').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(DEFAULT_TURN_DELAY_MS)
    await flushPromises()
    expect(mark(w, 'crc-2').exists()).toBe(true)
    expect(card(w, 'crc-2').exists()).toBe(true)
    expect(card(w, 'crc-4').exists()).toBe(false)

    await runToEnd()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review complete')
    expect(card(w, 'crc-4').exists()).toBe(true)
    expect(card(w, 'crc-8').exists()).toBe(true) // the orphan: a card, no highlight
    expect(mark(w, 'crc-8').exists()).toBe(false)
    // The app-wide research disclaimer lives in App.vue's header; the export carries it too (below).
  })

  it('restart discards the results and runs again', async () => {
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    await reviewButton(w).trigger('click')
    await runToEnd()
    expect(card(w, 'crc-2').exists()).toBe(true)
    await reviewButton(w).trigger('click')
    await flushPromises()
    expect(card(w, 'crc-2').exists()).toBe(false)
    expect(w.get('[data-testid="review-status"]').text()).toBe('Reviewing…')
    await runToEnd()
    expect(card(w, 'crc-2').exists()).toBe(true)
  })

  it('Clear transcript discards the review', async () => {
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    await reviewButton(w).trigger('click')
    await runToEnd()
    await byText(w, 'Clear transcript')!.trigger('click')
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    // Loading the same sample again starts from a clean slate: no header until Review is pressed.
    await sampleButton(w, crc.title).trigger('click')
    await flushPromises()
    await confirmRoles(w)
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    expect(reviewButton(w).text()).toBe('Review with agent')
  })

  it('loading another sample discards the review', async () => {
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    await reviewButton(w).trigger('click')
    await runToEnd()
    await sampleButton(w, unmocked.title).trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    expect(w.find('[data-testid="review-scroll"]').exists()).toBe(false)
  })

  it('Edit roles with results asks first: cancel keeps the review, accept discards it', async () => {
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    await reviewButton(w).trigger('click')
    await runToEnd()

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await byText(w, 'Edit roles')!.trigger('click')
    expect(confirmSpy).toHaveBeenCalledWith('Editing roles discards the current review. Continue?')
    expect(w.text()).toContain('Roles confirmed.')
    expect(card(w, 'crc-2').exists()).toBe(true)

    confirmSpy.mockReturnValue(true)
    await byText(w, 'Edit roles')!.trigger('click')
    expect(confirmSpy).toHaveBeenCalledTimes(2)
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    expect(w.get('#role-S1').attributes('disabled')).toBeUndefined()

    // Confirming again offers a fresh review.
    await confirmRoles(w)
    expect(reviewButton(w).text()).toBe('Review with agent')
  })

  it('Edit roles without results does not ask', async () => {
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    const confirmSpy = vi.spyOn(window, 'confirm')
    await byText(w, 'Edit roles')!.trigger('click')
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('Export review downloads review-<sampleId>-<YYYYMMDD>.json', async () => {
    vi.setSystemTime(new Date(2026, 10, 25, 12))
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    await reviewButton(w).trigger('click')
    await runToEnd()
    await w.get('[data-testid="review-export"]').trigger('click')
    expect(downloadJson).toHaveBeenCalledTimes(1)
    const [filename, data] = vi.mocked(downloadJson).mock.calls[0]!
    expect(filename).toBe('review-crc_adenocarcinoma-20261125.json')
    const exported = data as { notice: string; source: string; sample_id: string; comments: { id: string }[] }
    expect(exported.source).toBe('mock')
    expect(exported.sample_id).toBe('crc_adenocarcinoma')
    expect(exported.notice).toMatch(/Research use only/)
    expect(exported.comments.map((c) => c.id)).toContain('crc-2')
  })

  it('never touches web storage through a review', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const w = await mountWithSample(crc.title)
    await confirmRoles(w)
    await reviewButton(w).trigger('click')
    await runToEnd()
    await card(w, 'crc-2').get('[data-testid="resolve"]').trigger('click')
    await w.get('[data-testid="review-export"]').trigger('click')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await byText(w, 'Edit roles')!.trigger('click')
    await byText(w, 'Clear transcript')!.trigger('click')
    expect(setItem).not.toHaveBeenCalled()
  })
})
