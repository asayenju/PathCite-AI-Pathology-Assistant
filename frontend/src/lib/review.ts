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

/** The research-use disclaimer that every exported review carries. */
export const RESEARCH_USE_NOTICE = 'Research use only, not a diagnosis.'

/** Badge text for a review from the hand-written mock (src/mocks/review, test data only). */
export const DEMO_REVIEW_LABEL = 'Demo review: hand-written mock, not an AI agent'

/** The notice at the top of an exported mock review. */
export const REVIEW_EXPORT_NOTICE = `${DEMO_REVIEW_LABEL}. ${RESEARCH_USE_NOTICE}`

/** Badge text shown in the review header for Jev triage (docs/PRD-jev-triage.md), the app's review source. */
export const JEV_REVIEW_LABEL =
  'Triage by Jev (TypeSafe). Claims are not fact-checked yet. Doctor turns are sent to TypeSafe.'

/** The notice at the top of an exported Jev review. */
export const JEV_REVIEW_EXPORT_NOTICE = `${JEV_REVIEW_LABEL} ${RESEARCH_USE_NOTICE}`

/** The export notice for a review source name (useTranscriptReview's `sourceName`): Jev's, else the mock's. */
export function reviewExportNotice(sourceName: string | undefined): string {
  return sourceName === 'jev' ? JEV_REVIEW_EXPORT_NOTICE : REVIEW_EXPORT_NOTICE
}

/** `review-<sampleId or "transcript">-<YYYYMMDD>.json`, using the local date. Unsafe id characters become `_`. */
export function reviewFilename(sampleId: string | null, now: Date): string {
  const id = sampleId ? sampleId.replace(/[^A-Za-z0-9_-]/g, '_') : 'transcript'
  const pad = (n: number) => String(n).padStart(2, '0')
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  return `review-${id}-${date}.json`
}

/** Saves `data` as a pretty-printed JSON file in the browser (Blob + object URL + anchor click). Nothing is uploaded. */
export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  a.style.display = 'none'
  document.body.appendChild(a)
  try {
    a.click()
  } finally {
    a.remove()
    URL.revokeObjectURL(url)
  }
}
