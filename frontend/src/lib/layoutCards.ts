/** One margin card to place: its highlight's top and its own rendered height, both in px within the margin. */
export interface CardBox {
  id: string
  anchorTop: number
  height: number
}

/**
 * Places margin cards next to their highlights, Google Docs style. Returns each card's top in px.
 *
 * Cards are taken in anchorTop order (ties keep input order, so callers put orphans first and pass the rest in
 * transcript order). With no active card, each card sits at its anchor unless that would overlap the card above,
 * in which case it is pushed down to `gap` below it.
 *
 * With an active card, that card sits exactly at its anchor. Cards after it are pushed down as above; cards before
 * it are pushed up (walking backwards) just enough to clear it. If that would push the first card above 0, the
 * margin's top, everything is clamped back down from 0, so the active card ends up as close below its anchor as
 * fits. No card is ever placed above 0, and in the result no two cards overlap.
 */
export function layoutCards(items: CardBox[], activeId: string | null, gap = 8): Record<string, number> {
  const sorted = items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => a.item.anchorTop - b.item.anchorTop || a.index - b.index)
  const boxes = sorted.map(({ item }) => item)
  const tops = new Array<number>(boxes.length)
  const activeIndex = activeId === null ? -1 : boxes.findIndex((b) => b.id === activeId)

  if (activeIndex === -1) {
    // Plain downward pass; the clamp pass below does all the work.
    boxes.forEach((b, i) => (tops[i] = b.anchorTop))
  } else {
    tops[activeIndex] = boxes[activeIndex].anchorTop
    for (let i = activeIndex + 1; i < boxes.length; i++) {
      const prevBottom = tops[i - 1] + boxes[i - 1].height
      tops[i] = Math.max(boxes[i].anchorTop, prevBottom + gap)
    }
    for (let i = activeIndex - 1; i >= 0; i--) {
      tops[i] = Math.min(boxes[i].anchorTop, tops[i + 1] - gap - boxes[i].height)
    }
  }

  // Never above 0, never overlapping: a no-op for an active layout that already fits.
  for (let i = 0; i < boxes.length; i++) {
    const floor = i === 0 ? 0 : tops[i - 1] + boxes[i - 1].height + gap
    tops[i] = Math.max(tops[i], floor, 0)
  }

  const result: Record<string, number> = {}
  boxes.forEach((b, i) => (result[b.id] = tops[i]))
  return result
}
