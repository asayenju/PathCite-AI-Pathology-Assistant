// @vitest-environment node
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  MANIFEST_PATH,
  VENDOR_NAMES,
  findSample,
  loadTranscriptManifest,
  parseTranscriptManifest,
  vendorName,
} from './transcriptSamples'

const publicDir = fileURLToPath(new URL('../../public/', import.meta.url))
const raw = JSON.parse(readFileSync(`${publicDir}${MANIFEST_PATH}`, 'utf8'))

afterEach(() => vi.unstubAllGlobals())

describe('bundled sample transcripts (public/transcripts/index.json)', () => {
  const manifest = parseTranscriptManifest(raw)!
  const items = manifest.folders.flatMap((f) => f.items)

  it('parses, with a synthetic notice', () => {
    expect(manifest).not.toBeNull()
    expect(manifest.notice).toMatch(/synthetic/i)
  })

  it('has 15 samples in 5 folders, the last being the checker test cases', () => {
    expect(manifest.folders.map((f) => f.name)).toEqual([
      'GI',
      'Breast',
      'Skin & blood',
      'Prostate & thyroid',
      'Checker test cases',
    ])
    expect(items).toHaveLength(15)
  })

  describe('checker test cases (issue #54, see public/transcripts/TESTING.md)', () => {
    const folder = manifest.folders.find((f) => f.slug === 'checker-tests')!

    type Seg = { speaker?: string | number; speaker_label?: string; transcript?: string; text?: string }

    /** Doctor (speaker 0) turn texts, read the way each vendor format labels speakers. */
    function doctorTurns(vendor: string, doc: Record<string, any>): string[] {
      const pick = (segs: Seg[], isDoctor: (s: Seg) => boolean, text: (s: Seg) => string | undefined) =>
        segs.filter(isDoctor).map((s) => text(s) ?? '')
      switch (vendor) {
        case 'deepgram':
          return pick(doc.results.utterances, (u) => u.speaker === 0, (u) => u.transcript)
        case 'aws_medical':
          return pick(doc.results.audio_segments, (s) => s.speaker_label === 'spk_0', (s) => s.transcript)
        case 'whisperx':
          return pick(doc.segments, (s) => s.speaker === 'SPEAKER_00', (s) => s.text)
        case 'openai_diarized':
          return pick(doc.segments, (s) => s.speaker === 'A', (s) => s.text)
        default:
          throw new Error(`unknown vendor ${vendor}`)
      }
    }

    it('has one conversation per corpus cancer, in at least 3 vendor formats', () => {
      expect(folder.items.map((i) => i.id)).toEqual([
        'check_colorectal',
        'check_breast',
        'check_melanoma',
        'check_dlbcl',
        'check_prostate',
        'check_thyroid',
      ])
      expect(new Set(folder.items.map((i) => i.vendor)).size).toBeGreaterThanOrEqual(3)
    })

    it.each(folder.items.map((i) => [i.id, i] as const))('%s parses and has doctor turns', (_id, item) => {
      const doc = JSON.parse(readFileSync(`${publicDir}${item.file}`, 'utf8'))
      const turns = doctorTurns(item.vendor, doc)
      expect(turns.length).toBeGreaterThanOrEqual(5)
      for (const text of turns) expect(text.trim().length).toBeGreaterThan(0)
    })
  })

  it('has unique ids and file paths, each under its folder', () => {
    expect(new Set(items.map((i) => i.id)).size).toBe(items.length)
    expect(new Set(items.map((i) => i.file)).size).toBe(items.length)
    for (const folder of manifest.folders) {
      for (const item of folder.items) expect(item.file).toBe(`transcripts/${folder.slug}/${item.id}.json`)
    }
  })

  it('points at files that exist and parse as JSON', () => {
    for (const item of items) {
      const path = `${publicDir}${item.file}`
      expect(existsSync(path), item.file).toBe(true)
      expect(() => JSON.parse(readFileSync(path, 'utf8')), item.file).not.toThrow()
    }
  })

  it('names every vendor in a badge', () => {
    for (const item of items) expect(VENDOR_NAMES[item.vendor], item.vendor).toBeDefined()
  })
})

describe('parseTranscriptManifest', () => {
  const item = { id: 'a', title: 'A', vendor: 'deepgram', file: 'transcripts/x/a.json' }
  const ok = { notice: 'Synthetic.', folders: [{ name: 'X', slug: 'x', items: [item] }] }

  it('accepts a well-formed manifest', () => {
    expect(parseTranscriptManifest(ok)).toEqual(ok)
  })

  it.each([
    ['null', null],
    ['an array', []],
    ['no notice', { folders: ok.folders }],
    ['no folders', { notice: 'n' }],
    ['no items at all', { notice: 'n', folders: [{ name: 'X', slug: 'x', items: [] }] }],
    ['an item without a file', { notice: 'n', folders: [{ name: 'X', slug: 'x', items: [{ ...item, file: undefined }] }] }],
    ['a path outside transcripts/', { notice: 'n', folders: [{ name: 'X', slug: 'x', items: [{ ...item, file: 'api/x.json' }] }] }],
    ['a parent-dir path', { notice: 'n', folders: [{ name: 'X', slug: 'x', items: [{ ...item, file: 'transcripts/../a.json' }] }] }],
  ])('rejects %s', (_name, value) => {
    expect(parseTranscriptManifest(value)).toBeNull()
  })

  it('finds a sample by its file path', () => {
    const manifest = parseTranscriptManifest(ok)
    expect(findSample(manifest, item.file)).toEqual(item)
    expect(findSample(manifest, 'transcripts/x/nope.json')).toBeNull()
    expect(findSample(null, item.file)).toBeNull()
  })

  it('shows unknown vendors by their id', () => {
    expect(vendorName('aws_medical')).toBe('AWS Medical')
    expect(vendorName('rev_ai')).toBe('rev_ai')
  })
})

describe('loadTranscriptManifest', () => {
  it('fetches the manifest from the public base path', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(raw))))
    expect(await loadTranscriptManifest()).not.toBeNull()
    expect(vi.mocked(fetch).mock.calls[0]![0]).toBe(`/${MANIFEST_PATH}`)
  })

  it.each([
    ['a network error', async () => Promise.reject(new TypeError('offline'))],
    ['a 404', async () => new Response('nope', { status: 404 })],
    ['non-JSON', async () => new Response('<html>')],
    ['the wrong shape', async () => new Response(JSON.stringify({ samples: [] }))],
  ])('gives null on %s', async (_name, impl) => {
    vi.stubGlobal('fetch', vi.fn(impl))
    expect(await loadTranscriptManifest()).toBeNull()
  })
})
