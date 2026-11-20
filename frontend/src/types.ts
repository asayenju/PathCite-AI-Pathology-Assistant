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
