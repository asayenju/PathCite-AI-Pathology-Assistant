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

/** A turn after roles are confirmed: the input for later steps (e.g. fact checking). */
export interface RoledTurn extends TranscriptTurn {
  role: Role | null
}
