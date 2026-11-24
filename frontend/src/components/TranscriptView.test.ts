import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TranscriptView from './TranscriptView.vue'
import { formatClock } from '@/lib/transcripts'
import type { TranscriptTurn } from '@/types'

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
