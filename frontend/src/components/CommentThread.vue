<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { LoaderCircle, Send } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import type { ReviewThread } from '@/composables/useTranscriptReview'

/**
 * A comment's reply thread and reply box. Presentational: it emits `reply` with the question as typed and the host
 * calls useTranscriptReview().reply(id, question), which appends it plus a pending agent message.
 *
 * Open comments show the messages and the reply box. While an agent answer is pending, sending is blocked (Ask
 * disabled, Enter ignored) but the box stays editable so focus isn't dropped; `disabled` turns the whole box off.
 * The message list is always rendered as a polite live region, so the answer is announced when it arrives.
 * Collapsed (resolved / disagreed) comments only show a reply count, or nothing when there are no replies.
 * Clicks stop here so typing in the box doesn't also activate the card.
 */
const props = defineProps<{ comment: ReviewThread; disabled?: boolean }>()

const emit = defineEmits<{ reply: [question: string] }>()

const draft = ref('')
const inputId = `reply-${useId()}`

const open = computed(() => props.comment.status === 'open')
const pending = computed(() => props.comment.thread.some((m) => m.pending))
const blocked = computed(() => props.disabled || pending.value)
const canSend = computed(() => !blocked.value && draft.value.trim().length > 0)
/** Questions asked, which is what "replies" means to the person. */
const replyCount = computed(() => props.comment.thread.filter((m) => m.author === 'you').length)

function send() {
  if (!canSend.value) return
  emit('reply', draft.value)
  draft.value = ''
}

function onEnter(event: KeyboardEvent) {
  if (event.shiftKey || event.isComposing) return
  event.preventDefault()
  send()
}
</script>

<template>
  <div v-if="open" class="space-y-2" data-testid="comment-thread" @click.stop>
    <ol v-show="comment.thread.length" class="space-y-1.5 border-t border-frost pt-2" aria-live="polite" aria-label="Replies">
      <li
        v-for="message in comment.thread"
        :key="message.id"
        class="text-sm"
        :data-author="message.author"
        data-testid="thread-message"
      >
        <span class="block text-xs font-semibold text-quiet">{{ message.author === 'you' ? 'You' : 'Agent' }}</span>
        <span v-if="message.pending" class="flex items-center gap-1.5 text-quiet" data-testid="thread-pending">
          <LoaderCircle class="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
          Agent is thinking…
        </span>
        <span v-else class="whitespace-pre-wrap">{{ message.text }}</span>
      </li>
    </ol>

    <form class="flex items-end gap-2" data-testid="reply-form" @submit.prevent="send">
      <label :for="inputId" class="sr-only">Ask the agent to clarify</label>
      <textarea
        :id="inputId"
        v-model="draft"
        rows="1"
        placeholder="Ask the agent to clarify…"
        :disabled="disabled"
        class="field-sizing-content block max-h-32 min-h-9 w-full min-w-0 flex-1 resize-none rounded-md border border-frost bg-glass px-2 py-1.5 text-sm text-ink placeholder:text-quiet focus-visible:outline-2 focus-visible:outline-offset-1 disabled:opacity-60"
        data-testid="reply-input"
        @keydown.enter="onEnter"
      />
      <Button type="submit" size="sm" :disabled="!canSend" data-testid="reply-send">
        <Send aria-hidden="true" />
        Ask
      </Button>
    </form>
  </div>

  <p v-else-if="replyCount" class="mt-1 text-xs text-quiet" data-testid="thread-count">
    {{ replyCount }} {{ replyCount === 1 ? 'reply' : 'replies' }}
  </p>
</template>
