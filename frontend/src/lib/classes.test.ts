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
  it('has one sample per class', async () => {
    const path = fileURLToPath(new URL('../../public/samples/samples.json', import.meta.url))
    const { samples } = JSON.parse(readFileSync(path, 'utf8'))
    expect(samples.map((s: { code: string }) => s.code)).toEqual([...CLASS_CODES])
  })
})
