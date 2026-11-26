<script setup lang="ts">
import { computed } from 'vue'
import { Check, CircleHelp, Plus, Quote, X } from 'lucide-vue-next'
import type { ReviewThread } from '@/composables/useTranscriptReview'
import type { ReviewVerdict } from '@/types'

/**
 * One agent comment in the review margin. Display only: it emits `activate` on click or Enter and leaves the
 * Resolve / Disagree / Reply controls (#30) to the `actions` and `thread` slots.
 * The root id `comment-<id>` is the target of the highlight's aria-describedby.
 */
const props = defineProps<{ comment: ReviewThread; active: boolean }>()

const emit = defineEmits<{ activate: [commentId: string] }>()

defineSlots<{
  /** Resolve / Reopen / Disagree controls (#30). */
  actions?: () => unknown
  /** Reply thread and reply box (#30). */
  thread?: () => unknown
}>()

/** Full class strings so Tailwind sees them. Accurate never gets a card, but the map stays total. */
const VERDICTS: Record<ReviewVerdict, { label: string; icon: typeof X; text: string; border: string }> = {
  inaccurate: { label: 'Inaccurate', icon: X, text: 'text-eosin', border: 'border-l-eosin' },
  incomplete: { label: 'Incomplete', icon: Plus, text: 'text-amber', border: 'border-l-amber' },
  accurate: { label: 'Accurate', icon: Check, text: 'text-moss', border: 'border-l-moss' },
  // Jev triage (#36): neutral placeholders until the triage cards are styled (#37).
  claim: { label: 'Claim', icon: Quote, text: 'text-ink', border: 'border-l-ink' },
  transcription: { label: 'Possible transcription error', icon: CircleHelp, text: 'text-quiet', border: 'border-l-quiet' },
}

const verdict = computed(() => VERDICTS[props.comment.verdict])
const collapsed = computed(() => props.comment.status !== 'open')
const orphan = computed(() => props.comment.start === null)
const statusLabel = computed(() => (props.comment.status === 'disagreed' ? 'Disagreed' : 'Resolved'))

function activate() {
  emit('activate', props.comment.id)
}
</script>

<template>
  <article
    :id="`comment-${comment.id}`"
    tabindex="0"
    :aria-current="active ? 'true' : undefined"
    :data-status="comment.status"
    :data-verdict="comment.verdict"
    data-testid="comment-card"
    class="cursor-pointer rounded-glass border border-frost bg-glass text-ink transition-[box-shadow,border-color] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2"
    :class="[
      active ? ['border-l-4 shadow-lg', verdict.border] : 'shadow-sm hover:shadow-md',
      collapsed ? 'px-3 py-2' : 'space-y-2 p-3',
    ]"
    @click="activate"
    @keydown.enter.self.prevent="activate"
  >
    <template v-if="collapsed">
      <p class="flex min-w-0 items-center gap-2 text-sm" data-testid="comment-collapsed">
        <component :is="verdict.icon" class="size-4 shrink-0" :class="verdict.text" aria-hidden="true" />
        <span class="sr-only">{{ verdict.label }}:</span>
        <span class="min-w-0 flex-1 truncate">“{{ comment.quote }}”</span>
        <span
          class="shrink-0 rounded-full border border-frost px-2 py-0.5 text-xs text-quiet"
          data-testid="comment-status"
        >
          {{ statusLabel }}
        </span>
      </p>
      <p v-if="comment.status === 'disagreed' && comment.disagreeReason" class="mt-1 text-sm text-quiet">
        {{ comment.disagreeReason }}
      </p>
    </template>

    <template v-else>
      <p class="flex items-center gap-1.5 text-sm font-semibold" :class="verdict.text" data-testid="comment-verdict">
        <component :is="verdict.icon" class="size-4" aria-hidden="true" />
        {{ verdict.label }}
      </p>

      <p v-if="orphan" class="text-sm text-quiet italic" data-testid="comment-orphan">
        Couldn't find this text in the turn.
      </p>

      <blockquote
        class="border-l-2 border-frost pl-2 text-sm text-quiet"
        :class="active ? 'line-clamp-2' : 'line-clamp-1'"
        :title="comment.quote"
      >
        “{{ comment.quote }}”
      </blockquote>

      <p v-if="comment.issue" class="text-sm" :class="!active && 'line-clamp-2'" data-testid="comment-issue">
        {{ comment.issue }}
      </p>

      <!--
        Like Google Docs, only the selected comment shows its detail and controls; the others stay compact so they sit
        next to their highlights. The detail opens smoothly (grid rows 0fr → 1fr) and is inert while closed.
      -->
      <div
        class="grid motion-safe:transition-[grid-template-rows] motion-safe:duration-300 motion-safe:ease-out"
        :class="active ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'"
        :inert="!active"
        data-testid="comment-details"
        :data-open="active ? '' : undefined"
      >
        <!-- relative: clips absolutely positioned sr-only labels too, or they stretch the scroll area. -->
        <div class="relative min-h-0 space-y-2 overflow-hidden">
          <div v-if="comment.suggestion" class="rounded-glass bg-bench px-2 py-1.5" data-testid="comment-suggestion">
            <p class="text-xs font-semibold text-quiet">Suggested wording</p>
            <p class="text-sm">{{ comment.suggestion }}</p>
          </div>

          <p v-if="comment.source" class="text-xs text-quiet" data-testid="comment-source">
            Source: {{ comment.source }}
          </p>

          <slot name="actions" />
          <slot name="thread" />
        </div>
      </div>
      <p v-if="!active" class="text-xs text-quiet" aria-hidden="true">Select to see the suggestion and reply</p>
    </template>

    <!-- Resolved / disagreed cards keep their controls (Reopen, reply count) on the single line's card. -->
    <template v-if="collapsed">
      <slot name="actions" />
      <slot name="thread" />
    </template>
  </article>
</template>
