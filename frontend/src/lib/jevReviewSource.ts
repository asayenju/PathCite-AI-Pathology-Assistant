import type {
  ReviewEvent,
  ReviewSource,
  RoledTurn,
  SpanTriage,
  SpanTriager,
  TriageBaseKind,
  TriageKind,
} from '@/types'
import { API_BASE, PredictError, errorFromResponse, messageFor } from './api'

/**
 * Jev triage over the backend (docs/PRD-jev-triage.md): a ReviewSource for POST /triage, which streams NDJSON, and
 * triageSpan for POST /triage/span. Only doctor turns are sent, with text and word confidences, nothing else.
 */

/** A malformed stream line, a stream cut off before `done`, or a malformed /triage/span answer. */
export const TRIAGE_STREAM_ERROR_MESSAGE = "The triage answer couldn't be read. Try again."

export interface JevOptions {
  /** Injectable for tests. */
  fetchImpl?: typeof fetch
}

/** One turn of the POST /triage body. */
interface TriageTurnBody {
  id: string
  text: string
  words?: { text: string; confidence: number }[]
}

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** Doctor turns only. Words go along with their confidence when the vendor gave one; words without are left out. */
export function triageRequestBody(turns: RoledTurn[]): { turns: TriageTurnBody[] } {
  return {
    turns: turns
      .filter((t) => t.role === 'doctor')
      .map((t) => {
        const words = t.words
          .filter((w) => typeof w.confidence === 'number')
          .map((w) => ({ text: w.text, confidence: w.confidence as number }))
        return words.length ? { id: t.id, text: t.text, words } : { id: t.id, text: t.text }
      }),
  }
}

const KINDS: readonly TriageKind[] = ['skip', 'claim', 'transcription']
const BASE_KINDS: readonly TriageBaseKind[] = ['skip', 'claim']

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isString = (v: unknown): v is string => typeof v === 'string'
const isOffset = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= 0
const isProbability = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * One backend event as a ReviewEvent: claim and transcription spans become comments (id `<turn_id>:<start>`),
 * skip spans `span_skipped`. Returns null for an unknown event type (ignored, so the backend can add events), and
 * throws on a malformed known event.
 */
function toReviewEvent(raw: unknown): ReviewEvent | null {
  if (!isObject(raw) || !isString(raw.type)) throw new Error('malformed event')
  switch (raw.type) {
    case 'turn_started':
    case 'turn_done':
      if (!isString(raw.turn_id)) throw new Error('malformed event')
      return { type: raw.type, turn_id: raw.turn_id }
    case 'span': {
      const { turn_id, start, end, quote, kind, base_kind, p_claim, p_asr } = raw
      if (
        !isString(turn_id) ||
        !isOffset(start) ||
        !isOffset(end) ||
        end < start ||
        !isString(quote) ||
        !KINDS.includes(kind as TriageKind) ||
        !BASE_KINDS.includes(base_kind as TriageBaseKind) ||
        !isProbability(p_claim) ||
        !(p_asr === null || p_asr === undefined || isProbability(p_asr))
      )
        throw new Error('malformed span')
      if (kind === 'skip') return { type: 'span_skipped', turn_id, start, end }
      return {
        type: 'comment',
        comment: {
          id: `${turn_id}:${start}`,
          turn_id,
          quote,
          start,
          end,
          verdict: kind as 'claim' | 'transcription',
          issue: null,
          suggestion: null,
          source: null,
          triage: {
            kind: kind as TriageKind,
            base_kind: base_kind as TriageBaseKind,
            p_claim,
            p_asr: p_asr ?? null,
          },
        },
      }
    }
    case 'done':
      return isString(raw.model) ? { type: 'done', model: raw.model } : { type: 'done' }
    case 'error':
      return { type: 'error', message: messageFor(isString(raw.code) ? raw.code : '') }
    default:
      return null
  }
}

/**
 * A ReviewSource backed by POST /triage. Errors become a single `error` event: a `{detail, code}` reply or an
 * `{type: "error", code}` line gets lib/api's message for the code, an unreachable backend or a dropped connection
 * the start-uvicorn message, and a malformed line or a stream that ends without `done` TRIAGE_STREAM_ERROR_MESSAGE.
 * Aborting the signal aborts the fetch, cancels the body and ends the stream with no further events, like
 * mockReviewSource.
 */
export function jevReviewSource({ fetchImpl = fetch }: JevOptions = {}): ReviewSource {
  return async function* (turns: RoledTurn[], signal: AbortSignal): AsyncGenerator<ReviewEvent> {
    let response: Response
    try {
      response = await fetchImpl(`${API_BASE}/triage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(triageRequestBody(turns)),
        signal,
      })
    } catch (error) {
      if (signal.aborted || isAbort(error)) return
      yield { type: 'error', message: messageFor('network') }
      return
    }
    if (signal.aborted) {
      void response.body?.cancel().catch(() => {})
      return
    }
    if (!response.ok) {
      const error = await errorFromResponse(response)
      if (!signal.aborted) yield { type: 'error', message: error.message }
      return
    }
    if (!response.body) {
      yield { type: 'error', message: TRIAGE_STREAM_ERROR_MESSAGE }
      return
    }

    const reader = response.body.getReader()
    // A fetch that honours the signal errors the body on abort; cancelling covers one that doesn't.
    const onAbort = () => void reader.cancel().catch(() => {})
    signal.addEventListener('abort', onAbort, { once: true })
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      for (;;) {
        let chunk: ReadableStreamReadResult<Uint8Array>
        try {
          chunk = await reader.read()
        } catch (error) {
          if (signal.aborted || isAbort(error)) return
          yield { type: 'error', message: messageFor('network') }
          return
        }
        if (signal.aborted) return
        buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true })
        const lines = buffer.split('\n')
        // Keep the unfinished last line for the next chunk; at the end it is a final line without "\n".
        buffer = chunk.done ? '' : lines.pop()!
        for (const line of lines) {
          if (!line.trim()) continue
          let event: ReviewEvent | null
          try {
            event = toReviewEvent(JSON.parse(line))
          } catch {
            yield { type: 'error', message: TRIAGE_STREAM_ERROR_MESSAGE }
            return
          }
          if (!event) continue
          if (signal.aborted) return
          yield event
          if (signal.aborted || event.type === 'done' || event.type === 'error') return
        }
        if (chunk.done) break
      }
      // The server always ends with done or error; anything else means the stream was cut off.
      yield { type: 'error', message: TRIAGE_STREAM_ERROR_MESSAGE }
    } finally {
      signal.removeEventListener('abort', onAbort)
      void reader.cancel().catch(() => {})
    }
  }
}

/**
 * Triages one typed span with POST /triage/span (a SpanTriager, plus injectable fetch). Rejects with the AbortError
 * when aborted, and otherwise with a PredictError: lib/api's message for a `{detail, code}` reply, the
 * start-uvicorn message for an unreachable backend, and TRIAGE_STREAM_ERROR_MESSAGE for a malformed answer.
 */
export async function triageSpan(
  text: string,
  previousDoctorTurns: string[],
  signal: AbortSignal,
  { fetchImpl = fetch }: JevOptions = {},
): Promise<SpanTriage> {
  let response: Response
  try {
    response = await fetchImpl(`${API_BASE}/triage/span`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, previous_doctor_turns: previousDoctorTurns }),
      signal,
    })
  } catch (error) {
    if (isAbort(error)) throw error
    throw new PredictError('network', messageFor('network'))
  }
  if (!response.ok) throw await errorFromResponse(response)
  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    if (isAbort(error)) throw error
    payload = null
  }
  if (!isObject(payload) || !BASE_KINDS.includes(payload.kind as TriageBaseKind) || !isProbability(payload.p_claim))
    throw new PredictError('unknown', TRIAGE_STREAM_ERROR_MESSAGE)
  return { kind: payload.kind as TriageBaseKind, p_claim: payload.p_claim }
}

/** triageSpan with the real fetch, as a SpanTriager. */
export const defaultSpanTriager: SpanTriager = (text, previous, signal) => triageSpan(text, previous, signal)
