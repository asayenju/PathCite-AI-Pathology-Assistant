import { afterEach, describe, expect, it, vi } from 'vitest'
import { anchorComment, downloadJson, reviewFilename } from './review'

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

describe('reviewFilename', () => {
  const now = new Date(2026, 0, 5, 23, 59)

  it('uses the sample id and the local date', () => {
    expect(reviewFilename('crc_adenocarcinoma', now)).toBe('review-crc_adenocarcinoma-20260105.json')
  })

  it('falls back to "transcript" and sanitises unsafe characters', () => {
    expect(reviewFilename(null, now)).toBe('review-transcript-20260105.json')
    expect(reviewFilename('a/b c', now)).toBe('review-a_b_c-20260105.json')
  })
})

describe('downloadJson', () => {
  const original = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL }
  afterEach(() => {
    vi.restoreAllMocks()
    Object.assign(URL, original)
  })

  it('clicks a download link for a JSON blob and revokes the URL', async () => {
    let blob: Blob | null = null
    const create = vi.fn((b: Blob) => {
      blob = b
      return 'blob:test'
    })
    const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this)
    })

    downloadJson('review-x-20260105.json', { a: 1 })

    expect(clicked).toHaveLength(1)
    expect(clicked[0]!.download).toBe('review-x-20260105.json')
    expect(clicked[0]!.getAttribute('href')).toBe('blob:test')
    expect(clicked[0]!.isConnected).toBe(false)
    expect(revoke).toHaveBeenCalledWith('blob:test')
    expect(blob!.type).toBe('application/json')
    expect(JSON.parse(await blob!.text())).toEqual({ a: 1 })
  })
})
