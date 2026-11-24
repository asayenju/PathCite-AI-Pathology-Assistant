import { PredictError, messageFor, postFile } from '@/lib/api'
import type { TranscriptImport, TranscriptSource } from '@/types'

/** Mirrors the backend's default MAX_TRANSCRIPT_MB (20). */
export const MAX_TRANSCRIPT_BYTES = 20 * 1024 * 1024
export const TRANSCRIPT_ACCEPT = '.json,application/json'

/** Codes the transcript endpoint can return, plus `network` for an unreachable backend. */
export const TRANSCRIPT_ERROR_CODES = [
  'file_too_large',
  'empty_file',
  'invalid_json',
  'unsupported_transcript_format',
  'undiarized_transcript',
  'empty_transcript',
  'invalid_request',
  'internal_error',
  'network',
] as const

/** Where the shared (image) wording would be wrong for a transcript. */
const TRANSCRIPT_MESSAGES: Record<string, string> = {
  file_too_large: 'This transcript is over 20 MB. Export a shorter visit or split the file.',
  empty_file: 'The file is empty. Pick a transcript JSON file.',
  invalid_request: 'The transcript upload was not recognised. Try again.',
}

export function transcriptMessageFor(code: string, fallback?: string): string {
  return TRANSCRIPT_MESSAGES[code] ?? messageFor(code, fallback)
}

export const SOURCE_NAMES: Record<TranscriptSource, string> = {
  deepgram: 'Deepgram',
  aws_transcribe: 'AWS Transcribe',
  whisperx: 'WhisperX',
  openai_diarized: 'OpenAI (diarized)',
}

const fail = (code: string) => new PredictError(code, transcriptMessageFor(code))

/** Cheap checks before uploading. The server sniffs the content and stays the authority. */
export function validateTranscriptFile(file: File): PredictError | null {
  if (file.size === 0) return fail('empty_file')
  if (file.size > MAX_TRANSCRIPT_BYTES) return fail('file_too_large')
  const looksJson = file.name.toLowerCase().endsWith('.json') || file.type === 'application/json'
  if (!looksJson) return fail('unsupported_transcript_format')
  return null
}

export async function importTranscript(file: File, signal?: AbortSignal): Promise<TranscriptImport> {
  const invalid = validateTranscriptFile(file)
  if (invalid) throw invalid
  return postFile<TranscriptImport>('/transcripts/import', file, signal, transcriptMessageFor)
}
