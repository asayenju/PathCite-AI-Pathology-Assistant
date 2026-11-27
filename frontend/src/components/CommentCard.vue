<script setup lang="ts">
import { computed } from 'vue'
import { Check, CircleHelp, LoaderCircle, Plus, Quote, Scale, SearchX, TriangleAlert, X } from 'lucide-vue-next'
import CheckSources from './CheckSources.vue'
import type { ReviewThread } from '@/composables/useTranscriptReview'
import { CHECK_VERDICT_TEXT, NOT_CHECKED_TEXT, confidenceText, reviewReasonText } from '@/lib/check'
import type { ReviewVerdict } from '@/types'

/**
 * One review comment in the margin. Display only: it emits `activate` on click or Enter and leaves the controls to
 * slots: `actions` (Resolve / Reopen / Disagree), `thread` (reply thread, fact-check comments only) and `answer`
 * (the Yes / No answer of an unanswered Jev transcription card).
 * The root id `comment-<id>` is the target of the highlight's aria-describedby.
 *
 * Jev triage cards (docs/PRD-jev-triage.md): a claim shows its check state in a polite live region ("Checking…",
 * then the check verdict, "Not fact-checked" if stopped, or the error) and what it was checked as after a
 * correction; a transcription card asks "Is this correct?" and, once answered as small talk, collapses to "Not a
 * claim" with the correction if any.
 *
 * A checked claim (docs/PRD-rag-checker.md) takes the check verdict's label, icon and colour (`data-verdict` stays
 * `claim`; `data-check-verdict` has the check verdict), shows the checker's issue and suggestion, a "Needs your
 * review" banner with the reasons, Jev's confidence next to the verdict and, when selected, its sources grouped by
 * stance and what was searched for.
 */
const props = defineProps<{ comment: ReviewThread; active: boolean }>()

const emit = defineEmits<{ activate: [commentId: string] }>()

defineSlots<{
  /** Resolve / Reopen / Disagree controls. */
  actions?: () => unknown
  /** Reply thread and reply box. */
  thread?: () => unknown
  /** Yes / No answer for an unanswered transcription card. */
  answer?: () => unknown
}>()

/**
 * Full class strings so Tailwind sees them. A fact-check comment with verdict accurate never gets a card, but a
 * checked claim can show any check verdict, so the map stays total.
 */
const VERDICTS: Record<ReviewVerdict, { label: string; icon: typeof X; text: string; border: string }> = {
  inaccurate: { label: 'Inaccurate', icon: X, text: 'text-eosin', border: 'border-l-eosin' },
  incomplete: { label: 'Incomplete', icon: Plus, text: 'text-amber', border: 'border-l-amber' },
  accurate: { label: 'Accurate', icon: Check, text: 'text-moss', border: 'border-l-moss' },
  unverified: { label: 'Not verified', icon: SearchX, text: 'text-quiet', border: 'border-l-quiet' },
  disputed: { label: 'Sources disagree', icon: Scale, text: 'text-ink', border: 'border-l-ink' },
  claim: { label: 'Claim', icon: Quote, text: 'text-hema', border: 'border-l-hema' },
  transcription: { label: 'Possible transcription error', icon: CircleHelp, text: 'text-ink', border: 'border-l-ink' },
}

const isClaim = computed(() => props.comment.verdict === 'claim')
/** The checker's answer on a claim, once checked. */
const check = computed(() => (isClaim.value && props.comment.checkStatus === 'checked' ? props.comment.check : null))
/** A checked claim shows the check verdict; everything else its own verdict. */
const verdict = computed(() => VERDICTS[check.value?.verdict ?? props.comment.verdict])
const issue = computed(() => props.comment.issue ?? check.value?.issue ?? null)
const suggestion = computed(() => props.comment.suggestion ?? check.value?.suggestion ?? null)
const checkModels = computed(() => {
  const c = check.value
  if (!c) return ''
  return c.jev_model ? `Checked with ${c.model} and ${c.jev_model}` : `Checked with ${c.model} (Jev unavailable)`
})
const collapsed = computed(() => props.comment.status !== 'open')
const orphan = computed(() => props.comment.start === null)
const isTranscription = computed(() => props.comment.verdict === 'transcription')
/** A transcription card the doctor has answered (Yes, or a correction that went through). */
const answered = computed(() => isTranscription.value && props.comment.confirmed !== null)
const statusLabel = computed(() => {
  if (props.comment.status === 'disagreed') return 'Disagreed'
  return answered.value ? 'Not a claim' : 'Resolved'
})
const checkText = computed(() => {
  const status = props.comment.checkStatus
  if (status === 'error') return props.comment.checkError ?? "This claim couldn't be checked."
  if (status === 'checking') return 'Checking…'
  if (status === 'not_checked') return NOT_CHECKED_TEXT
  return check.value ? CHECK_VERDICT_TEXT[check.value.verdict] : ''
})
const hint = computed(() => {
  if (isTranscription.value && !answered.value) return 'Select to answer'
  if (props.comment.triage) return 'Select to resolve or disagree'
  return 'Select to see the suggestion and reply'
})

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
    :data-check-verdict="check?.verdict"
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
      <p v-if="isTranscription && comment.correction" class="mt-1 text-sm" data-testid="comment-correction">
        Correction: “{{ comment.correction }}”
      </p>
      <p v-if="comment.status === 'disagreed' && comment.disagreeReason" class="mt-1 text-sm text-quiet">
        {{ comment.disagreeReason }}
      </p>
    </template>

    <template v-else>
      <div class="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
        <p class="flex items-center gap-1.5 text-sm font-semibold" :class="verdict.text" data-testid="comment-verdict">
          <component :is="verdict.icon" class="size-4" aria-hidden="true" />
          {{ verdict.label }}
        </p>
        <span
          v-if="check"
          class="text-xs"
          :class="check.confidence?.agrees === false ? 'text-amber' : 'text-quiet'"
          data-testid="check-confidence"
        >
          {{ confidenceText(check.confidence) }}
        </span>
      </div>

      <p v-if="orphan" class="text-sm text-quiet italic" data-testid="comment-orphan">
        Couldn't find this text in the turn.
      </p>

      <blockquote
        class="border-l-2 border-frost pl-2 text-sm text-quiet"
        :class="[active ? 'line-clamp-2' : 'line-clamp-1', isTranscription && 'border-dashed']"
        :title="comment.quote"
      >
        “{{ comment.quote }}”
      </blockquote>

      <p v-if="issue" class="text-sm" :class="!active && 'line-clamp-2'" data-testid="comment-issue">
        {{ issue }}
      </p>

      <p v-if="isClaim && comment.checkedAs" class="text-sm" data-testid="claim-checked-as">
        Checked as: “{{ comment.checkedAs }}”
      </p>
      <!-- Always rendered for a claim, so the change from Checking… to its result is announced. -->
      <p
        v-if="isClaim"
        role="status"
        aria-live="polite"
        class="flex items-center gap-1.5 text-sm"
        :class="comment.checkStatus === 'error' ? 'text-eosin' : 'text-quiet'"
        data-testid="claim-check"
      >
        <LoaderCircle
          v-if="comment.checkStatus === 'checking'"
          class="size-3.5 shrink-0 motion-safe:animate-spin"
          aria-hidden="true"
        />
        {{ checkText }}
      </p>

      <div
        v-if="check?.needs_review"
        class="rounded-glass border border-amber bg-amber/10 px-2 py-1.5 text-sm"
        data-testid="check-review-banner"
      >
        <p class="flex items-center gap-1.5 font-semibold" data-testid="check-review-title">
          <TriangleAlert class="size-4 shrink-0 text-amber" aria-hidden="true" />
          Needs your review
        </p>
        <ul v-if="check.review_reasons.length" class="mt-1 list-disc space-y-0.5 pl-5">
          <li v-for="reason in check.review_reasons" :key="reason">{{ reviewReasonText(reason) }}</li>
        </ul>
      </div>

      <p v-if="isTranscription && !answered" class="text-sm font-medium" data-testid="transcription-question">
        Is this correct?
      </p>
      <p v-else-if="answered" class="text-sm text-quiet" data-testid="transcription-answered">
        Not a claim<template v-if="comment.correction">. Correction: “{{ comment.correction }}”</template>
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
          <div v-if="suggestion" class="rounded-glass bg-bench px-2 py-1.5" data-testid="comment-suggestion">
            <p class="text-xs font-semibold text-quiet">Suggested wording</p>
            <p class="text-sm">{{ suggestion }}</p>
          </div>

          <p v-if="comment.source" class="text-xs text-quiet" data-testid="comment-source">
            Source: {{ comment.source }}
          </p>

          <template v-if="check">
            <CheckSources :sources="check.sources" />
            <details v-if="check.search_queries.length" data-testid="check-search-queries">
              <summary class="cursor-pointer text-xs font-semibold text-quiet">
                Searched for ({{ check.search_queries.length }})
              </summary>
              <ul class="mt-1 list-disc space-y-0.5 pl-5 text-xs text-quiet">
                <li v-for="(q, i) in check.search_queries" :key="i">{{ q }}</li>
              </ul>
            </details>
            <p class="text-xs text-quiet" data-testid="check-models">{{ checkModels }}</p>
          </template>

          <slot v-if="isTranscription && !answered" name="answer" />
          <slot name="actions" />
          <slot name="thread" />
        </div>
      </div>
      <p v-if="!active" class="text-xs text-quiet" aria-hidden="true">{{ hint }}</p>
    </template>

    <!-- Resolved / disagreed cards keep their controls (Reopen, reply count) on the single line's card. -->
    <template v-if="collapsed">
      <slot name="actions" />
      <slot name="thread" />
    </template>
  </article>
</template>
