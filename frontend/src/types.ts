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
 * A ReviewSource streams ReviewEvents for the doctor turns of a roled transcript. Two sources exist: the
 * hand-written mock in src/mocks/review/ (test data) and lib/jevReviewSource.ts, which maps the backend's
 * POST /triage NDJSON stream (docs/PRD-jev-triage.md) onto the same events.
 */

/**
 * The fact-check verdicts (inaccurate, incomplete, accurate) come from the mock agent and, later, the checker.
 * Jev triage adds two: `claim` (a general medical fact, sent to the ClaimChecker) and `transcription` (a span that
 * speech-to-text probably got wrong, which the doctor confirms or corrects). Skipped spans produce no comment.
 */
export type ReviewVerdict = 'inaccurate' | 'incomplete' | 'accurate' | 'claim' | 'transcription'

/** Jev's routing of one span (backend app/triage/decide.py). */
export type TriageKind = 'skip' | 'claim' | 'transcription'
/** What a span is apart from transcription doubts; used when the doctor answers "Yes, it's correct". */
export type TriageBaseKind = 'skip' | 'claim'

/** Jev's decision for a triaged span, as sent by the backend. */
export interface TriageInfo {
  kind: TriageKind
  base_kind: TriageBaseKind
  /** Jev's probability that the span is a medical claim (0-1). */
  p_claim: number
  /** Jev's probability that the span has a speech-to-text error (0-1); null when it wasn't asked. */
  p_asr: number | null
}

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
  /**
   * Set on comments from Jev triage (verdict claim or transcription), absent on fact-check comments. Triage
   * comments have no issue, suggestion or source, can't be replied to, and come with start/end from the server,
   * so a sentence repeated within a turn is placed by its offsets rather than by its quote.
   */
  triage?: TriageInfo
}

/**
 * Per doctor turn, in transcript order: turn_started, then its comment (and span_skipped) events, then turn_done;
 * finally done. `error` ends the stream instead of `done`. Aborting the signal ends it with no further events.
 * `span_skipped` is a span Jev triaged as small talk: no comment, only counted. `done` carries the triage model id
 * (e.g. jev-1.14.0) when there is one.
 */
export type ReviewEvent =
  | { type: 'turn_started'; turn_id: string }
  | { type: 'comment'; comment: ReviewComment }
  | { type: 'span_skipped'; turn_id: string; start: number; end: number }
  | { type: 'turn_done'; turn_id: string }
  | { type: 'done'; model?: string }
  | { type: 'error'; message: string }

export type ReviewSource = (turns: RoledTurn[], signal: AbortSignal) => AsyncIterable<ReviewEvent>

/** Answers a reply on a comment. Rejects with an AbortError (DOMException) when the signal aborts. */
export type ClarifySource = (comment: ReviewComment, question: string, signal: AbortSignal) => Promise<string>

/** Jev's answer for one typed span (POST /triage/span): the claim Choice only, no transcription question. */
export interface SpanTriage {
  kind: TriageBaseKind
  p_claim: number
}

/**
 * Triages a doctor's typed correction. `previousDoctorTurns` is the text of up to 2 doctor turns before the span's
 * turn, oldest first. Rejects with an AbortError when the signal aborts, otherwise with a PredictError whose
 * message is readable.
 */
export type SpanTriager = (text: string, previousDoctorTurns: string[], signal: AbortSignal) => Promise<SpanTriage>

/** A claim sent to the checker. `context` is the text of up to 2 doctor turns before the claim's turn. */
export interface ClaimToCheck {
  turn_id: string
  /** The claim as checked: the span's quote, or the doctor's correction when they typed one. */
  quote: string
  context: string[]
}

/**
 * The checker's answer. Only `not_checked` exists while the checker is a stub (lib/claimChecker.ts); the RAG PRD
 * adds real verdicts as further members of this union, e.g. `{ status: 'checked'; verdict: ...; source: ... }`.
 */
export type CheckResult = { status: 'not_checked' }

/** Checks one claim. Rejects with an AbortError (DOMException) when the signal aborts. */
export type ClaimChecker = (claim: ClaimToCheck, signal: AbortSignal) => Promise<CheckResult>
