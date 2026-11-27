import type { CheckVerdict, ReviewReason, SourceStance, SourceTrust } from '@/types'

/*
 * Words for the RAG checker's answer on a claim card (docs/PRD-rag-checker.md). Kept out of the components so tests
 * and docs can share them.
 */

/** What a claim card says when its check was stopped (or the test stub answered). */
export const NOT_CHECKED_TEXT = 'Not fact-checked (the check was stopped)'

/** The card's polite status line once a claim is checked. */
export const CHECK_VERDICT_TEXT: Record<CheckVerdict, string> = {
  accurate: 'Accurate: the sources support this.',
  inaccurate: 'Inaccurate: the sources contradict this.',
  incomplete: 'Incomplete: the sources support this, but it leaves out a qualifier.',
  unverified: "Not verified — the corpus doesn't cover this",
  disputed: 'Sources disagree',
}

const REVIEW_REASON_TEXT: Record<ReviewReason, string> = {
  sources_disagree: 'The sources disagree with each other.',
  models_disagree: 'OpenAI (Luna) and Jev read at least one source differently.',
  jev_unavailable: "Jev didn't answer, so only OpenAI (Luna) judged the sources.",
}

/** A review reason in plain words; one this frontend doesn't know is shown as "Other reason: …". */
export function reviewReasonText(reason: string): string {
  return (REVIEW_REASON_TEXT as Record<string, string>)[reason] ?? `Other reason: ${reason.replace(/_/g, ' ')}`
}

export const TRUST_LABELS: Record<SourceTrust, string> = {
  high: 'High trust',
  medium: 'Medium trust',
  low: 'Low trust',
}

/** The stances shown as groups, in order. Irrelevant sources are collapsed separately. */
export const STANCE_GROUPS: { stance: Exclude<SourceStance, 'irrelevant'>; label: string }[] = [
  { stance: 'supports', label: 'Supports' },
  { stance: 'contradicts', label: 'Contradicts' },
  { stance: 'unclear', label: 'Unclear' },
]

/** The url if it is http(s), else null: a source link must never run script. */
export function safeUrl(url: string): string | null {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null
  } catch {
    return null
  }
}
