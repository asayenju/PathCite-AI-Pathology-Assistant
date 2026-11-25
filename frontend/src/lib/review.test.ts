import { describe, expect, it } from 'vitest'
import { anchorComment } from './review'

const turn = (text: string) => ({ text })

describe('anchorComment', () => {
  it('returns offsets of an exact match', () => {
    const t = turn('The growth is an adenocarcinoma.')
    const a = anchorComment(t, 'an adenocarcinoma')
    expect(a).toEqual({ start: 14, end: 31 })
    expect(t.text.slice(a!.start, a!.end)).toBe('an adenocarcinoma')
  })

  it('uses the first match of a repeated quote', () => {
    const t = turn('moderately differentiated. Because it is moderately differentiated, ...')
    expect(anchorComment(t, 'moderately differentiated')).toEqual({ start: 0, end: 25 })
  })

  it('returns null for a missing quote (orphan) and for an empty quote', () => {
    const t = turn('Next we will remove a wider area of skin.')
    expect(anchorComment(t, 'a wider area of skin around the scar')).toBeNull()
    expect(anchorComment(t, 'Wider area')).toBeNull()
    expect(anchorComment(t, '')).toBeNull()
  })

  it('includes punctuation attached to words', () => {
    const t = turn('The Breslow thickness was 1.2 millimeters, which is how wide the spot was.')
    const a = anchorComment(t, '1.2 millimeters,')!
    expect(t.text.slice(a.start, a.end)).toBe('1.2 millimeters,')
    expect(anchorComment(t, 'the spot was.')).toEqual({ start: 61, end: 74 })
    expect(anchorComment(t, '1.2 millimeters;')).toBeNull()
  })

  it('matches across NFC and NFD forms, with offsets into the original text', () => {
    const composed = 'café au lait macule'
    const decomposed = 'café au lait macule'
    expect(anchorComment(turn(composed), 'café au')).toEqual({ start: 0, end: 7 })
    const a = anchorComment(turn(decomposed), 'café au')!
    expect(a).toEqual({ start: 0, end: 8 })
    expect(decomposed.slice(a.start, a.end)).toBe('café au')
    const b = anchorComment(turn(decomposed), 'au lait')!
    expect(decomposed.slice(b.start, b.end)).toBe('au lait')
  })
})
