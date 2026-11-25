<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'

/**
 * The shared scroll container for a review: transcript on the left, comment margin on the right (lg+), stacked
 * below lg. Both columns live in one scrolling element, so margin cards scroll with the text they point at.
 * Presentational: the host passes TranscriptView (with `:scroll="false"`) and ReviewMargin into the slots; both
 * slots get `container`, the transcript column element, which ReviewMargin measures highlights against.
 *
 * When `activeCommentId` changes, the active highlight (or, for an orphan, its card) is scrolled into view if it
 * isn't already visible. Focus is never moved.
 */
const props = defineProps<{ activeCommentId: string | null; label?: string }>()

defineSlots<{
  transcript?: (props: { container: HTMLElement | null }) => unknown
  margin?: (props: { container: HTMLElement | null }) => unknown
}>()

const scroller = ref<HTMLElement | null>(null)
const container = ref<HTMLElement | null>(null)
const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')

function escapeAttr(value: string) {
  return value.replace(/["\\]/g, '\\$&')
}

function fullyVisible(el: Element, viewport: Element) {
  const r = el.getBoundingClientRect()
  const v = viewport.getBoundingClientRect()
  return r.top >= v.top && r.bottom <= v.bottom
}

async function reveal(id: string | null) {
  if (!id) return
  await nextTick()
  const root = scroller.value
  if (!root) return
  const anchor = root.querySelector(`[data-anchor-for="${escapeAttr(id)}"]`)
  const target = anchor ?? root.querySelector(`[id="comment-${escapeAttr(id)}"]`)
  if (!target || fullyVisible(target, root)) return
  target.scrollIntoView({ block: anchor ? 'center' : 'nearest', behavior: reducedMotion.value ? 'auto' : 'smooth' })
}

watch(() => props.activeCommentId, reveal)
</script>

<template>
  <div
    ref="scroller"
    tabindex="0"
    :aria-label="label ?? 'Transcript and review comments'"
    class="max-h-[70vh] overflow-y-auto rounded-glass border border-frost bg-glass focus-visible:outline-2 focus-visible:outline-offset-2"
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
