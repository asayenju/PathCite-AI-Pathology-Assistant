// @vitest-environment node
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { CLASS_CODES, CLASS_NAMES } from './classes'

describe('class list', () => {
  it('matches the backend CLASS_NAMES order exactly', () => {
    const path = fileURLToPath(new URL('../../../backend/app/classes.py', import.meta.url))
    const source = readFileSync(path, 'utf8')
    const tuple = source.slice(source.indexOf('CLASS_NAMES'))
    const backend = [...tuple.matchAll(/^\s*"([A-Z]+)",/gm)].map((m) => m[1])
    expect(backend).toHaveLength(9)
    expect([...CLASS_CODES]).toEqual(backend)
  })

  it('has a readable name for every code', () => {
    for (const code of CLASS_CODES) expect(CLASS_NAMES[code].length).toBeGreaterThan(3)
  })
})

describe('bundled samples', () => {
  const publicDir = fileURLToPath(new URL('../../public/', import.meta.url))
  const { samples } = JSON.parse(readFileSync(`${publicDir}samples/samples.json`, 'utf8')) as {
    samples: { code: string; file: string; sourceRow: number }[]
  }

  it('has the same number of slides in every class folder', () => {
    for (const code of CLASS_CODES) {
      expect(samples.filter((s) => s.code === code), code).toHaveLength(8)
    }
    expect(samples).toHaveLength(CLASS_CODES.length * 8)
  })

  it('stores each slide in its class folder, with a unique path', () => {
    for (const s of samples) expect(s.file.startsWith(`samples/${s.code}/`), s.file).toBe(true)
    expect(new Set(samples.map((s) => s.file)).size).toBe(samples.length)
  })

  it('points at real JPEG files', () => {
    for (const s of samples) {
      const bytes = readFileSync(`${publicDir}${s.file}`)
      expect(bytes.subarray(0, 2).toString('hex'), s.file).toBe('ffd8')
    }
  })
})
