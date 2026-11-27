import type { PredictResponse } from '@/types'

/** In dev, Vite proxies /api to the backend. Set VITE_API_BASE when the backend is hosted elsewhere. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
/** The backend's default UNCERTAIN_THRESHOLD, for drawing the tick. `uncertain` in the response is authoritative. */
export const UNCERTAIN_THRESHOLD = 0.7
export const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/tiff']

/** A request to the backend failed. `code` is the backend's error code, or `network` / `unknown`. */
export class PredictError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'PredictError'
    this.code = code
  }
}

/** What went wrong and what to do next, keyed by the backend's error `code`. */
const MESSAGES: Record<string, string> = {
  file_too_large: 'This file is over 10 MB. Use a smaller image.',
  unsupported_type: 'Only PNG, JPEG and TIFF images work. Pick one of those.',
  empty_file: 'The file is empty. Pick a different image.',
  corrupt_image: "The image couldn't be read. Export it again and retry.",
  image_too_large: 'The image has too many pixels. Crop it to a single patch first.',
  invalid_request: 'The upload was not recognised. Try again.',
  model_unavailable: 'The model is not loaded on the server. Check the backend logs.',
  internal_error: 'The server hit an error. Try again.',
  network: "Can't reach the backend. Start it with uvicorn on port 8000.",
  // Transcript import (POST /transcripts/import). Size and empty-file messages for transcripts live in
  // lib/transcripts.ts, so the image wording above stays correct.
  invalid_json: "The file isn't valid JSON. Export the transcript again as JSON and retry.",
  unsupported_transcript_format:
    'This transcript format is not supported. Use JSON from Deepgram, AWS Transcribe, WhisperX or OpenAI (diarized).',
  undiarized_transcript: 'This transcript has no speaker labels. Re-run it with speaker diarization turned on.',
  empty_transcript: 'This transcript has no words in it. Check that the audio was transcribed, then export again.',
  // Jev triage (POST /triage and /triage/span). Mid-stream errors arrive as {type: "error", code} with the same codes.
  triage_unavailable:
    "Triage isn't available: the server has no TypeSafe key, or TypeSafe rejected it. Set JEV_KEY in the root .env and restart uvicorn.",
  triage_rate_limited: 'TypeSafe is rate-limiting triage requests. Wait a minute, then try again.',
  triage_upstream_error: "TypeSafe (Jev) didn't answer in time or sent a bad answer. Try again.",
  invalid_triage_request:
    'This transcript has no doctor turns to triage, or is too long. Check the roles, or use a shorter transcript.',
  // RAG claim checker (POST /check).
  check_unavailable:
    "Fact-checking isn't available: the server is missing its OpenAI or Supabase key. Set them in the root .env and restart uvicorn.",
  check_rate_limited: 'The fact-checker is being rate-limited. Wait a minute, then try again.',
  check_upstream_error: "OpenAI, TypeSafe or Supabase didn't answer in time or sent a bad answer. Try again.",
  invalid_check_request: "This claim couldn't be sent to the checker: it is empty or too long.",
}

/** Every backend error code with a message, for tests. */
export const MESSAGE_CODES = Object.keys(MESSAGES)

export function messageFor(code: string, fallback?: string): string {
  return MESSAGES[code] ?? fallback ?? 'Something went wrong. Try again.'
}

type Describe = (code: string, fallback?: string) => string

/**
 * The error for a non-OK response: `{detail, code}` bodies are mapped with `describe`; a non-JSON 5xx (e.g. the dev
 * proxy answering for a backend that is down) becomes `network`, any other non-JSON reply `unknown`.
 */
export async function errorFromResponse(response: Response, describe: Describe = messageFor): Promise<PredictError> {
  let payload: { code?: string; detail?: string } | null = null
  try {
    payload = await response.json()
  } catch {
    // Not JSON.
  }
  if (!payload?.code) {
    if (response.status >= 500) return new PredictError('network', describe('network'))
    return new PredictError('unknown', `Request failed (${response.status}).`)
  }
  return new PredictError(payload.code, describe(payload.code, payload.detail))
}

/**
 * POST one file as multipart field `file` and parse the JSON reply.
 * Fetch failures and non-JSON 5xx become `network`; `{detail, code}` errors are mapped with `describe`.
 * Aborts are rethrown untouched.
 */
export async function postFile<T>(
  path: string,
  file: File,
  signal: AbortSignal | undefined,
  describe: Describe = messageFor,
): Promise<T> {
  const body = new FormData()
  body.append('file', file)

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { method: 'POST', body, signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new PredictError('network', describe('network'))
  }

  if (response.ok) return (await response.json()) as T
  throw await errorFromResponse(response, describe)
}

/** Cheap checks before uploading. The server stays the authority. */
export function validateFile(file: File): PredictError | null {
  if (file.size === 0) return new PredictError('empty_file', messageFor('empty_file'))
  if (file.size > MAX_UPLOAD_BYTES) return new PredictError('file_too_large', messageFor('file_too_large'))
  if (file.type && !ACCEPTED_TYPES.includes(file.type)) {
    return new PredictError('unsupported_type', messageFor('unsupported_type'))
  }
  return null
}

export async function predict(file: File, signal?: AbortSignal): Promise<PredictResponse> {
  const invalid = validateFile(file)
  if (invalid) throw invalid
  return postFile<PredictResponse>('/predict', file, signal)
}
