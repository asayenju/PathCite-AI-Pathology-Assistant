import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TranscriptView from './TranscriptView.vue'
import type { CommentStatus, ReviewThread } from '@/composables/useTranscriptReview'
import { anchorComment } from '@/lib/review'
import { formatClock } from '@/lib/transcripts'
import { breastIdc } from '@/mocks/review/breastIdc'
import { crcAdenocarcinoma } from '@/mocks/review/crcAdenocarcinoma'
import type { MockComment } from '@/mocks/review/types'
import breastImport from '@/mocks/review/__fixtures__/breast_idc.import.json'
import crcImport from '@/mocks/review/__fixtures__/crc_adenocarcinoma.import.json'
import type { Role, TranscriptImport, TranscriptTurn } from '@/types'

const turns: TranscriptTurn[] = [
  {
    id: 't0001',
    speaker: 'S1',
    start: 65.4,
    end: 70,
    text: 'The biopsy shows adenocarcinoma.',
    is_final: true,
    words: [
      { text: 'The', start: 65.4, end: 65.6, confidence: 0.99 },
      { text: 'biopsy', start: 65.6, end: 66, confidence: 0.95 },
      { text: 'shows', start: 66, end: 66.3, confidence: null },
      { text: 'adenocarcinoma.', start: 66.3, end: 67, confidence: 0.41 },
    ],
    min_confidence: 0.41,
  },
  { id: 't0002', speaker: 'S2', start: null, end: null, text: 'What does that mean?', is_final: true, words: [], min_confidence: null },
  { id: 't0003', speaker: 'S3', start: 3, end: 4, text: 'Hm.', is_final: true, words: [], min_confidence: null },
]

const mountView = () => mount(TranscriptView, { props: { turns, roles: { S1: 'doctor', S2: 'patient', S3: null } } })

afterEach(() => vi.unstubAllGlobals())

describe('TranscriptView', () => {
  it('writes the role out on each turn', () => {
    const w = mountView()
    expect(w.get('[data-testid="turn-t0001"] [data-testid="turn-role"]').text()).toBe('Doctor')
    expect(w.get('[data-testid="turn-t0002"] [data-testid="turn-role"]').text()).toBe('Patient')
    expect(w.get('[data-testid="turn-t0003"] [data-testid="turn-role"]').text()).toBe('Unassigned')
    expect(w.get('[data-testid="turn-t0001"]').text()).toContain('S1')
  })

  it('formats timestamps as m:ss, or a dash when unknown', () => {
    const w = mountView()
    expect(w.get('[data-testid="turn-t0001"] [data-testid="turn-time"]').text()).toBe('1:05')
    expect(w.get('[data-testid="turn-t0002"] [data-testid="turn-time"]').text()).toBe('—')
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(125)).toBe('2:05')
  })

  it('underlines only low-confidence words, with the percentage in the tooltip', async () => {
    const w = mountView()
    const low = w.findAll('[data-testid="low-confidence-word"]')
    expect(low).toHaveLength(1)
    expect(low[0]!.classes()).toContain('decoration-dotted')
    expect(low[0]!.text()).toContain('adenocarcinoma.')
    expect(low[0]!.text()).toContain('transcription confidence 41%')
    expect(w.get('[data-testid="turn-t0001"]').text()).toContain('The biopsy shows')
  })

  it('opens the tooltip on focus with "Transcription confidence 41%"', async () => {
    // jsdom has no ResizeObserver; the tooltip's floating positioning needs one.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
    const w = mount(TranscriptView, {
      props: { turns, roles: {} },
      attachTo: document.body,
    })
    await w.get('[data-testid="low-confidence-word"]').trigger('focus')
    await new Promise((r) => setTimeout(r, 0))
    expect(document.body.textContent).toContain('Transcription confidence 41%')
    w.unmount()
  })

  it('renders plain text for turns without words', () => {
    const w = mountView()
    expect(w.get('[data-testid="turn-t0002"]').text()).toContain('What does that mean?')
    expect(w.get('[data-testid="turn-t0002"]').find('[data-testid="low-confidence-word"]').exists()).toBe(false)
  })
})

/** Mock comments anchored against their fixture turns, as the review composable would hold them. */
function threads(imp: TranscriptImport, mocks: MockComment[], status: Record<string, CommentStatus> = {}): ReviewThread[] {
  return mocks.map(({ clarification: _c, ...m }) => {
    const turn = imp.turns.find((t) => t.id === m.turn_id)!
    const a = anchorComment(turn, m.quote)
    return { ...m, start: a?.start ?? null, end: a?.end ?? null, status: status[m.id] ?? 'open', disagreeReason: null, thread: [] }
  })
}
const rolesOf = (imp: TranscriptImport) =>
  Object.fromEntries(imp.speakers.map((s) => [s.id, s.suggested_role])) as Record<string, Role | null>

const crc = crcImport as TranscriptImport
const breast = breastImport as TranscriptImport
const crcComments = threads(crc, crcAdenocarcinoma)

const mountReview = (props: Partial<InstanceType<typeof TranscriptView>['$props']> = {}, imp = crc, comments = crcComments) =>
  mount(TranscriptView, { props: { turns: imp.turns, roles: rolesOf(imp), comments, ...props } })

describe('TranscriptView highlights', () => {
  it('anchors each placed comment once, with data-comment-ids on every mark', () => {
    const w = mountReview()
    for (const c of crcComments) {
      const anchors = w.findAll(`[data-anchor-for="${c.id}"]`)
      expect(anchors).toHaveLength(c.start === null ? 0 : 1)
    }
    expect(w.find('[data-anchor-for="crc-8"]').exists()).toBe(false) // orphan
    const marks = w.findAll('mark')
    expect(marks.length).toBeGreaterThan(0)
    for (const m of marks) expect(m.attributes('data-comment-ids')).toMatch(/^[\w-]+( [\w-]+)*$/)
    const crc2 = w.get('[data-anchor-for="crc-2"]')
    expect(crc2.element.tagName).toBe('MARK')
    expect(crc2.text()).toContain("Because it's moderately differentiated")
    expect(crc2.attributes('aria-describedby')).toBe('comment-crc-2')
    expect(crc2.attributes('tabindex')).toBe('0')
    expect(crc2.classes()).toContain('bg-eosin/15')
    expect(crc2.text()).toContain('(inaccurate statement)')
  })

  it('stacks the crc-5 / crc-6 overlap with a darker tint and shortest-first ids', () => {
    const w = mountReview()
    const marks = w.get('[data-testid="turn-t0011"]').findAll('mark')
    expect(marks.map((m) => m.attributes('data-comment-ids'))).toEqual(['crc-5', 'crc-6 crc-5', 'crc-5'])
    expect(marks[0]!.attributes('data-anchor-for')).toBe('crc-5')
    expect(marks[1]!.attributes('data-anchor-for')).toBe('crc-6')
    expect(marks[1]!.text()).toBe('how deep the tumor goes (incomplete statement)')
    expect(marks[1]!.classes()).toContain('bg-amber/30')
    expect(marks[1]!.attributes('aria-describedby')).toBe('comment-crc-6')
    expect(marks[2]!.text()).toContain('(accurate statement)')
  })

  it('leaves accurate-only marks unfocusable and inert', async () => {
    const w = mountReview()
    const accurate = w.get('[data-anchor-for="crc-1"]')
    expect(accurate.attributes('tabindex')).toBeUndefined()
    expect(accurate.attributes('aria-describedby')).toBeUndefined()
    expect(accurate.classes()).not.toContain('cursor-pointer')
    expect(accurate.findAll('[data-testid="verdict-icon"]')).toHaveLength(1)
    await accurate.trigger('click')
    await accurate.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('activate')).toBeUndefined()
  })

  it('emits activate with the shortest covering issue on click, Enter and Space', async () => {
    const w = mountReview()
    const overlap = w.get('[data-testid="turn-t0011"]').findAll('mark')[1]!
    await overlap.trigger('click')
    await overlap.trigger('keydown', { key: 'Enter' })
    await overlap.trigger('keydown', { key: ' ' })
    await overlap.trigger('keydown', { key: 'a' })
    expect(w.emitted('activate')).toEqual([['crc-6'], ['crc-6'], ['crc-6']])
  })

  it('rings the active comment and fades resolved ones', () => {
    const comments = threads(crc, crcAdenocarcinoma, { 'crc-3': 'resolved' })
    const w = mountReview({ activeCommentId: 'crc-2' }, crc, comments)
    expect(w.get('[data-anchor-for="crc-2"]').classes()).toContain('ring-2')
    const resolved = w.get('[data-anchor-for="crc-3"]')
    expect(resolved.classes()).not.toContain('ring-2')
    expect(resolved.classes()).toContain('bg-eosin/5')
    expect(resolved.attributes('tabindex')).toBe('0')
  })

  it('highlights text-only turns (breast fixture has no words)', () => {
    const comments = threads(breast, breastIdc)
    const w = mountReview({}, breast, comments)
    const brc2 = w.get('[data-anchor-for="brc-2"]')
    expect(brc2.text()).toContain("It's estrogen receptor and progesterone receptor positive, and HER2 negative.")
    expect(w.find('[data-testid="low-confidence-word"]').exists()).toBe(false)
    expect(w.get('[data-testid="turn-t0007"]').text()).toContain(breast.turns.find((t) => t.id === 't0007')!.text)
  })

  it('keeps the low-confidence underline inside a highlight', () => {
    // crc-1 is on t0005; put an issue over the low-confidence "sigmoid" and the planted "colin" there.
    const turn = crc.turns.find((t) => t.id === 't0005')!
    const a = anchorComment(turn, 'your sigmoid colin')!
    const comment: ReviewThread = {
      ...crcComments[1]!,
      id: 'x-1',
      turn_id: 't0005',
      quote: 'your sigmoid colin',
      start: a.start,
      end: a.end,
    }
    const w = mountReview({}, crc, [comment])
    const mark = w.get('[data-anchor-for="x-1"]')
    expect(mark.element.textContent).toBe('your ')
    // Spacing survives the split: the paragraph reads as the turn text plus screen-reader notes.
    const para = w.get('[data-testid="turn-t0005"] p.leading-relaxed').element.textContent
    expect(para).toBe(
      turn.text
        .replace('sigmoid', 'sigmoid (transcription confidence 52%)')
        .replace('colin ', 'colin (transcription confidence 41%) (inaccurate statement) '),
    )
    const low = w.get('[data-testid="turn-t0005"] [data-testid="low-confidence-word"]')
    expect(low.text()).toContain('sigmoid')
    expect(low.text()).toContain('transcription confidence 52%')
    expect(low.element.closest('mark')?.getAttribute('data-comment-ids')).toBe('x-1')
    expect(low.classes()).toContain('decoration-dotted')
  })
})

describe('TranscriptView review progress', () => {
  it('dims doctor turns not reviewed yet and marks the current one', () => {
    const w = mountReview({ reviewing: true, currentTurnId: 't0007', doneTurnIds: ['t0001', 't0003', 't0005'] })
    expect(w.get('[data-testid="turn-t0005"]').classes()).not.toContain('opacity-50')
    expect(w.get('[data-testid="turn-t0007"]').classes()).not.toContain('opacity-50')
    expect(w.get('[data-testid="turn-t0009"]').classes()).toContain('opacity-50')
    expect(w.get('[data-testid="turn-t0008"]').classes()).not.toContain('opacity-50') // patient
    const pills = w.findAll('[data-testid="reviewing-pill"]')
    expect(pills).toHaveLength(1)
    expect(w.get('[data-testid="turn-t0007"] [data-testid="reviewing-pill"]').text()).toBe('Reviewing…')
    expect(pills[0]!.classes()).not.toContain('motion-safe:animate-pulse') // a pulsing pill read as flicker
    expect(pills[0]!.find('svg').classes()).toContain('motion-safe:animate-spin')
    const live = w.get('[data-testid="review-live"]')
    expect(live.attributes('aria-live')).toBe('polite')
    expect(live.text()).toBe('Reviewing turn at 0:32')
  })

  it('does not dim or show a pill before a review starts', () => {
    const w = mountReview({ currentTurnId: 't0007' })
    expect(w.find('[data-testid="reviewing-pill"]').exists()).toBe(false)
    expect(w.findAll('li.opacity-50')).toHaveLength(0)
    expect(w.get('[data-testid="review-live"]').text()).toBe('')
  })

  it('drops the inner scroll box when scroll is false', () => {
    expect(mountReview().get('[data-testid="transcript-turns"]').classes()).toContain('max-h-[32rem]')
    const list = mountReview({ scroll: false }).get('[data-testid="transcript-turns"]')
    expect(list.classes()).not.toContain('max-h-[32rem]')
    expect(list.classes()).not.toContain('overflow-y-auto')
    expect(list.attributes('tabindex')).toBeUndefined()
  })
})
