import { describe, expect, it } from 'vitest'
import { layoutCards, type CardBox } from './layoutCards'

/** Deterministic PRNG so failures reproduce. */
function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

function expectNoOverlap(items: CardBox[], tops: Record<string, number>, gap: number) {
  const placed = items.map((b) => ({ top: tops[b.id], bottom: tops[b.id] + b.height })).sort((a, b) => a.top - b.top)
  for (let i = 1; i < placed.length; i++) {
    expect(placed[i].top).toBeGreaterThanOrEqual(placed[i - 1].bottom + gap - 1e-9)
  }
  for (const p of placed) expect(p.top).toBeGreaterThanOrEqual(0)
}

describe('layoutCards', () => {
  it('returns an empty layout for no cards', () => {
    expect(layoutCards([], null)).toEqual({})
    expect(layoutCards([], 'x')).toEqual({})
  })

  it('puts a single card at its anchor, active or not', () => {
    expect(layoutCards([{ id: 'a', anchorTop: 120, height: 80 }], null)).toEqual({ a: 120 })
    expect(layoutCards([{ id: 'a', anchorTop: 120, height: 80 }], 'a')).toEqual({ a: 120 })
  })

  it('keeps cards at their anchors when they fit', () => {
    const items = [
      { id: 'a', anchorTop: 0, height: 50 },
      { id: 'b', anchorTop: 100, height: 50 },
    ]
    expect(layoutCards(items, null)).toEqual({ a: 0, b: 100 })
  })

  it('pushes overlapping cards down by the gap', () => {
    const items = [
      { id: 'a', anchorTop: 10, height: 100 },
      { id: 'b', anchorTop: 20, height: 50 },
      { id: 'c', anchorTop: 30, height: 50 },
    ]
    expect(layoutCards(items, null, 8)).toEqual({ a: 10, b: 118, c: 176 })
  })

  it('sorts by anchor and keeps input order on ties', () => {
    const items = [
      { id: 'late', anchorTop: 300, height: 40 },
      { id: 'x', anchorTop: 0, height: 40 },
      { id: 'y', anchorTop: 0, height: 40 },
    ]
    const tops = layoutCards(items, null, 10)
    expect(tops).toEqual({ x: 0, y: 50, late: 300 })
    expect(Object.keys(tops)).toEqual(['x', 'y', 'late'])
    const swapped = layoutCards([items[0], items[2], items[1]], null, 10)
    expect(swapped).toEqual({ y: 0, x: 50, late: 300 })
  })

  it('snaps the active card to its anchor and pushes earlier cards up', () => {
    const items = [
      { id: 'a', anchorTop: 200, height: 60 },
      { id: 'b', anchorTop: 210, height: 60 },
      { id: 'c', anchorTop: 220, height: 60 },
    ]
    // Without active: a 200, b 268, c 336.
    expect(layoutCards(items, null, 8)).toEqual({ a: 200, b: 268, c: 336 })
    const tops = layoutCards(items, 'c', 8)
    expect(tops).toEqual({ a: 84, b: 152, c: 220 })
    expectNoOverlap(items, tops, 8)
  })

  it('pushes earlier cards only as far as needed', () => {
    const items = [
      { id: 'a', anchorTop: 0, height: 40 },
      { id: 'b', anchorTop: 300, height: 60 },
      { id: 'c', anchorTop: 320, height: 60 },
    ]
    expect(layoutCards(items, 'c', 8)).toEqual({ a: 0, b: 252, c: 320 })
  })

  it('clamps at the top and re-pushes downward when the active card cannot fit at its anchor', () => {
    const items = [
      { id: 'a', anchorTop: 0, height: 100 },
      { id: 'b', anchorTop: 20, height: 50 },
    ]
    // b can't sit at 20 with a above it; a stays at 0 and b goes just below.
    expect(layoutCards(items, 'b', 8)).toEqual({ a: 0, b: 108 })
  })

  it('ignores an active id that is not in the list', () => {
    const items = [
      { id: 'a', anchorTop: 10, height: 100 },
      { id: 'b', anchorTop: 20, height: 50 },
    ]
    expect(layoutCards(items, 'zzz', 8)).toEqual(layoutCards(items, null, 8))
  })

  it('never overlaps and keeps the active card at its anchor when there is room (random inputs)', () => {
    const rand = rng(42)
    for (let run = 0; run < 500; run++) {
      const n = 1 + Math.floor(rand() * 12)
      const items: CardBox[] = Array.from({ length: n }, (_, i) => ({
        id: `c${i}`,
        anchorTop: Math.floor(rand() * 1500),
        height: 20 + Math.floor(rand() * 200),
      }))
      const gap = Math.floor(rand() * 16)
      const active = rand() < 0.3 ? null : items[Math.floor(rand() * n)].id
      const tops = layoutCards(items, active, gap)
      expect(Object.keys(tops)).toHaveLength(n)
      expectNoOverlap(items, tops, gap)
      if (active) {
        const box = items.find((b) => b.id === active)!
        // The cards before the active one (in sorted order) need this much room above it.
        const sorted = [...items].sort((a, b) => a.anchorTop - b.anchorTop || items.indexOf(a) - items.indexOf(b))
        const before = sorted.slice(0, sorted.indexOf(box))
        const needed = before.reduce((sum, b) => sum + b.height + gap, 0)
        if (needed <= box.anchorTop) expect(tops[active]).toBe(box.anchorTop)
        else expect(tops[active]).toBe(needed)
      }
      // Order follows anchors (stable): tops are non-decreasing in sorted order.
      const order = [...items].sort((a, b) => a.anchorTop - b.anchorTop || items.indexOf(a) - items.indexOf(b))
      for (let i = 1; i < order.length; i++) expect(tops[order[i].id]).toBeGreaterThan(tops[order[i - 1].id])
    }
  })
})
