import type { CheckResult, ClaimChecker, ClaimToCheck } from '@/types'

/** How long the stub "checks", so the UI's Checking… state is visible. */
export const STUB_CHECK_DELAY_MS = 600

const abortError = () => new DOMException('The check was aborted.', 'AbortError')

/**
 * The placeholder ClaimChecker (docs/PRD-jev-triage.md): after delayMs it answers `not_checked` for every claim and
 * sends nothing anywhere. Rejects with an AbortError as soon as the signal aborts, leaving no timer behind.
 * The RAG fact-checker PRD replaces it.
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

// Keeps the stub assignable to the contract.
export const defaultClaimChecker: ClaimChecker = (claim, signal) => stubClaimChecker(claim, signal)
