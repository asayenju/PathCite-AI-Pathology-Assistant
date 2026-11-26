<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEventListener, useMediaQuery } from '@vueuse/core'
import CommentCard from './CommentCard.vue'
import type { ReviewThread } from '@/composables/useTranscriptReview'
import { layoutCards } from '@/lib/layoutCards'
import { reviewMotionStyle } from '@/lib/reviewMotion'

/**
 * The comment margin beside the transcript. At lg and up, cards are absolutely positioned next to their highlights
 * (found in `container` via `[data-anchor-for="<id>"]`) and laid out by layoutCards; below lg they are a plain list.
 * Either way orphans (no highlight) come first, then the rest in `order`. Accurate comments never get a card, and
 * resolved/disagreed ones only show when `showResolved` is on, except a Jev transcription card the doctor just
 * answered: while it stays the active card it shows collapsed ("Not a claim"), so the answer is visible and focus
 * has somewhere to stay.
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
  answer?: (props: { comment: ReviewThread }) => unknown
}>()

const isLg = useMediaQuery('(min-width: 1024px)')
const positioned = computed(() => (props.mode === 'auto' ? isLg.value : props.mode === 'margin'))

const isOrphan = (c: ReviewThread) => c.start === null

/** Shown: open comments, closed ones with Show resolved, and the active answered transcription card. */
const isShown = (c: ReviewThread) =>
  props.showResolved ||
  c.status === 'open' ||
  (c.id === props.activeCommentId && c.verdict === 'transcription' && c.confirmed !== null)

/** Visible cards: orphans first, then transcript order; comments missing from `order` go last in arrival order. */
const visible = computed<ReviewThread[]>(() => {
  const rank = new Map(props.order.map((id, i) => [id, i]))
  return props.comments
    .map((comment, arrival) => ({ comment, arrival }))
    .filter(({ comment }) => comment.verdict !== 'accurate' && isShown(comment))
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
/**
 * Cards that have been laid out and shown. A new card renders invisible until its first measure puts it at its
 * highlight; only then does it fade in, and only placed cards animate moves. Without this a new card would flash at
 * the top of the margin and slide down.
 */
const placed = ref<Set<string>>(new Set())

function markPlaced(ids: string[]) {
  const fresh = ids.filter((id) => !placed.value.has(id))
  if (!fresh.length) return
  const show = () => (placed.value = new Set([...placed.value, ...fresh]))
  // One frame later, so the invisible card is already at its place when it starts to appear.
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(show)
  else show()
}

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
  markPlaced(items.filter((item) => item.height > 0).map((item) => item.id))
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

// A card that leaves (resolved and hidden) must be placed afresh if it comes back.
watch(
  () => visible.value.map((c) => c.id),
  (ids) => {
    const keep = new Set(ids)
    if ([...placed.value].some((id) => !keep.has(id))) placed.value = new Set([...placed.value].filter((id) => keep.has(id)))
  },
)

watch(
  () => [
    visible.value.map((c) => `${c.id}:${c.status}:${c.verdict}:${c.thread.length}`).join('|'),
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

const isPlaced = (id: string) => placed.value.has(id)

/**
 * Reveal animation (issue #39): each card slides in once, delayed by its arrival order among the cards of its turn
 * (`--review-stagger`, times CARD_STAGGER_MS in CSS). The animation is on the card's inner element, so the
 * absolutely positioned wrapper, its measured height and the ResizeObserver are untouched. None with reduced motion.
 */
const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
const motionStyle = reviewMotionStyle()
const staggerIndex = computed(() => {
  const perTurn = new Map<string, number>()
  const index = new Map<string, number>()
  for (const c of props.comments) {
    if (c.verdict === 'accurate') continue
    const n = perTurn.get(c.turn_id) ?? 0
    index.set(c.id, n)
    perTurn.set(c.turn_id, n + 1)
  }
  return index
})
const staggerStyle = (id: string) => (reduceMotion.value ? undefined : { '--review-stagger': staggerIndex.value.get(id) ?? 0 })
const revealClass = (id: string) => (isPlaced(id) && !reduceMotion.value ? 'review-card-in' : undefined)

defineExpose({ measure })
</script>

<template>
  <section aria-label="Review comments" :style="motionStyle" data-testid="review-margin">
    <div
      v-if="positioned"
      ref="root"
      class="relative"
      :style="{ minHeight: `${minHeight}px` }"
      data-testid="review-margin-positioned"
    >
      <TransitionGroup leave-active-class="review-card-leave" leave-to-class="opacity-0">
        <div
          v-for="comment in visible"
          :key="comment.id"
          :ref="(el) => setCardEl(comment.id, el)"
          class="absolute inset-x-0 top-0"
          :class="
            isPlaced(comment.id)
              ? 'motion-safe:transition-transform motion-safe:duration-300 motion-safe:ease-[cubic-bezier(0.2,0.7,0.2,1)]'
              : 'invisible'
          "
          :style="{ transform: `translateY(${tops[comment.id] ?? 0}px)` }"
          :data-card-for="comment.id"
          :data-top="tops[comment.id] ?? 0"
          :data-placed="isPlaced(comment.id) ? '' : undefined"
        >
          <div :class="revealClass(comment.id)" :style="staggerStyle(comment.id)" data-testid="card-reveal">
            <CommentCard :comment="comment" :active="comment.id === activeCommentId" @activate="emit('activate', $event)">
              <template #actions><slot name="actions" :comment="comment" /></template>
              <template #thread><slot name="thread" :comment="comment" /></template>
              <template #answer><slot name="answer" :comment="comment" /></template>
            </CommentCard>
          </div>
        </div>
      </TransitionGroup>
    </div>

    <ol v-else class="space-y-3" data-testid="review-margin-list">
      <TransitionGroup
        :enter-active-class="reduceMotion ? '' : 'review-card-in'"
        leave-active-class="review-card-leave"
        leave-to-class="opacity-0"
      >
        <li v-for="comment in visible" :key="comment.id" :data-card-for="comment.id" :style="staggerStyle(comment.id)">
          <CommentCard :comment="comment" :active="comment.id === activeCommentId" @activate="emit('activate', $event)">
            <template #actions><slot name="actions" :comment="comment" /></template>
            <template #thread><slot name="thread" :comment="comment" /></template>
            <template #answer><slot name="answer" :comment="comment" /></template>
          </CommentCard>
        </li>
      </TransitionGroup>
    </ol>

    <p v-if="!visible.length" class="text-sm text-quiet" data-testid="review-margin-empty">No open comments.</p>
  </section>
</template>
