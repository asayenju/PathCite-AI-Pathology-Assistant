import { API_BASE, PredictError, errorFromResponse, messageFor } from '@/lib/api'
import type {
  CheckResult,
  CheckSource,
  CheckConfidence,
  CheckVerdict,
  CheckedResult,
  ConfidenceLabel,
  ClaimChecker,
  ClaimToCheck,
  SourceStance,
  SourceTrust,
} from '@/types'

/** How long the stub "checks", so the UI's Checking… state is visible. */
export const STUB_CHECK_DELAY_MS = 600
/** How many POST /check requests run at once; the rest wait in a queue. */
export const MAX_CONCURRENT_CHECKS = 4
/** A 200 from /check that isn't a CheckedResult. */
export const CHECK_RESPONSE_ERROR_MESSAGE = "The checker's answer couldn't be read. Try again."

const abortError = () => new DOMException('The check was aborted.', 'AbortError')
const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/**
 * The placeholder ClaimChecker (docs/PRD-jev-triage.md), kept for tests: after delayMs it answers `not_checked` for
 * every claim and sends nothing anywhere. Rejects with an AbortError as soon as the signal aborts, leaving no timer
 * behind.
 */
export function stubClaimChecker(
  _claim: ClaimToCheck,
  signal: AbortSignal,
  { delayMs = STUB_CHECK_DELAY_MS }: { delayMs?: number } = {},
): Promise<CheckResult> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(abortError())
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError())
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve({ status: 'not_checked' })
    }, delayMs)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

/* ------------------------------------------------------------------------------------------------ parsing */

const VERDICTS: readonly CheckVerdict[] = ['accurate', 'inaccurate', 'incomplete', 'unverified', 'disputed']
const STANCES: readonly SourceStance[] = ['supports', 'contradicts', 'irrelevant', 'unclear']
const TRUSTS: readonly SourceTrust[] = ['high', 'medium', 'low']
const CONFIDENCE_LABELS: readonly ConfidenceLabel[] = ['supported', 'contradicted', 'insufficient']

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const optString = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const optNumber = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function parseSource(v: unknown): CheckSource | null {
  if (!isObject(v)) return null
  if (typeof v.chunk_id !== 'number' || typeof v.url !== 'string' || typeof v.quote !== 'string') return null
  if (!STANCES.includes(v.stance as SourceStance) || !TRUSTS.includes(v.trust as SourceTrust)) return null
  return {
    chunk_id: v.chunk_id,
    title: optString(v.title),
    section: typeof v.section === 'string' ? v.section : '',
    url: v.url,
    trust: v.trust as SourceTrust,
    year: optNumber(v.year),
    quote: v.quote,
    stance: v.stance as SourceStance,
    luna_stance: optString(v.luna_stance),
  }
}

const isProbability = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1

/** Jev's confidence: null stays null (Jev unavailable); undefined means malformed or missing. */
function parseConfidence(v: unknown): CheckConfidence | null | undefined {
  if (v === null) return null
  if (!isObject(v) || !CONFIDENCE_LABELS.includes(v.label as ConfidenceLabel) || !isProbability(v.p)) return undefined
  if (v.p_verdict !== null && !isProbability(v.p_verdict)) return undefined
  if (v.agrees !== null && typeof v.agrees !== 'boolean') return undefined
  return { label: v.label as ConfidenceLabel, p: v.p, p_verdict: v.p_verdict, agrees: v.agrees }
}

/** The CheckedResult in a /check body, or null if it doesn't have the contract's shape. */
export function parseCheckedResult(v: unknown): CheckedResult | null {
  if (!isObject(v) || v.status !== 'checked' || !VERDICTS.includes(v.verdict as CheckVerdict)) return null
  if (typeof v.needs_review !== 'boolean') return null
  if (!Array.isArray(v.review_reasons) || !v.review_reasons.every((r) => typeof r === 'string')) return null
  if (!Array.isArray(v.sources)) return null
  const sources = v.sources.map(parseSource)
  if (sources.some((s) => s === null)) return null
  if (!Array.isArray(v.search_queries) || !v.search_queries.every((q) => typeof q === 'string')) return null
  const confidence = parseConfidence(v.confidence)
  if (confidence === undefined) return null
  return {
    status: 'checked',
    verdict: v.verdict as CheckVerdict,
    issue: optString(v.issue),
    suggestion: optString(v.suggestion),
    needs_review: v.needs_review,
    review_reasons: v.review_reasons as string[],
    sources: sources as CheckSource[],
    model: typeof v.model === 'string' ? v.model : '',
    jev_model: optString(v.jev_model),
    search_queries: v.search_queries as string[],
    confidence,
  }
}

/* ------------------------------------------------------------------------------------------------ limiter */

/**
 * Runs at most `max` tasks at once, first come first served. A queued task whose signal aborts is dropped from the
 * queue and rejects at once with an AbortError, without ever starting; a running task frees its slot when it
 * settles, however it settles.
 */
function createLimiter(max: number) {
  let active = 0
  const queue: (() => void)[] = []

  const pump = () => {
    while (active < max && queue.length) queue.shift()!()
  }

  return function run<T>(task: () => Promise<T>, signal: AbortSignal): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (signal.aborted) return reject(abortError())
      const onAbort = () => {
        const at = queue.indexOf(begin)
        if (at >= 0) queue.splice(at, 1)
        reject(abortError())
      }
      function begin() {
        signal.removeEventListener('abort', onAbort)
        active++
        let pending: Promise<T>
        try {
          pending = task()
        } catch (error) {
          pending = Promise.reject(error)
        }
        pending.then(resolve, reject).finally(() => {
          active--
          pump()
        })
      }
      signal.addEventListener('abort', onAbort, { once: true })
      queue.push(begin)
      pump()
    })
  }
}

/* ------------------------------------------------------------------------------------------------ HTTP */

export interface HttpCheckerOptions {
  /** Injectable for tests. Defaults to the global fetch, looked up at call time. */
  fetchImpl?: typeof fetch
  maxConcurrent?: number
}

async function postCheck(claim: ClaimToCheck, signal: AbortSignal, fetchImpl: typeof fetch): Promise<CheckResult> {
  let response: Response
  try {
    response = await fetchImpl(`${API_BASE}/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ turn_id: claim.turn_id, quote: claim.quote, context: claim.context }),
      signal,
    })
  } catch (error) {
    if (isAbort(error) || signal.aborted) throw isAbort(error) ? error : abortError()
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
  if (signal.aborted) throw abortError()
  const result = parseCheckedResult(payload)
  if (!result) throw new PredictError('unknown', CHECK_RESPONSE_ERROR_MESSAGE)
  return result
}

/**
 * The RAG ClaimChecker (docs/PRD-rag-checker.md): POSTs `{turn_id, quote, context}` to /api/check, at most
 * MAX_CONCURRENT_CHECKS at once (each checker made here has its own queue). Rejects with an AbortError when the
 * signal aborts (a queued check never starts), otherwise with a PredictError whose message is readable: lib/api's
 * message for a `{detail, code}` reply, the start-uvicorn message for an unreachable backend, and
 * CHECK_RESPONSE_ERROR_MESSAGE for a malformed answer.
 */
export function createHttpClaimChecker({
  fetchImpl,
  maxConcurrent = MAX_CONCURRENT_CHECKS,
}: HttpCheckerOptions = {}): ClaimChecker {
  const limit = createLimiter(maxConcurrent)
  const doFetch: typeof fetch = fetchImpl ?? ((input, init) => fetch(input, init))
  return (claim, signal) => limit(() => postCheck(claim, signal, doFetch), signal)
}

/** The app's checker: one shared queue, so a review never has more than 4 checks in flight. */
export const httpClaimChecker: ClaimChecker = createHttpClaimChecker()

export const defaultClaimChecker: ClaimChecker = httpClaimChecker
