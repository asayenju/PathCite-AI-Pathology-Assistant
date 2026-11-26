<script setup lang="ts">
import { computed, useId } from 'vue'
import { ChevronDown, ChevronUp, Download, LoaderCircle, Square } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import type { ReviewStatus } from '@/composables/useTranscriptReview'

/**
 * The bar above a review: status, counts, Previous/Next issue, Show resolved, Stop and Export. Presentational: the
 * host wires the events to useTranscriptReview (prevIssue / nextIssue / stop / exportReview + downloadJson).
 * Every control is a native button or checkbox, so it all works from the keyboard.
 *
 * With `triageCounts` (a Jev review) the counts are claims, open transcription checks and skipped spans, and
 * Previous/Next are named for claims and transcription checks.
 */
const props = defineProps<{
  status: ReviewStatus
  counts: { open: number; resolved: number; disagreed: number }
  showResolved: boolean
  /** At least one open issue exists (Previous/Next have somewhere to go). */
  hasOpen: boolean
  canExport: boolean
  /** Shown when the review is in error (useTranscriptReview().errorMessage). */
  errorMessage?: string
  /** A badge such as JEV_REVIEW_LABEL from lib/review; nothing is rendered when it's absent. */
  demoLabel?: string
  /** useTranscriptReview().triageCounts, for a Jev review. */
  triageCounts?: { claims: number; openTranscriptions: number; skipped: number }
}>()

const emit = defineEmits<{
  prev: []
  next: []
  stop: []
  export: []
  'update:showResolved': [value: boolean]
}>()

const toggleId = `show-resolved-${useId()}`

const closed = computed(() => props.counts.resolved + props.counts.disagreed)

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
const triageText = computed(() => {
  const t = props.triageCounts
  if (!t) return ''
  return [
    plural(t.claims, 'claim', 'claims'),
    plural(t.openTranscriptions, 'open transcription check', 'open transcription checks'),
    plural(t.skipped, 'skipped span', 'skipped spans'),
  ].join(' · ')
})
const stepNoun = computed(() => (props.triageCounts ? 'claim or transcription check' : 'issue'))

const statusText = computed(() => {
  switch (props.status) {
    case 'running':
      return 'Reviewing…'
    case 'done':
      return 'Review complete'
    case 'stopped':
      return 'Review stopped'
    case 'error':
      return props.errorMessage || 'The review stopped because of an error.'
    default:
      return ''
  }
})

function onToggle(event: Event) {
  emit('update:showResolved', (event.target as HTMLInputElement).checked)
}
</script>

<template>
  <div class="space-y-2" data-testid="review-header">
    <p
      v-if="demoLabel"
      class="inline-flex rounded-full border border-amber bg-amber/10 px-2.5 py-0.5 text-xs font-semibold text-ink"
      data-testid="review-demo-label"
    >
      {{ demoLabel }}
    </p>

    <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
      <p
        class="flex items-center gap-1.5 text-sm font-semibold"
        :class="status === 'error' ? 'text-eosin' : 'text-ink'"
        role="status"
        aria-live="polite"
        data-testid="review-status"
      >
        <LoaderCircle v-if="status === 'running'" class="size-4 motion-safe:animate-spin" aria-hidden="true" />
        {{ statusText }}
      </p>

      <p v-if="triageCounts" class="text-sm text-quiet tabular-nums" data-testid="review-triage-counts">
        {{ triageText }}
      </p>
      <p v-else class="text-sm text-quiet tabular-nums" data-testid="review-counts">
        {{ counts.open }} open · {{ counts.resolved }} resolved · {{ counts.disagreed }} disagreed
      </p>

      <div class="flex items-center gap-1">
        <Button
          type="button"
          size="icon-sm"
          variant="outline"
          :aria-label="`Previous ${stepNoun}`"
          :title="`Previous ${stepNoun}`"
          :disabled="!hasOpen"
          data-testid="review-prev"
          @click="emit('prev')"
        >
          <ChevronUp aria-hidden="true" />
        </Button>
        <Button
          type="button"
          size="icon-sm"
          variant="outline"
          :aria-label="`Next ${stepNoun}`"
          :title="`Next ${stepNoun}`"
          :disabled="!hasOpen"
          data-testid="review-next"
          @click="emit('next')"
        >
          <ChevronDown aria-hidden="true" />
        </Button>
      </div>

      <div class="flex items-center gap-2">
        <input
          :id="toggleId"
          type="checkbox"
          class="size-4 accent-ink"
          :checked="showResolved"
          data-testid="review-show-resolved"
          @change="onToggle"
        />
        <label :for="toggleId" class="text-sm text-ink">
          Show resolved <span class="text-quiet tabular-nums">({{ closed }})</span>
        </label>
      </div>

      <div class="ml-auto flex items-center gap-2">
        <Button
          v-if="status === 'running'"
          type="button"
          size="sm"
          variant="outline"
          data-testid="review-stop"
          @click="emit('stop')"
        >
          <Square aria-hidden="true" />
          Stop
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          :disabled="!canExport"
          data-testid="review-export"
          @click="emit('export')"
        >
          <Download aria-hidden="true" />
          Export review
        </Button>
      </div>
    </div>
  </div>
</template>
