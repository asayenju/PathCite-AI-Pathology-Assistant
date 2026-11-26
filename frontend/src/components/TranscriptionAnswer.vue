<script setup lang="ts">
import { computed, nextTick, ref, useId } from 'vue'
import { Check, LoaderCircle, PencilLine } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import type { ReviewThread } from '@/composables/useTranscriptReview'

/**
 * The Yes / No answer on a Jev transcription card ("Possible transcription error. Is this correct?"). Presentational:
 * it emits `confirm` (Yes) or `correct` with the typed text (No → "What was said?" → Submit), and the host calls
 * useTranscriptReview's confirmTranscription / correctTranscription. The transcript text is never edited.
 *
 * Submit is disabled for blank text and while the correction is being checked (`correctionPending`); a failed check
 * shows `correctionError` and keeps the form and its text, so the card stays answerable. After No, focus moves to the
 * input; Escape or Cancel closes the form and returns focus to No. Clicks stop here so they don't activate the card.
 */
const props = defineProps<{ comment: ReviewThread }>()

const emit = defineEmits<{ confirm: []; correct: [text: string] }>()

const correcting = ref(false)
const text = ref('')
const inputEl = ref<HTMLInputElement | null>(null)
const noButton = ref<{ $el: HTMLElement } | null>(null)
const inputId = `correction-${useId()}`
const errorId = `${inputId}-error`

const pending = computed(() => props.comment.correctionPending)
const canSubmit = computed(() => !pending.value && text.value.trim().length > 0)

async function openForm() {
  correcting.value = true
  await nextTick()
  inputEl.value?.focus()
}

async function cancel() {
  correcting.value = false
  text.value = ''
  await nextTick()
  noButton.value?.$el.focus()
}

function submit() {
  if (!canSubmit.value) return
  emit('correct', text.value)
}
</script>

<template>
  <div class="space-y-2" data-testid="transcription-answer" @click.stop>
    <div role="group" aria-label="Is this correct?" class="flex flex-wrap gap-2">
      <Button type="button" size="sm" :disabled="pending" data-testid="transcription-yes" @click="emit('confirm')">
        <Check aria-hidden="true" />
        Yes
      </Button>
      <Button
        ref="noButton"
        type="button"
        size="sm"
        variant="outline"
        :disabled="pending"
        :aria-expanded="correcting ? 'true' : 'false'"
        data-testid="transcription-no"
        @click="openForm"
      >
        <PencilLine aria-hidden="true" />
        No
      </Button>
    </div>

    <form
      v-if="correcting"
      class="space-y-2"
      data-testid="correction-form"
      @submit.prevent="submit"
      @keydown.escape.stop.prevent="cancel"
    >
      <label :for="inputId" class="block text-xs font-semibold text-quiet">What was said?</label>
      <input
        :id="inputId"
        ref="inputEl"
        v-model="text"
        type="text"
        autocomplete="off"
        :aria-describedby="comment.correctionError ? errorId : undefined"
        :aria-invalid="comment.correctionError ? 'true' : undefined"
        class="block w-full rounded-md border border-frost bg-glass px-2 py-1.5 text-sm text-ink focus-visible:outline-2 focus-visible:outline-offset-1"
        data-testid="correction-input"
      />
      <p
        v-if="pending"
        role="status"
        class="flex items-center gap-1.5 text-sm text-quiet"
        data-testid="correction-pending"
      >
        <LoaderCircle class="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
        Checking your correction…
      </p>
      <p v-else-if="comment.correctionError" :id="errorId" role="alert" class="text-sm text-eosin" data-testid="correction-error">
        {{ comment.correctionError }}
      </p>
      <div class="flex flex-wrap gap-2">
        <Button type="submit" size="sm" :disabled="!canSubmit" data-testid="correction-submit">Submit</Button>
        <Button type="button" size="sm" variant="ghost" :disabled="pending" data-testid="correction-cancel" @click="cancel">
          Cancel
        </Button>
      </div>
    </form>
  </div>
</template>
