import type { PredictResponse } from '@/types'

/** In dev, Vite proxies /api to the backend. Set VITE_API_BASE when the backend is hosted elsewhere. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
/** The backend's default UNCERTAIN_THRESHOLD, for drawing the tick. `uncertain` in the response is authoritative. */
export const UNCERTAIN_THRESHOLD = 0.7
export const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/tiff']

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
}

export function messageFor(code: string, fallback?: string): string {
  return MESSAGES[code] ?? fallback ?? 'Something went wrong. Try again.'
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

  const body = new FormData()
  body.append('file', file)

  let response: Response
  try {
    response = await fetch(`${API_BASE}/predict`, { method: 'POST', body, signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new PredictError('network', messageFor('network'))
  }

  if (response.ok) return (await response.json()) as PredictResponse

  let payload: { code?: string; detail?: string } | null = null
  try {
    payload = await response.json()
  } catch {
    // Not JSON: e.g. the dev proxy answering for a backend that is down.
  }
  if (!payload?.code) {
    if (response.status >= 500) throw new PredictError('network', messageFor('network'))
    throw new PredictError('unknown', `Request failed (${response.status}).`)
  }
  throw new PredictError(payload.code, messageFor(payload.code, payload.detail))
}
