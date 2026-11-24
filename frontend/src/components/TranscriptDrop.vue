<script setup lang="ts">
import { ref } from 'vue'
import { CircleAlert } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useTranscriptImport } from '@/composables/useTranscriptImport'
import { TRANSCRIPT_ACCEPT } from '@/lib/transcripts'
import SpeakerRoles from './SpeakerRoles.vue'
import TranscriptSummary from './TranscriptSummary.vue'
import TranscriptView from './TranscriptView.vue'

/** The Transcripts view: drop or pick a diarized transcript JSON, assign speaker roles, confirm. Memory only. */
const {
  status,
  transcript,
  errorMessage,
  fileName,
  roles,
  canConfirm,
  confirmedTranscript,
  readFile,
  setRole,
  confirm,
  editRoles,
  reset,
} = useTranscriptImport()

const over = ref(false)
const picker = ref<HTMLInputElement | null>(null)

function onDrop(event: DragEvent) {
  over.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) readFile(file)
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) readFile(file)
  input.value = '' // allow choosing the same file again
}
</script>

<template>
  <section aria-labelledby="transcript-title" class="max-w-[52rem]">
    <h2 id="transcript-title" class="mb-3 text-sm font-semibold text-quiet">Transcript</h2>

    <div aria-live="polite" :aria-busy="status === 'loading'">
      <!-- Review: summary (with warnings), speaker roles, then the turn-by-turn view. -->
      <div v-if="transcript && (status === 'review' || status === 'confirmed')" class="space-y-8">
        <TranscriptSummary :transcript="transcript" :file-name="fileName" />
        <SpeakerRoles
          :speakers="transcript.speakers"
          :roles="roles"
          :confirmed="status === 'confirmed'"
          :can-confirm="canConfirm"
          @set-role="setRole"
          @confirm="confirm"
          @edit="editRoles"
        />
        <TranscriptView :turns="transcript.turns" :roles="roles" />
        <!-- Later steps (fact checking) consume the confirmed, role-tagged turns from here. -->
        <slot name="review" :transcript="transcript" :status="status" :confirmed-transcript="confirmedTranscript" />
        <Button variant="outline" size="sm" @click="reset">Clear transcript</Button>
      </div>

      <!-- Idle, loading or error: the drop zone stays available so a new file can replace a bad one. -->
      <div v-else class="space-y-5">
        <button
          type="button"
          aria-label="Drop zone. Drop a transcript JSON file here, or press Enter to choose a file."
          class="relative grid min-h-56 w-full place-content-center gap-1 rounded-glass border-2 border-dashed bg-glass px-6 text-center transition-colors [&>*]:pointer-events-none"
          :class="[over ? 'border-ink' : 'border-frost', status === 'loading' && 'ring-pulse']"
          @click="picker?.click()"
          @dragenter.prevent="over = true"
          @dragover.prevent="over = true"
          @dragleave="over = false"
          @drop.prevent="onDrop"
        >
          <span class="cond text-3xl font-semibold tracking-tight">
            {{ status === 'loading' ? 'Reading the transcript…' : 'Drop a transcript here' }}
          </span>
          <span class="text-sm text-quiet">
            or choose a JSON file. Deepgram, AWS Transcribe, WhisperX or OpenAI diarized output, up to 20 MB.
          </span>
        </button>
        <input ref="picker" type="file" :accept="TRANSCRIPT_ACCEPT" class="hidden" @change="onPick" />

        <p v-if="status === 'loading'" role="status" class="text-sm">
          Importing <span class="break-all">{{ fileName }}</span>
        </p>

        <Alert v-else-if="status === 'error'" class="max-w-[52ch] border-eosin bg-glass text-ink">
          <CircleAlert class="text-eosin" />
          <AlertTitle>The transcript wasn't imported</AlertTitle>
          <AlertDescription class="text-ink">
            <p>{{ errorMessage }}</p>
            <Button variant="outline" size="sm" class="mt-3" @click="reset">Clear</Button>
          </AlertDescription>
        </Alert>
      </div>
    </div>
  </section>
</template>
