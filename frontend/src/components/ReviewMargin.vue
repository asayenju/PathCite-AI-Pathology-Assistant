<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEventListener, useMediaQuery } from '@vueuse/core'
import CommentCard from './CommentCard.vue'
import type { ReviewThread } from '@/composables/useTranscriptReview'
import { layoutCards } from '@/lib/layoutCards'

/**
 * The comment margin beside the transcript. At lg and up, cards are absolutely positioned next to their highlights
 * (found in `container` via `[data-anchor-for="<id>"]`) and laid out by layoutCards; below lg they are a plain list.
 * Either way orphans (no highlight) come first, then the rest in `order`. Accurate comments never get a card, and
 * resolved/disagreed ones only show when `showResolved` is on.
 */
const props = withDefaults(
  defineProps<{
    /** Every comment, accurate included (they're filtered out here). */
    comments: ReviewThread[]
    /** Non-accurate comment ids in transcript order (useTranscriptReview().issueOrder). */
    order: string[]
    activeCommentId: string | null
    showResolved: boolean
    /**
     * The element that holds the transcript, inside the shared scroll container (TranscriptReviewLayout's slot
     * `container`). It must not contain this margin: its height is a floor for the margin's min-height.
     */
    container: HTMLElement | null
    /** 'auto' follows the lg breakpoint; 'margin' / 'list' force a mode (tests, or a host that knows better). */
    mode?: 'auto' | 'margin' | 'list'
    /** Space between stacked cards, in px. */
    gap?: number
  }>(),
  { mode: 'auto', gap: 8 },
)

const emit = defineEmits<{ activate: [commentId: string] }>()

defineSlots<{
  actions?: (props: { comment: ReviewThread }) => unknown
  thread?: (props: { comment: ReviewThread }) => unknown
}>()

const isLg = useMediaQuery('(min-width: 1024px)')
const positioned = computed(() => (props.mode === 'auto' ? isLg.value : props.mode === 'margin'))

const isOrphan = (c: ReviewThread) => c.start === null

/** Visible cards: orphans first, then transcript order; comments missing from `order` go last in arrival order. */
const visible = computed<ReviewThread[]>(() => {
  const rank = new Map(props.order.map((id, i) => [id, i]))
  return props.comments
    .map((comment, arrival) => ({ comment, arrival }))
    .filter(({ comment }) => comment.verdict !== 'accurate' && (props.showResolved || comment.status === 'open'))
    .sort(
      (a, b) =>
        Number(isOrphan(b.comment)) - Number(isOrphan(a.comment)) ||
        (rank.get(a.comment.id) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.comment.id) ?? Number.MAX_SAFE_INTEGER) ||
        a.arrival - b.arrival,
    )
    .map(({ comment }) => comment)
})

const root = ref<HTMLElement | null>(null)
const cardEls = new Map<string, HTMLElement>()
const tops = ref<Record<string, number>>({})
const minHeight = ref(0)

function setCardEl(id: string, el: unknown) {
  if (el instanceof HTMLElement) cardEls.set(id, el)
  else cardEls.delete(id)
}

function anchorSelector(id: string) {
  return `[data-anchor-for="${id.replace(/["\\]/g, '\\$&')}"]`
}

/**
 * Measures anchors relative to this margin's own top (it shares the scroll content with the transcript, so the
 * same offset works for both) and card heights, then lays out. Orphans and anchors not in the DOM get top 0.
 */
function measure() {
  if (!positioned.value || !root.value) return
  const originTop = root.value.getBoundingClientRect().top
  const items = visible.value.map((comment) => {
    const anchor = isOrphan(comment) ? null : props.container?.querySelector(anchorSelector(comment.id))
    const anchorTop = anchor ? Math.max(0, anchor.getBoundingClientRect().top - originTop) : 0
    return { id: comment.id, anchorTop, height: cardEls.get(comment.id)?.offsetHeight ?? 0 }
  })
  const next = layoutCards(items, props.activeCommentId, props.gap)
  tops.value = next
  const bottom = items.reduce((max, item) => Math.max(max, next[item.id] + item.height), 0)
  minHeight.value = Math.max(bottom, props.container?.offsetHeight ?? 0)
}

let observer: ResizeObserver | null = null

/** Watches the container and every card, so expanding a card (e.g. a reply) or reflowing text re-lays out. */
function observe() {
  observer?.disconnect()
  if (!positioned.value || typeof ResizeObserver === 'undefined') return
  observer ??= new ResizeObserver(() => measure())
  if (props.container) observer.observe(props.container)
  for (const el of cardEls.values()) observer.observe(el)
}

async function relayout() {
  await nextTick()
  measure()
  observe()
}

watch(
  () => [
    visible.value.map((c) => `${c.id}:${c.status}:${c.thread.length}`).join('|'),
    props.activeCommentId,
    props.container,
    positioned.value,
    props.gap,
  ],
  relayout,
)
onMounted(relayout)
useEventListener(window, 'resize', measure)
onBeforeUnmount(() => observer?.disconnect())

defineExpose({ measure })
</script>

<template>
  <section aria-label="Review comments" data-testid="review-margin">
    <div
      v-if="positioned"
      ref="root"
      class="relative"
      :style="{ minHeight: `${minHeight}px` }"
      data-testid="review-margin-positioned"
    >
      <div
        v-for="comment in visible"
        :key="comment.id"
        :ref="(el) => setCardEl(comment.id, el)"
        class="absolute inset-x-0 top-0 motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out"
        :style="{ transform: `translateY(${tops[comment.id] ?? 0}px)` }"
        :data-card-for="comment.id"
      >
        <CommentCard :comment="comment" :active="comment.id === activeCommentId" @activate="emit('activate', $event)">
          <template #actions><slot name="actions" :comment="comment" /></template>
          <template #thread><slot name="thread" :comment="comment" /></template>
        </CommentCard>
      </div>
    </div>

    <ol v-else class="space-y-3" data-testid="review-margin-list">
      <li v-for="comment in visible" :key="comment.id" :data-card-for="comment.id">
        <CommentCard :comment="comment" :active="comment.id === activeCommentId" @activate="emit('activate', $event)">
          <template #actions><slot name="actions" :comment="comment" /></template>
          <template #thread><slot name="thread" :comment="comment" /></template>
        </CommentCard>
      </li>
    </ol>

    <p v-if="!visible.length" class="text-sm text-quiet" data-testid="review-margin-empty">No open comments.</p>
  </section>
</template>
