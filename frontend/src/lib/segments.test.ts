import { describe, expect, it } from 'vitest'
import { splitSegments, type SegmentRange } from './segments'
import { anchorComment } from './review'

const w = (text: string, confidence: number | null = 0.95) => ({ text, confidence })
const wordsOf = (text: string, low: Record<string, number> = {}) =>
  text.split(' ').map((t) => w(t, low[t] ?? 0.95))
const range = (text: string, id: string, quote: string, verdict: SegmentRange['verdict'] = 'inaccurate'): SegmentRange => {
  const a = anchorComment({ text }, quote)!
  return { id, start: a.start, end: a.end, verdict }
}
const joined = (runs: { text: string }[]) => runs.map((r) => r.text).join('')

describe('splitSegments', () => {
  it('returns one plain run without ranges or low-confidence words', () => {
    const text = 'Staging tells us how deep.'
    const { runs, aligned } = splitSegments(text, wordsOf(text), [])
    expect(aligned).toBe(true)
    expect(runs).toEqual([
      { text, start: 0, end: text.length, commentIds: [], lowConfidence: null, wordBoundaryAfter: true },
    ])
  })

  it('splits around a single range', () => {
    const text = 'Staging tells us how deep.'
    const { runs } = splitSegments(text, wordsOf(text), [range(text, 'c1', 'tells us')])
    expect(runs.map((r) => [r.text, r.commentIds])).toEqual([
      ['Staging ', []],
      ['tells us', ['c1']],
      [' how deep.', []],
    ])
    expect(runs[1]!.wordBoundaryAfter).toBe(true)
    expect(runs[0]!.wordBoundaryAfter).toBe(false)
  })

  it('stacks nested ranges shortest first (crc-5 contains crc-6)', () => {
    const text = 'Staging tells us how deep the tumor goes and whether the cancer is in the lymph nodes or other organs.'
    const outer = range(text, 'crc-5', text, 'accurate')
    const inner = range(text, 'crc-6', 'how deep the tumor goes', 'incomplete')
    const { runs } = splitSegments(text, wordsOf(text), [outer, inner])
    expect(runs.map((r) => [r.text, r.commentIds])).toEqual([
      ['Staging tells us ', ['crc-5']],
      ['how deep the tumor goes', ['crc-6', 'crc-5']],
      [' and whether the cancer is in the lymph nodes or other organs.', ['crc-5']],
    ])
    expect(joined(runs)).toBe(text)
  })

  it('handles partially overlapping ranges (mel-2 / mel-3 shape)', () => {
    const text = 'The Breslow thickness was 1.2 millimeters, which is how wide the spot was.'
    const a = range(text, 'mel-2', 'The Breslow thickness was 1.2 millimeters,')
    const b = range(text, 'mel-3', '1.2 millimeters, which is how wide the spot was.')
    const { runs } = splitSegments(text, wordsOf(text), [a, b])
    expect(runs.map((r) => [r.text, r.commentIds])).toEqual([
      ['The Breslow thickness was ', ['mel-2']],
      ['1.2 millimeters,', ['mel-2', 'mel-3']],
      [' which is how wide the spot was.', ['mel-3']],
    ])
  })

  it('splits a range that covers part of a low-confidence word', () => {
    const text = 'The growth in your sigmoid colon is cancer.'
    const { runs } = splitSegments(text, wordsOf(text, { sigmoid: 0.52 }), [
      { id: 'c1', start: text.indexOf('sigm') + 4, end: text.indexOf(' is'), verdict: 'incomplete' },
    ])
    expect(runs.map((r) => [r.text, r.commentIds, r.lowConfidence])).toEqual([
      ['The growth in your ', [], null],
      ['sigm', [], 0.52],
      ['oid', ['c1'], 0.52],
      [' colon', ['c1'], null],
      [' is cancer.', [], null],
    ])
  })

  it('finds words with punctuation attached', () => {
    const text = 'Hello, thanks for coming in. How is it healing?'
    const { runs, aligned } = splitSegments(text, wordsOf(text, { 'in.': 0.3, 'healing?': 0.4 }), [])
    expect(aligned).toBe(true)
    expect(runs.filter((r) => r.lowConfidence !== null).map((r) => r.text)).toEqual(['in.', 'healing?'])
  })

  it('falls back to text only when the words do not line up, keeping highlights', () => {
    const text = 'The biopsy shows adenocarcinoma.'
    const words = [w('The'), w('biopsy', 0.2), w('reveals'), w('adenocarcinoma.')]
    const { runs, aligned } = splitSegments(text, words, [range(text, 'c1', 'biopsy')])
    expect(aligned).toBe(false)
    expect(runs.every((r) => r.lowConfidence === null && r.wordBoundaryAfter === undefined)).toBe(true)
    expect(runs.map((r) => [r.text, r.commentIds])).toEqual([
      ['The ', []],
      ['biopsy', ['c1']],
      [' shows adenocarcinoma.', []],
    ])
  })

  it('renders text-only turns (no words) with highlights', () => {
    const text = 'Of course. The biopsy shows an invasive ductal carcinoma.'
    const { runs, aligned } = splitSegments(text, [], [range(text, 'b1', 'invasive ductal carcinoma.')])
    expect(aligned).toBe(true)
    expect(runs.map((r) => [r.text, r.commentIds])).toEqual([
      ['Of course. The biopsy shows an ', []],
      ['invasive ductal carcinoma.', ['b1']],
    ])
    expect(runs[0]!.wordBoundaryAfter).toBeUndefined()
  })

  it('clamps out-of-bounds ranges and ignores empty or inverted ones', () => {
    const text = 'Short text.'
    const { runs } = splitSegments(text, [], [
      { id: 'over', start: 6, end: 99, verdict: 'inaccurate' },
      { id: 'neg', start: -5, end: 5, verdict: 'incomplete' },
      { id: 'empty', start: 3, end: 3, verdict: 'inaccurate' },
      { id: 'inverted', start: 8, end: 2, verdict: 'inaccurate' },
      { id: 'outside', start: 50, end: 60, verdict: 'inaccurate' },
      { id: 'nan', start: Number.NaN, end: 4, verdict: 'inaccurate' },
    ])
    expect(runs.map((r) => [r.text, r.commentIds])).toEqual([
      ['Short', ['neg']],
      [' ', []],
      ['text.', ['over']],
    ])
    expect(joined(runs)).toBe(text)
  })

  it('returns no runs for empty text', () => {
    expect(splitSegments('', [], [])).toEqual({ runs: [], aligned: true })
  })
})
