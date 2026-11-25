import type { TranscriptTurn } from '@/types'

export interface Anchor {
  start: number
  end: number
}

/** Maps an offset in text.normalize('NFC') back to an offset in text (only needed when text isn't already NFC). */
function toOriginalOffset(text: string, normalizedOffset: number): number {
  let best = 0
  for (let i = 0; i <= text.length; i++) {
    if (text.slice(0, i).normalize('NFC').length <= normalizedOffset) best = i
    else break
  }
  return best
}

/**
 * Locates a comment's quote in its turn: `{start, end}` character offsets into `turn.text` (end exclusive) for the
 * first exact match after NFC-normalising both, or null when the quote isn't there (an orphan).
 */
export function anchorComment(turn: Pick<TranscriptTurn, 'text'>, quote: string): Anchor | null {
  const needle = quote.normalize('NFC')
  if (!needle) return null
  const text = turn.text
  const normalized = text.normalize('NFC')
  const at = normalized.indexOf(needle)
  if (at < 0) return null
  if (normalized === text) return { start: at, end: at + needle.length }
  return { start: toOriginalOffset(text, at), end: toOriginalOffset(text, at + needle.length) }
}
