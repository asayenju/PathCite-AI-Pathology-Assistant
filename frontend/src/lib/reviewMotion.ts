/**
 * Review reveal timings (issue #39), shared by the pacing (pacedReviewSource) and the CSS (as custom properties via
 * reviewMotionStyle), so a turn is never handed over before its own animations have played.
 */

/** A new highlight's tint fades in. */
export const HIGHLIGHT_FADE_MS = 300
/** A new card slides up and fades in. */
export const CARD_SLIDE_MS = 200
/** Delay between cards of the same turn. */
export const CARD_STAGGER_MS = 120
/** Time to read a turn once its animations are done; the only part the cap may shorten. */
export const READING_PAUSE_MS = 400
/** The "Reviewing…" marker and the turn's dimming move between turns. */
export const MARKER_TRANSITION_MS = 180

/** How long a turn's animations take: highlight fade, then every card slid in with its stagger. 0 without cards. */
export function animationMinimumMs(cards: number): number {
  if (cards <= 0) return 0
  return HIGHLIGHT_FADE_MS + CARD_STAGGER_MS * (cards - 1) + CARD_SLIDE_MS
}

/** The person asked for reduced motion: no pacing and no animation. False where matchMedia is missing (jsdom). */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false
}

/** The timings as CSS custom properties, for the element that holds the highlights and cards. */
export function reviewMotionStyle(): Record<string, string> {
  return {
    '--review-mark-ms': `${HIGHLIGHT_FADE_MS}ms`,
    '--review-card-ms': `${CARD_SLIDE_MS}ms`,
    '--review-stagger-ms': `${CARD_STAGGER_MS}ms`,
    '--review-marker-ms': `${MARKER_TRANSITION_MS}ms`,
  }
}
