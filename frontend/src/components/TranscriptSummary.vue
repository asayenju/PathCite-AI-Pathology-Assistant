<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { SOURCE_NAMES, formatClock } from '@/lib/transcripts'
import type { TranscriptImport } from '@/types'

const props = defineProps<{ transcript: TranscriptImport; fileName: string | null }>()

const facts = computed(() => [
  { label: 'Source', value: SOURCE_NAMES[props.transcript.source] ?? props.transcript.source },
  { label: 'Language', value: props.transcript.language ?? 'Not given' },
  { label: 'Duration', value: formatClock(props.transcript.duration_s, 'Unknown') },
  { label: 'Speakers', value: String(props.transcript.speakers.length) },
  { label: 'Turns', value: String(props.transcript.turns.length) },
])
</script>

<template>
  <div class="space-y-6" data-testid="transcript-summary">
    <div>
      <h3 class="cond text-4xl leading-tight font-semibold tracking-tight">Transcript imported</h3>
      <p v-if="fileName" class="mt-1 text-sm break-all text-quiet">{{ fileName }}</p>
    </div>

    <dl class="grid max-w-[34rem] grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-5">
      <div v-for="fact in facts" :key="fact.label">
        <dt class="text-sm text-quiet">{{ fact.label }}</dt>
        <dd class="tnum font-semibold">{{ fact.value }}</dd>
      </div>
    </dl>

    <Alert v-if="transcript.warnings.length" class="max-w-[48ch] border-amber bg-glass text-ink">
      <CircleAlert class="text-amber" />
      <AlertTitle>Check the transcript</AlertTitle>
      <AlertDescription class="text-ink">
        <ul class="list-disc space-y-1 pl-4">
          <li v-for="warning in transcript.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </AlertDescription>
    </Alert>

    <p class="max-w-[48ch] text-sm text-quiet">{{ transcript.notice }}</p>
  </div>
</template>
