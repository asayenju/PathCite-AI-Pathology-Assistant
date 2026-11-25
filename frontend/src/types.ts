import type { ClassCode } from '@/lib/classes'

/** Mirrors backend/app/schemas.py PredictResponse. */
export interface PredictResponse {
  label: number
  tissue: ClassCode
  confidence: number
  uncertain: boolean
  probabilities: Record<ClassCode, number>
  warnings: string[]
  model_version: string
  notice: string
}

/** A bundled sample slide (public/samples/samples.json). */
export interface Sample {
  code: ClassCode
  file: string
  sourceRow: number
}

/** Mirrors backend/app/transcripts/schemas.py. */
export type TranscriptSource = 'deepgram' | 'aws_transcribe' | 'whisperx' | 'openai_diarized'
export type Role = 'doctor' | 'patient' | 'other'

export interface TranscriptWord {
  text: string
  start: number | null
  end: number | null
  /** Speech-to-text confidence 0-1; null if the vendor gave none. */
  confidence: number | null
}

export interface TranscriptTurn {
  /** Stable turn id, e.g. t0001. */
  id: string
  /** Speaker id, e.g. S1. */
  speaker: string
  start: number | null
  end: number | null
  text: string
  is_final: boolean
  words: TranscriptWord[]
  min_confidence: number | null
}

export interface TranscriptSpeaker {
  /** Stable speaker id, e.g. S1. */
  id: string
  /** The vendor's label, e.g. spk_0, SPEAKER_00, ch_1. */
  source_label: string
  word_count: number
  talk_time_s: number
  preview: string[]
  suggested_role: Role | null
}

export interface TranscriptImport {
  source: TranscriptSource
  language: string | null
  duration_s: number | null
  speakers: TranscriptSpeaker[]
  turns: TranscriptTurn[]
  warnings: string[]
  notice: string
}

/** A turn with its speaker's assigned role: the input for later steps (e.g. the agent review). */
export interface RoledTurn extends TranscriptTurn {
  role: Role | null
}

/** One bundled sample transcript (public/transcripts/index.json). Synthetic, not a real patient. */
export interface TranscriptSample {
  id: string
  title: string
  /** e.g. deepgram, aws_medical, whisperx, openai_diarized. */
  vendor: string
  /** Path relative to public/, e.g. transcripts/gi/crc_adenocarcinoma.json. Unique. */
  file: string
}

export interface TranscriptSampleFolder {
  name: string
  slug: string
  items: TranscriptSample[]
}

/** public/transcripts/index.json, written by backend/tests/fixtures/transcripts/generate.py. */
export interface TranscriptManifest {
  notice: string
  folders: TranscriptSampleFolder[]
}

/*
 * Transcript review contract (docs/PRD-transcript-review-ui.md).
 *
 * A ReviewSource streams ReviewEvents for the doctor turns of a roled transcript. Today the only source is the
 * hand-written mock in src/mocks/review/. A later backend endpoint will stream the same ReviewEvent objects over
 * SSE, one JSON object per `data:` message, so the real agent drops in as another ReviewSource with no UI change.
 */

export type ReviewVerdict = 'inaccurate' | 'incomplete' | 'accurate'

/**
 * One agent comment on a span of a single turn. The span is located by `turn_id` + `quote`; `start`/`end` are
 * character offsets into that turn's `text` (end exclusive), found with `anchorComment` (first exact match after NFC).
 * Both are null for an orphan, a comment whose quote isn't in the turn: it gets a card but no highlight.
 * Sources fill start/end before emitting (the mock anchors against the turn it was given); a server may send nulls,
 * and consumers can always re-anchor from the quote.
 */
export interface ReviewComment {
  id: string
  turn_id: string
  quote: string
  start: number | null
  end: number | null
  verdict: ReviewVerdict
  /** What's wrong or missing; null for accurate. */
  issue: string | null
  /** Better wording; null for accurate. */
  suggestion: string | null
  /** e.g. "AJCC 8th edition melanoma". */
  source: string | null
}

/**
 * Per doctor turn, in transcript order: turn_started, then its comment events, then turn_done; finally done.
 * `error` ends the stream instead of `done`. Aborting the signal ends it with no further events.
 */
export type ReviewEvent =
  | { type: 'turn_started'; turn_id: string }
  | { type: 'comment'; comment: ReviewComment }
  | { type: 'turn_done'; turn_id: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

export type ReviewSource = (turns: RoledTurn[], signal: AbortSignal) => AsyncIterable<ReviewEvent>

/** Answers a reply on a comment. Rejects with an AbortError (DOMException) when the signal aborts. */
export type ClarifySource = (comment: ReviewComment, question: string, signal: AbortSignal) => Promise<string>
