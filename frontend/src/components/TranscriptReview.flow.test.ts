import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TranscriptDrop from './TranscriptDrop.vue'
import { messageFor } from '@/lib/api'
import { CHECK_VERDICT_TEXT, NOT_CHECKED_TEXT } from '@/lib/check'
import { JEV_REVIEW_EXPORT_NOTICE, JEV_REVIEW_LABEL, downloadJson } from '@/lib/review'
import { parseTranscriptManifest } from '@/lib/transcriptSamples'
import { stubReducedMotion, unstubMatchMedia } from '@/test-utils/matchMedia'
import crcImport from '@/mocks/review/__fixtures__/crc_adenocarcinoma.import.json'
import rawManifest from '../../public/transcripts/index.json'
import type { CheckedResult, TranscriptImport, TranscriptManifest } from '@/types'

vi.mock('@/lib/review', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/review')>()),
  downloadJson: vi.fn(),
}))

/**
 * End to end through TranscriptDrop with Jev triage: the backend's POST /api/triage NDJSON stream,
 * POST /api/triage/span and POST /api/check are faked at fetch, so nothing goes over the network.
 */

/** The real manifest, so the sample ids are the ones the app ships. */
const manifest = parseTranscriptManifest(rawManifest) as TranscriptManifest
const items = manifest.folders.flatMap((f) => f.items)
const crc = items.find((i) => i.id === 'crc_adenocarcinoma')!
const other = items.find((i) => i.id === 'tubular_adenoma')!

const crcImported = crcImport as TranscriptImport
/** The import answer for every other sample and for uploads: same turns, another vendor. */
const otherImported: TranscriptImport = { ...crcImported, source: 'aws_transcribe' }

const turnText = (id: string) => crcImported.turns.find((t) => t.id === id)!.text

/** A backend span event for `quote` in turn `turnId`, with its real offsets. */
function span(turnId: string, quote: string, kind: 'skip' | 'claim' | 'transcription', baseKind: 'skip' | 'claim' = 'claim') {
  const start = turnText(turnId).indexOf(quote)
  if (start < 0) throw new Error(`"${quote}" is not in ${turnId}`)
  return {
    type: 'span',
    turn_id: turnId,
    start,
    end: start + quote.length,
    quote,
    kind,
    base_kind: kind === 'skip' ? 'skip' : baseKind,
    p_claim: kind === 'skip' ? 0.03 : 0.9,
    p_asr: kind === 'transcription' ? 0.91 : 0.04,
  }
}
const idOf = (s: { turn_id: string; start: number }) => `${s.turn_id}:${s.start}`

const GREETING = span('t0001', 'Good morning, thanks for coming back in.', 'skip')
const SEAT = span('t0001', 'Please, have a seat.', 'skip')
const COLIN = span('t0005', 'The growth in your sigmoid colin is an adenocarcinoma.', 'transcription', 'skip')
const GLAND = span('t0005', "That's a cancer that starts in the gland cells lining the colon.", 'claim')
const SPREAD = span('t0007', "Because it's moderately differentiated, that tells us it hasn't spread.", 'claim')
const COLIN_ID = idOf(COLIN)
const GLAND_ID = idOf(GLAND)
const SPREAD_ID = idOf(SPREAD)

/** The fake /triage stream, one turn per step. */
const STEPS: object[][] = [
  [{ type: 'turn_started', turn_id: 't0001' }, GREETING, SEAT, { type: 'turn_done', turn_id: 't0001' }],
  [{ type: 'turn_started', turn_id: 't0005' }, COLIN, GLAND, { type: 'turn_done', turn_id: 't0005' }],
  [{ type: 'turn_started', turn_id: 't0007' }, SPREAD, { type: 'turn_done', turn_id: 't0007' }],
  [{ type: 'done', model: 'jev-1.14.0' }],
]

const encoder = new TextEncoder()

/** One /triage request: the body it was sent and a stream the test feeds step by step. */
interface TriageCall {
  body: { turns: { id: string; text: string }[] }
  step: () => void
  cancelled: boolean
}

let triageCalls: TriageCall[] = []
/** How /triage/span answers; a test can swap in a deferred or an error. */
let spanAnswer: (body: { text: string; previous_doctor_turns: string[] }) => Promise<Response>
let triageStatus: { status: number; code: string } | null = null

/** How long the fake /check takes, so the Checking… state is visible between steps. */
const CHECK_DELAY_MS = 600
/** What the fake /check answers for every claim. */
const CHECKED: CheckedResult = {
  status: 'checked',
  verdict: 'inaccurate',
  issue: 'Differentiation grade describes the cells, not whether the cancer has spread.',
  suggestion: 'Being moderately differentiated describes how the cells look; staging tells us about spread.',
  needs_review: true,
  review_reasons: ['jev_disagrees'],
  sources: [
    {
      chunk_id: 41,
      title: 'Colorectal cancer (NG151)',
      section: 'Recommendations > Staging',
      url: 'https://www.nice.org.uk/guidance/ng151',
      trust: 'high',
      year: 2021,
      quote: 'Grade does not determine stage.',
      stance: 'contradicts',
      luna_stance: 'contradicts',
    },
    {
      chunk_id: 42,
      title: null,
      section: 'Colorectal cancer > Pathology',
      url: 'https://www.wikidoc.org/index.php/Colorectal_cancer',
      trust: 'low',
      year: null,
      quote: 'Moderately differentiated tumours are common.',
      stance: 'unclear',
      luna_stance: 'supports',
    },
  ],
  model: 'gpt-6-luna',
  jev_model: 'jev-1.14.0',
  search_queries: ['the raw claim', 'grade and stage colorectal'],
  confidence: { label: 'supported', p: 0.62, p_verdict: 0.3, agrees: false },
}
/** Every /check request body. */
let checkCalls: { turn_id: string; quote: string; context: string[] }[] = []

/** The fake /check: answers CHECKED after CHECK_DELAY_MS, or rejects with an AbortError when aborted, like fetch. */
function checkResponse(signal: AbortSignal | null | undefined): Promise<Response> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(jsonResponse(CHECKED)), CHECK_DELAY_MS)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(new DOMException('aborted', 'AbortError'))
    })
  })
}

function triageResponse(body: TriageCall['body']): Response {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let next = 0
  const call: TriageCall = {
    body,
    cancelled: false,
    step: () => {
      if (next >= STEPS.length || call.cancelled) return
      controller.enqueue(encoder.encode(STEPS[next]!.map((e) => JSON.stringify(e) + '\n').join('')))
      if (++next === STEPS.length) controller.close()
    },
  }
  triageCalls.push(call)
  const stream = new ReadableStream<Uint8Array>({
    start: (c) => void (controller = c),
    cancel: () => void (call.cancelled = true),
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })
}

const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

/** Samples are served from public/, the import endpoint answers with the crc fixture, triage is faked. */
function routeFetch() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/transcripts/import') {
      const file = (init!.body as FormData).get('file') as File
      return jsonResponse(file.name === `${crc.id}.json` ? crcImported : otherImported)
    }
    if (url === '/api/triage') {
      if (triageStatus) return jsonResponse({ detail: 'x', code: triageStatus.code }, triageStatus.status)
      return triageResponse(JSON.parse(init!.body as string))
    }
    if (url === '/api/triage/span') return spanAnswer(JSON.parse(init!.body as string))
    if (url === '/api/check') {
      checkCalls.push(JSON.parse(init!.body as string))
      return checkResponse(init!.signal)
    }
    if (items.some((i) => url === `/${i.file}`)) {
      return { ok: true, status: 200, blob: async () => new Blob([`{"sample":"${url}"}`]) } as Response
    }
    throw new TypeError(`unexpected fetch ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

type W = ReturnType<typeof mount>
const byText = (w: W, text: string) => w.findAll('button').find((b) => b.text() === text)
const sampleButton = (w: W, title: string) =>
  w.findAll('[data-testid="transcript-folders"] button').find((b) => b.text().includes(title))!
const reviewButton = (w: W) => w.find('[data-testid="review-button"]')
const card = (w: W, id: string) => w.find(`[id="comment-${id}"]`)
const mark = (w: W, id: string) => w.find(`[data-anchor-for="${id}"]`)
const triageCounts = (w: W) => w.get('[data-testid="review-triage-counts"]').text()
const lastCall = () => triageCalls[triageCalls.length - 1]!

let wrapper: W | null = null

async function mountWithSample(title: string) {
  routeFetch()
  wrapper = mount(TranscriptDrop, { props: { manifest }, attachTo: document.body })
  await sampleButton(wrapper, title).trigger('click')
  await flushPromises()
  return wrapper
}

async function mountWithUpload() {
  routeFetch()
  const w = (wrapper = mount(TranscriptDrop, { props: { manifest }, attachTo: document.body }))
  await w.get('[aria-label^="Drop zone."]').trigger('drop', {
    dataTransfer: { types: ['Files'], files: [new File(['{}'], 'visit.json', { type: 'application/json' })] },
  })
  await flushPromises()
  return w
}

/** The select of the speaker currently marked doctor. */
function doctorSelect(w: W) {
  return w.findAll('select').find((sel) => (sel.element as HTMLSelectElement).value === 'doctor')!
}

async function settle() {
  for (let i = 0; i < 3; i++) {
    await flushPromises()
    await vi.advanceTimersByTimeAsync(0)
  }
}

async function startReview(w: W) {
  await reviewButton(w).trigger('click')
  await settle()
}

/** Sends the next turn of the fake stream. */
async function step() {
  lastCall().step()
  await settle()
}

/** Sends the rest of the stream and lets the fake checker finish. */
async function runToEnd() {
  for (let i = 0; i < STEPS.length; i++) lastCall().step()
  await settle()
  await vi.advanceTimersByTimeAsync(CHECK_DELAY_MS)
  await settle()
}

/** Activates a card (as a click on it does) so its controls are reachable. */
async function open(w: W, id: string) {
  await card(w, id).trigger('click')
  await settle()
}

beforeEach(() => {
  vi.useFakeTimers()
  // Most tests step the stream turn by turn and want each step shown at once: no pacing (see 'paced reveal').
  stubReducedMotion(true)
  triageCalls = []
  checkCalls = []
  triageStatus = null
  spanAnswer = async () => jsonResponse({ kind: 'claim', p_claim: 0.8 })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.mocked(downloadJson).mockClear()
  unstubMatchMedia()
})

describe('Transcripts view: paced reveal (issue #39)', () => {
  const pillTurn = (w: W) => w.find('[data-testid="reviewing-pill"]').element.closest('[data-turn-id]')?.getAttribute('data-turn-id')
  const dimmed = (w: W, id: string) => w.get(`[data-testid="turn-${id}"]`).attributes('data-dimmed') !== undefined

  it('reveals Jev results turn by turn: the marker moves down and later turns stay dimmed', async () => {
    stubReducedMotion(false)
    const w = await mountWithSample(crc.title)
    await startReview(w)

    // Jev sends the first two turns at once.
    lastCall().step()
    lastCall().step()
    await settle()
    expect(pillTurn(w)).toBe('t0001')
    expect(dimmed(w, 't0005')).toBe(true)
    expect(card(w, GLAND_ID).exists()).toBe(false)

    // t0001 only has skips: a 350 ms quiet dwell, then the marker moves on and t0005's results appear.
    await vi.advanceTimersByTimeAsync(349)
    expect(pillTurn(w)).toBe('t0001')
    await vi.advanceTimersByTimeAsync(1)
    await settle()
    expect(pillTurn(w)).toBe('t0005')
    expect(dimmed(w, 't0005')).toBe(false)
    expect(dimmed(w, 't0007')).toBe(true)
    expect(card(w, GLAND_ID).exists()).toBe(true)
    expect(card(w, COLIN_ID).exists()).toBe(true)

    // t0005 has two cards: 300 fade + 120 stagger + 200 slide + 400 reading = 1020 ms.
    lastCall().step()
    lastCall().step()
    await settle()
    await vi.advanceTimersByTimeAsync(1019)
    expect(pillTurn(w)).toBe('t0005')
    expect(card(w, SPREAD_ID).exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    await settle()
    expect(pillTurn(w)).toBe('t0007')
    expect(card(w, SPREAD_ID).exists()).toBe(true)

    await vi.advanceTimersByTimeAsync(900)
    await settle()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review complete')
  })

  it('shows everything at once with prefers-reduced-motion', async () => {
    stubReducedMotion(true)
    const w = await mountWithSample(crc.title)
    await startReview(w)
    for (let i = 0; i < STEPS.length; i++) lastCall().step()
    await settle()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review complete')
    expect(card(w, SPREAD_ID).exists()).toBe(true)
  })

  it('Stop during a dwell ends the reveal at once and leaves no pacing timer behind', async () => {
    stubReducedMotion(false)
    const w = await mountWithSample(crc.title)
    await startReview(w)
    for (let i = 0; i < STEPS.length; i++) lastCall().step()
    await settle()
    await byText(w, 'Stop')!.trigger('click')
    await settle()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review stopped')
    await vi.advanceTimersByTimeAsync(5000)
    await settle()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review stopped')
    expect(card(w, GLAND_ID).exists()).toBe(false)
  })
})

describe('Transcripts view: Jev review availability', () => {
  it('offers review straight after import (no confirm step) and needs a doctor', async () => {
    const w = await mountWithSample(crc.title)
    expect(byText(w, 'Confirm roles')).toBeUndefined()
    expect(reviewButton(w).attributes('disabled')).toBeUndefined()
    await doctorSelect(w).setValue('patient')
    expect(reviewButton(w).attributes('disabled')).toBeDefined()
    expect(reviewButton(w).attributes('aria-describedby')).toBe('review-unavailable')
    expect(w.get('#review-unavailable').text()).toBe('Mark at least one speaker as the doctor to run a review.')
  })

  it('is enabled for an uploaded transcript and sends only its doctor turns to /api/triage', async () => {
    const w = await mountWithUpload()
    expect(reviewButton(w).attributes('disabled')).toBeUndefined()
    expect(w.find('#review-unavailable').exists()).toBe(false)
    await startReview(w)
    expect(triageCalls).toHaveLength(1)
    const sent = lastCall().body.turns.map((t) => t.id)
    const doctorIds = crcImported.turns.filter((t) => t.speaker === 'S1').map((t) => t.id)
    expect(sent).toEqual(doctorIds)
    expect(lastCall().body.turns.every((t) => Object.keys(t).every((k) => ['id', 'text', 'words'].includes(k)))).toBe(
      true,
    )
    await runToEnd()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review complete')
    expect(card(w, GLAND_ID).exists()).toBe(true)
  })

  it('is enabled for every bundled sample', async () => {
    const w = await mountWithSample(crc.title)
    // Open every folder (only the first starts open) so each sample can be picked.
    for (const folder of w.findAll('[data-testid="transcript-folders"] button[aria-expanded="false"]')) {
      await folder.trigger('click')
    }
    for (const item of items) {
      await sampleButton(w, item.title).trigger('click')
      await flushPromises()
      expect(reviewButton(w).attributes('disabled'), item.id).toBeUndefined()
    }
  })
})

describe('Transcripts view: Jev review', () => {
  it('streams triage with the Jev label, triage counts, highlights and cards', async () => {
    const w = await mountWithSample(crc.title)
    expect(reviewButton(w).text()).toBe('Review with agent')
    expect(w.find('[data-testid="review-scroll"]').exists()).toBe(false)

    await startReview(w)
    expect(w.get('[data-testid="review-demo-label"]').text()).toBe(JEV_REVIEW_LABEL)
    expect(w.text()).not.toContain('hand-written mock')
    expect(w.get('[data-testid="review-status"]').text()).toBe('Reviewing…')
    expect(reviewButton(w).text()).toBe('Restart review')
    expect(w.findAll('[data-testid="review-scroll"]')).toHaveLength(1)
    expect(triageCounts(w)).toBe('0 claims · 0 open transcription checks · 0 skipped spans')

    // The greeting turn: both spans skipped, nothing highlighted.
    await step()
    expect(triageCounts(w)).toBe('0 claims · 0 open transcription checks · 2 skipped spans')
    expect(w.findAll('[data-testid="turn-t0001"] mark')).toHaveLength(0)

    await step()
    expect(triageCounts(w)).toBe('1 claim · 1 open transcription check · 2 skipped spans')
    // DOM contract: the anchor and aria-describedby point at the card's id.
    for (const id of [COLIN_ID, GLAND_ID]) {
      expect(mark(w, id).attributes('aria-describedby')).toBe(`comment-${id}`)
      expect(card(w, id).exists()).toBe(true)
    }
    expect(mark(w, COLIN_ID).classes()).toContain('border-dashed')
    expect(mark(w, GLAND_ID).classes()).toContain('bg-hema/15')
    expect(card(w, COLIN_ID).get('[data-testid="transcription-question"]').text()).toBe('Is this correct?')
    expect(card(w, GLAND_ID).get('[data-testid="claim-check"]').text()).toBe('Checking…')

    await runToEnd()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review complete')
    expect(triageCounts(w)).toBe('2 claims · 1 open transcription check · 2 skipped spans')
    expect(card(w, GLAND_ID).get('[data-testid="claim-check"]').text()).toBe(CHECK_VERDICT_TEXT.inaccurate)
  })

  it('checks claims with POST /api/check and shows the verdict, review banner and sources', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    expect(checkCalls).toEqual([
      { turn_id: 't0005', quote: GLAND.quote, context: [turnText('t0001'), turnText('t0003')] },
      { turn_id: 't0007', quote: SPREAD.quote, context: [turnText('t0003'), turnText('t0005')] },
    ])
    const c = card(w, SPREAD_ID)
    expect(c.attributes('data-verdict')).toBe('claim')
    expect(c.get('[data-testid="comment-verdict"]').text()).toBe('Inaccurate')
    expect(c.get('[data-testid="check-confidence"]').text()).toBe("Jev disagrees: thinks it's supported (62%)")
    expect(c.get('[data-testid="comment-issue"]').text()).toBe(CHECKED.issue)
    expect(c.get('[data-testid="check-review-banner"]').text()).toContain('Needs your review')
    expect(c.get('[data-testid="check-review-banner"]').text()).toContain('Jev disagrees with the verdict')
    // The highlight stays a claim highlight, and the DOM contract still holds.
    expect(mark(w, SPREAD_ID).classes()).toContain('bg-hema/15')
    expect(mark(w, SPREAD_ID).attributes('aria-describedby')).toBe(`comment-${SPREAD_ID}`)

    await open(w, SPREAD_ID)
    const details = card(w, SPREAD_ID).get('[data-testid="comment-details"]')
    expect(details.get('[data-testid="comment-suggestion"]').text()).toContain(CHECKED.suggestion!)
    expect(details.findAll('[data-testid^="sources-"] h4').map((h) => h.text())).toEqual([
      'Contradicts (1)',
      'Unclear (1)',
    ])
    expect(details.get('[data-testid="source-opinions"]').text()).toBe('Quote not found in the passage')
    expect(details.get('a').attributes('href')).toBe('https://www.nice.org.uk/guidance/ng151')
  })

  it('shows the checker error on the card', async () => {
    const w = await mountWithSample(crc.title)
    const fetchMock = vi.mocked(fetch)
    const route = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation(async (url, init) =>
      url === '/api/check'
        ? jsonResponse({ detail: 'x', code: 'check_unavailable' }, 503)
        : route(url as string, init),
    )
    await startReview(w)
    await runToEnd()
    const check = card(w, GLAND_ID).get('[data-testid="claim-check"]')
    expect(check.text()).toBe(messageFor('check_unavailable'))
    expect(check.classes()).toContain('text-eosin')
  })

  it('exports the check results', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await w.get('[data-testid="review-export"]').trigger('click')
    const [, data] = vi.mocked(downloadJson).mock.calls[0]!
    const row = (data as { comments: { id: string; check_status: string; check: unknown }[] }).comments.find(
      (c) => c.id === GLAND_ID,
    )!
    const { status: _status, ...check } = CHECKED
    expect(row.check_status).toBe('checked')
    expect(row.check).toEqual(check)
  })

  it('hides Reply on triage cards but keeps Resolve and Disagree', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    for (const id of [COLIN_ID, GLAND_ID, SPREAD_ID]) {
      await open(w, id)
      const c = card(w, id)
      expect(c.find('[data-testid="reply-form"]').exists()).toBe(false)
      expect(c.find('[data-testid="comment-thread"]').exists()).toBe(false)
      expect(c.find('[data-testid="resolve"]').exists()).toBe(true)
      expect(c.find('[data-testid="disagree"]').exists()).toBe(true)
    }
    await card(w, GLAND_ID).get('[data-testid="disagree"]').trigger('click')
    await card(w, GLAND_ID).get('[data-testid="disagree-reason"]').setValue('This is a plan.')
    await card(w, GLAND_ID).get('[data-testid="disagree-form"]').trigger('submit')
    await w.get('[data-testid="review-show-resolved"]').setValue(true)
    expect(card(w, GLAND_ID).get('[data-testid="comment-status"]').text()).toBe('Disagreed')
    await card(w, GLAND_ID).get('[data-testid="reopen"]').trigger('click')
    expect(card(w, GLAND_ID).attributes('data-status')).toBe('open')
  })

  it('Yes on a small-talk span collapses the card to "Not a claim" and keeps focus on it', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await open(w, COLIN_ID)
    const yes = card(w, COLIN_ID).get('[data-testid="transcription-yes"]')
    ;(yes.element as HTMLElement).focus()
    await yes.trigger('click')
    await settle()
    const c = card(w, COLIN_ID)
    expect(c.attributes('data-status')).toBe('resolved')
    expect(c.get('[data-testid="comment-status"]').text()).toBe('Not a claim')
    expect(document.activeElement).toBe(c.element)
    expect(triageCounts(w)).toBe('2 claims · 0 open transcription checks · 2 skipped spans')
    // The transcript text itself is never edited.
    expect(w.get('[data-testid="turn-t0005"]').text()).toContain('colin')
    expect(w.get('[data-testid="turn-t0005"]').text()).not.toContain('sigmoid colon')
  })

  it('No → "What was said?" → Submit: pending, then a claim checked as the typed text', async () => {
    let answer!: (r: Response) => void
    const spanCalls: { text: string; previous_doctor_turns: string[] }[] = []
    spanAnswer = (body) => {
      spanCalls.push(body)
      return new Promise((resolve) => (answer = resolve))
    }
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await open(w, COLIN_ID)

    await card(w, COLIN_ID).get('[data-testid="transcription-no"]').trigger('click')
    const input = card(w, COLIN_ID).get('[data-testid="correction-input"]')
    expect(document.activeElement).toBe(input.element)
    expect(card(w, COLIN_ID).get('[data-testid="correction-submit"]').attributes('disabled')).toBeDefined()

    const typed = 'The growth in your sigmoid colon is an adenocarcinoma.'
    await input.setValue(typed)
    await card(w, COLIN_ID).get('[data-testid="correction-form"]').trigger('submit')
    await settle()
    expect(spanCalls).toEqual([{ text: typed, previous_doctor_turns: [turnText('t0001'), turnText('t0003')] }])
    expect(card(w, COLIN_ID).get('[data-testid="correction-pending"]').text()).toBe('Checking your correction…')
    expect(card(w, COLIN_ID).get('[data-testid="transcription-yes"]').attributes('disabled')).toBeDefined()

    answer(jsonResponse({ kind: 'claim', p_claim: 0.82 }))
    await settle()
    const c = card(w, COLIN_ID)
    expect(c.get('[data-testid="comment-verdict"]').text()).toBe('Claim')
    expect(c.get('[data-testid="claim-checked-as"]').text()).toBe(`Checked as: “${typed}”`)
    expect(c.get('[data-testid="claim-check"]').text()).toBe('Checking…')
    expect(document.activeElement).toBe(c.element)
    expect(w.get('[data-testid="turn-t0005"]').text()).not.toContain('sigmoid colon')
    expect(triageCounts(w)).toBe('3 claims · 0 open transcription checks · 2 skipped spans')

    await vi.advanceTimersByTimeAsync(CHECK_DELAY_MS)
    await settle()
    expect(card(w, COLIN_ID).get('[data-testid="claim-check"]').text()).toBe(CHECK_VERDICT_TEXT.inaccurate)
    expect(checkCalls[checkCalls.length - 1]).toEqual({
      turn_id: 't0005',
      quote: typed,
      context: [turnText('t0001'), turnText('t0003')],
    })
  })

  it('a correction that is small talk resolves the card with the correction shown', async () => {
    spanAnswer = async () => jsonResponse({ kind: 'skip', p_claim: 0.05 })
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await open(w, COLIN_ID)
    await card(w, COLIN_ID).get('[data-testid="transcription-no"]').trigger('click')
    await card(w, COLIN_ID).get('[data-testid="correction-input"]').setValue('  colon  ')
    await card(w, COLIN_ID).get('[data-testid="correction-form"]').trigger('submit')
    await settle()
    const c = card(w, COLIN_ID)
    expect(c.get('[data-testid="comment-status"]').text()).toBe('Not a claim')
    expect(c.get('[data-testid="comment-correction"]').text()).toBe('Correction: “colon”')
  })

  it('a failed correction shows the error and the card stays answerable', async () => {
    spanAnswer = async () => jsonResponse({ detail: 'x', code: 'triage_upstream_error' }, 502)
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await open(w, COLIN_ID)
    await card(w, COLIN_ID).get('[data-testid="transcription-no"]').trigger('click')
    const input = card(w, COLIN_ID).get('[data-testid="correction-input"]')
    await input.setValue('colon')
    ;(input.element as HTMLElement).focus()
    await card(w, COLIN_ID).get('[data-testid="correction-form"]').trigger('submit')
    await settle()
    const c = card(w, COLIN_ID)
    expect(c.get('[data-testid="correction-error"]').text()).toBe(messageFor('triage_upstream_error'))
    expect(c.get('[data-testid="correction-submit"]').attributes('disabled')).toBeUndefined()
    expect(c.get('[data-testid="transcription-yes"]').attributes('disabled')).toBeUndefined()
    expect(document.activeElement).toBe(input.element)
  })

  it('header Previous/Next move across claims and open transcription checks in transcript order', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    const next = w.get('[data-testid="review-next"]')
    expect(next.attributes('aria-label')).toBe('Next claim or transcription check')
    const visited: (string | undefined)[] = []
    for (let i = 0; i < 4; i++) {
      await next.trigger('click')
      visited.push(w.find('[aria-current="true"]').attributes('id'))
    }
    expect(visited).toEqual([COLIN_ID, GLAND_ID, SPREAD_ID, COLIN_ID].map((id) => `comment-${id}`))
    await w.get('[data-testid="review-prev"]').trigger('click')
    expect(w.get('[aria-current="true"]').attributes('id')).toBe(`comment-${SPREAD_ID}`)
  })

  it('shows a Jev error from /api/triage in the header', async () => {
    triageStatus = { status: 503, code: 'triage_unavailable' }
    const w = await mountWithSample(crc.title)
    await startReview(w)
    expect(w.get('[data-testid="review-status"]').text()).toBe(messageFor('triage_unavailable'))
  })

  it('Stop ends the stream and keeps what arrived', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await step()
    await step()
    await w.get('[data-testid="review-stop"]').trigger('click')
    await settle()
    expect(w.get('[data-testid="review-status"]').text()).toBe('Review stopped')
    expect(lastCall().cancelled).toBe(true)
    expect(card(w, GLAND_ID).exists()).toBe(true)
    expect(card(w, SPREAD_ID).exists()).toBe(false)
    // The aborted check ends as not checked.
    expect(card(w, GLAND_ID).get('[data-testid="claim-check"]').text()).toBe(NOT_CHECKED_TEXT)
  })

  it('restart discards the results and runs again', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    expect(card(w, GLAND_ID).exists()).toBe(true)
    await startReview(w)
    expect(triageCalls).toHaveLength(2)
    expect(triageCalls[0]!.cancelled).toBe(false) // it had finished
    expect(card(w, GLAND_ID).exists()).toBe(false)
    expect(w.get('[data-testid="review-status"]').text()).toBe('Reviewing…')
    await runToEnd()
    expect(card(w, GLAND_ID).exists()).toBe(true)
  })

  it('Clear transcript discards the review', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await step()
    await byText(w, 'Clear transcript')!.trigger('click')
    await settle()
    expect(lastCall().cancelled).toBe(true)
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    await sampleButton(w, crc.title).trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    expect(reviewButton(w).text()).toBe('Review with agent')
  })

  it('loading another sample discards the review', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await sampleButton(w, other.title).trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
    expect(w.find('[data-testid="review-scroll"]').exists()).toBe(false)
  })

  it('changing a role with results asks first: cancel keeps the review and the role, accept discards it', async () => {
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()

    const select = doctorSelect(w)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await select.setValue('other')
    expect(confirmSpy).toHaveBeenCalledWith('Changing roles discards the current review. Continue?')
    expect((select.element as HTMLSelectElement).value).toBe('doctor')
    expect(card(w, GLAND_ID).exists()).toBe(true)

    confirmSpy.mockReturnValue(true)
    await select.setValue('other')
    expect((select.element as HTMLSelectElement).value).toBe('other')
    expect(w.find('[data-testid="review-header"]').exists()).toBe(false)
  })

  it('changing a role before any review does not ask', async () => {
    const w = await mountWithSample(crc.title)
    const confirmSpy = vi.spyOn(window, 'confirm')
    await doctorSelect(w).setValue('other')
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('Export review downloads the Jev review with its notice, model and triage fields', async () => {
    vi.setSystemTime(new Date(2026, 10, 25, 12))
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await open(w, COLIN_ID)
    await card(w, COLIN_ID).get('[data-testid="transcription-yes"]').trigger('click')
    await settle()
    await w.get('[data-testid="review-export"]').trigger('click')
    expect(downloadJson).toHaveBeenCalledTimes(1)
    const [filename, data] = vi.mocked(downloadJson).mock.calls[0]!
    expect(filename).toBe('review-crc_adenocarcinoma-20261125.json')
    const exported = data as {
      notice: string
      source: string
      model: string
      sample_id: string
      comments: { id: string; kind: string; confirmed: boolean | null }[]
    }
    expect(exported.notice).toBe(JEV_REVIEW_EXPORT_NOTICE)
    expect(exported.notice).toMatch(/Research use only/)
    expect(exported.source).toBe('jev')
    expect(exported.model).toBe('jev-1.14.0')
    expect(exported.sample_id).toBe('crc_adenocarcinoma')
    expect(exported.comments.find((c) => c.id === COLIN_ID)).toMatchObject({ kind: 'transcription', confirmed: true })
  })

  it('an uploaded transcript exports as review-transcript-<date>.json', async () => {
    vi.setSystemTime(new Date(2026, 10, 25, 12))
    const w = await mountWithUpload()
    await startReview(w)
    await runToEnd()
    await w.get('[data-testid="review-export"]').trigger('click')
    expect(vi.mocked(downloadJson).mock.calls[0]![0]).toBe('review-transcript-20261125.json')
  })

  it('never touches web storage through a review', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const w = await mountWithSample(crc.title)
    await startReview(w)
    await runToEnd()
    await open(w, COLIN_ID)
    await card(w, COLIN_ID).get('[data-testid="transcription-no"]').trigger('click')
    await card(w, COLIN_ID).get('[data-testid="correction-input"]').setValue('colon')
    await card(w, COLIN_ID).get('[data-testid="correction-form"]').trigger('submit')
    await settle()
    await card(w, GLAND_ID).get('[data-testid="resolve"]').trigger('click')
    await w.get('[data-testid="review-export"]').trigger('click')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await doctorSelect(w).setValue('other')
    await byText(w, 'Clear transcript')!.trigger('click')
    expect(setItem).not.toHaveBeenCalled()
  })
})
