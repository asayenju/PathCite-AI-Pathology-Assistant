import { isLowConfidence } from '@/lib/transcripts'
import type { ReviewVerdict, TranscriptWord } from '@/types'

/** A highlighted span of a turn: character offsets into `turn.text`, end exclusive. */
export interface SegmentRange {
  id: string
  start: number
  end: number
  verdict: ReviewVerdict
}

/** A run of a turn's text that has one set of highlights and one low-confidence state throughout. */
export interface Segment {
  text: string
  start: number
  end: number
  /** Ranges covering this run, shortest first (ties by id), so commentIds[0] wins a click. */
  commentIds: string[]
  /** The word's speech-to-text confidence when the run lies inside a low-confidence word, else null. */
  lowConfidence: number | null
  /** True when a word ends exactly where this run ends (only known when the words line up with the text). */
  wordBoundaryAfter?: boolean
}

export interface SplitResult {
  runs: Segment[]
  /** False when the words couldn't be found in the text in order; the runs then carry no confidence info. */
  aligned: boolean
}

interface WordSpan {
  start: number
  end: number
  confidence: number | null
}

/** Finds each word in `text` in order; null when one is missing (the words don't line up with the text). */
function locateWords(text: string, words: Pick<TranscriptWord, 'text' | 'confidence'>[]): WordSpan[] | null {
  const spans: WordSpan[] = []
  let cursor = 0
  for (const word of words) {
    if (!word.text) continue
    const at = text.indexOf(word.text, cursor)
    if (at < 0) return null
    spans.push({ start: at, end: at + word.text.length, confidence: word.confidence })
    cursor = at + word.text.length
  }
  return spans
}

/**
 * Splits a turn's text into runs at every highlight boundary and every low-confidence word boundary.
 * Ranges are clamped to the text; empty, inverted or non-finite ones are ignored. If the words don't line up with
 * the text, highlights are kept and confidence is dropped (`aligned: false`).
 */
export function splitSegments(
  text: string,
  words: Pick<TranscriptWord, 'text' | 'confidence'>[],
  ranges: SegmentRange[],
): SplitResult {
  const len = text.length
  const located = locateWords(text, words)
  const aligned = located !== null
  const spans = located ?? []
  const lowSpans = spans.filter((w) => isLowConfidence(w.confidence))
  const wordEnds = new Set(spans.map((w) => w.end))

  const clamp = (n: number) => Math.min(Math.max(n, 0), len)
  const valid = ranges
    .filter((r) => Number.isFinite(r.start) && Number.isFinite(r.end))
    .map((r) => ({ ...r, start: clamp(r.start), end: clamp(r.end) }))
    .filter((r) => r.start < r.end)

  const cuts = new Set<number>([0, len])
  for (const r of valid) cuts.add(r.start).add(r.end)
  for (const w of lowSpans) cuts.add(w.start).add(w.end)
  const points = [...cuts].sort((a, b) => a - b)

  const runs: Segment[] = []
  for (let i = 0; i < points.length - 1; i++) {
    const start = points[i]!
    const end = points[i + 1]!
    if (start === end) continue
    const commentIds = valid
      .filter((r) => r.start <= start && end <= r.end)
      .sort((a, b) => a.end - a.start - (b.end - b.start) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .map((r) => r.id)
    const low = lowSpans.find((w) => w.start <= start && end <= w.end)
    const run: Segment = {
      text: text.slice(start, end),
      start,
      end,
      commentIds,
      lowConfidence: low ? low.confidence : null,
    }
    if (aligned && spans.length) run.wordBoundaryAfter = wordEnds.has(end)
    runs.push(run)
  }
  return { runs, aligned }
}
