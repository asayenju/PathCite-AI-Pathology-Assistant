<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useEventListener, useMediaQuery } from '@vueuse/core'

/**
 * The shared scroll container for a review: transcript on the left, comment margin on the right (lg+), stacked
 * below lg. Both columns live in one scrolling element, so margin cards scroll with the text they point at.
 * Presentational: the host passes TranscriptView (with `:scroll="false"`) and ReviewMargin into the slots; both
 * slots get `container`, the transcript column element, which ReviewMargin measures highlights against.
 *
 * Scrolling (smooth unless the user prefers reduced motion; focus is never moved):
 * - `focusRequest` brings one end of a comment into view: `card` after a highlight is clicked, `highlight` after a
 *   card is clicked (or Previous/Next). A new `n` repeats the scroll for the same id.
 * - `followTurnId` keeps the turn the agent is reviewing in view, unless the user scrolled in the last few seconds.
 */
export interface FocusRequest {
  id: string
  target: 'card' | 'highlight'
  n: number
}

const props = defineProps<{ focusRequest?: FocusRequest | null; followTurnId?: string | null; label?: string }>()

defineSlots<{
  transcript?: (props: { container: HTMLElement | null }) => unknown
  margin?: (props: { container: HTMLElement | null }) => unknown
}>()

/** How long a manual scroll (or a jump to a comment) pauses following the agent. */
const FOLLOW_PAUSE_MS = 4000

const scroller = ref<HTMLElement | null>(null)
const container = ref<HTMLElement | null>(null)
const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
let pausedUntil = 0

const pause = () => (pausedUntil = Date.now() + FOLLOW_PAUSE_MS)
for (const event of ['wheel', 'touchmove', 'keydown'] as const) useEventListener(scroller, event, pause, { passive: true })

const escapeAttr = (value: string) => value.replace(/["\\]/g, '\\$&')
const frame = () =>
  new Promise<void>((resolve) =>
    typeof requestAnimationFrame === 'function' ? requestAnimationFrame(() => resolve()) : setTimeout(resolve, 16),
  )

/**
 * Where an element will rest, in viewport px. Positioned margin cards slide with a transform, so mid-transition
 * their box is not where they end up: they carry their laid-out top in `data-top`, relative to their parent.
 */
function restingBox(el: HTMLElement): { top: number; height: number } {
  const top = el.dataset.top
  if (top !== undefined && el.parentElement) {
    return { top: el.parentElement.getBoundingClientRect().top + Number(top), height: el.offsetHeight }
  }
  const r = el.getBoundingClientRect()
  return { top: r.top, height: r.height }
}

/** Scrolls the container so the box sits at `place` of its height (0.5 = centred), if it isn't fully visible. */
function bringIntoView(el: HTMLElement, place = 0.5) {
  const root = scroller.value
  if (!root) return
  const { top, height } = restingBox(el)
  const view = root.getBoundingClientRect()
  if (top >= view.top && top + height <= view.bottom) return
  const offset = Math.max(0, root.clientHeight - height) * place
  root.scrollTo({ top: root.scrollTop + (top - view.top) - offset, behavior: reducedMotion.value ? 'auto' : 'smooth' })
}

function find(selector: string) {
  return scroller.value?.querySelector<HTMLElement>(selector) ?? null
}

watch(
  () => props.focusRequest,
  async (request) => {
    if (!request) return
    pause()
    // Let the margin re-lay out around the newly active card before measuring where things will rest.
    await nextTick()
    await frame()
    await frame()
    if (props.focusRequest !== request) return
    const id = escapeAttr(request.id)
    const card = find(`[data-card-for="${id}"]`)
    const highlight = find(`[data-anchor-for="${id}"]`)
    const target = request.target === 'card' ? (card ?? highlight) : (highlight ?? card)
    if (target) bringIntoView(target)
  },
)

watch(
  () => props.followTurnId,
  async (turnId) => {
    if (!turnId || Date.now() < pausedUntil) return
    await nextTick()
    const turn = find(`[data-turn-id="${escapeAttr(turnId)}"]`)
    if (turn) bringIntoView(turn, 0.35)
  },
)
</script>

<template>
  <div
    ref="scroller"
    tabindex="0"
    :aria-label="label ?? 'Transcript and review comments'"
    class="max-h-[85vh] min-h-[32rem] overflow-y-auto overscroll-contain rounded-glass border border-frost bg-glass focus-visible:outline-2 focus-visible:outline-offset-2"
    data-testid="review-scroll"
  >
    <div class="grid gap-6 p-4 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
      <div ref="container" class="min-w-0" data-testid="review-transcript-column">
        <slot name="transcript" :container="container" />
      </div>
      <div class="min-w-0" data-testid="review-margin-column">
        <slot name="margin" :container="container" />
      </div>
    </div>
  </div>
</template>
