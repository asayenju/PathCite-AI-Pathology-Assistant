<script setup lang="ts">
import { nextTick, ref, useId, watch } from 'vue'
import { Check, RotateCcw, ThumbsDown } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import type { ReviewThread } from '@/composables/useTranscriptReview'

/**
 * Resolve / Disagree controls for an open comment, Reopen for a resolved or disagreed one. Presentational: it
 * emits and the host calls useTranscriptReview's resolve / disagree / reopen. The disagree reason is emitted as
 * typed (the composable trims it, blank becomes null). Clicks stop here so they don't also activate the card.
 */
const props = defineProps<{ comment: ReviewThread }>()

const emit = defineEmits<{
  resolve: []
  reopen: []
  disagree: [reason: string | null]
}>()

const disagreeing = ref(false)
const reason = ref('')
const reasonEl = ref<HTMLTextAreaElement | null>(null)
const disagreeButton = ref<{ $el: HTMLElement } | null>(null)
const reasonId = `disagree-reason-${useId()}`

async function openForm() {
  disagreeing.value = true
  await nextTick()
  reasonEl.value?.focus()
}

async function cancel() {
  disagreeing.value = false
  reason.value = ''
  await nextTick()
  disagreeButton.value?.$el.focus()
}

function submit() {
  const text = reason.value
  disagreeing.value = false
  reason.value = ''
  emit('disagree', text.trim() ? text : null)
}

// Closing the form if the comment changes status from elsewhere (e.g. resolved via another control).
watch(
  () => props.comment.status,
  () => {
    disagreeing.value = false
    reason.value = ''
  },
)
</script>

<template>
  <div
    class="flex flex-wrap items-center gap-2"
    :class="{ 'mt-2': comment.status !== 'open' }"
    data-testid="comment-actions"
    @click.stop
  >
    <template v-if="comment.status === 'open'">
      <template v-if="!disagreeing">
        <Button type="button" size="sm" data-testid="resolve" @click="emit('resolve')">
          <Check aria-hidden="true" />
          Resolve
        </Button>
        <Button
          ref="disagreeButton"
          type="button"
          size="sm"
          variant="outline"
          data-testid="disagree"
          @click="openForm"
        >
          <ThumbsDown aria-hidden="true" />
          Disagree
        </Button>
      </template>

      <form
        v-else
        class="w-full space-y-2"
        data-testid="disagree-form"
        @submit.prevent="submit"
        @keydown.escape.stop.prevent="cancel"
      >
        <label :for="reasonId" class="block text-xs font-semibold text-quiet">
          Why is the agent wrong? (optional)
        </label>
        <textarea
          :id="reasonId"
          ref="reasonEl"
          v-model="reason"
          rows="2"
          class="field-sizing-content block max-h-40 min-h-14 w-full resize-none rounded-md border border-frost bg-glass px-2 py-1.5 text-sm text-ink focus-visible:outline-2 focus-visible:outline-offset-1"
          data-testid="disagree-reason"
        />
        <div class="flex flex-wrap gap-2">
          <Button type="submit" size="sm" data-testid="disagree-submit">Submit disagreement</Button>
          <Button type="button" size="sm" variant="ghost" data-testid="disagree-cancel" @click="cancel">
            Cancel
          </Button>
        </div>
      </form>
    </template>

    <Button
      v-else
      type="button"
      size="xs"
      variant="outline"
      data-testid="reopen"
      @click="emit('reopen')"
    >
      <RotateCcw aria-hidden="true" />
      Reopen
    </Button>
  </div>
</template>
