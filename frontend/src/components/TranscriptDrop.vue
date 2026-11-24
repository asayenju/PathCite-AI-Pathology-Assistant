<script setup lang="ts">
import { ref } from 'vue'
import { CircleAlert } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useTranscriptImport } from '@/composables/useTranscriptImport'
import { TRANSCRIPT_MIME, findSample } from '@/lib/transcriptSamples'
import { TRANSCRIPT_ACCEPT } from '@/lib/transcripts'
import type { TranscriptManifest } from '@/types'
import SpeakerRoles from './SpeakerRoles.vue'
import TranscriptFolders from './TranscriptFolders.vue'
import TranscriptSummary from './TranscriptSummary.vue'
import TranscriptView from './TranscriptView.vue'

/**
 * The Transcripts view: drop or pick a diarized transcript JSON (or a bundled synthetic sample), assign speaker
 * roles, confirm. Memory only. Without a manifest the sample folders are simply not shown.
 */
const props = withDefaults(defineProps<{ manifest?: TranscriptManifest | null }>(), { manifest: null })

const {
  status,
  transcript,
  errorMessage,
  fileName,
  roles,
  sample,
  canConfirm,
  confirmedTranscript,
  readFile,
  readSample,
  setRole,
  confirm,
  editRoles,
  reset,
} = useTranscriptImport()

const over = ref(false)
const picker = ref<HTMLInputElement | null>(null)

const carriesSample = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes(TRANSCRIPT_MIME)

/** A dropped sample replaces whatever is in place. Returns true if the drop was a sample. */
function dropSample(event: DragEvent): boolean {
  if (!carriesSample(event)) return false
  const file = event.dataTransfer?.getData(TRANSCRIPT_MIME)
  if (!file) return true
  const chosen = findSample(props.manifest, file)
  if (chosen) readSample(chosen)
  return true
}

function onDrop(event: DragEvent) {
  over.value = false
  if (dropSample(event)) return
  const file = event.dataTransfer?.files?.[0]
  if (file) readFile(file)
}

/** In review only samples can be dropped (files keep needing Clear first), so only they get the drop target. */
const overReview = ref(false)

function onReviewDragOver(event: DragEvent) {
  if (!carriesSample(event)) return
  event.preventDefault()
  overReview.value = true
}

function onReviewDrop(event: DragEvent) {
  overReview.value = false
  if (!carriesSample(event)) return
  event.preventDefault()
  dropSample(event)
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) readFile(file)
  input.value = '' // allow choosing the same file again
}
</script>

<template>
  <!-- Same column rhythm as the Slides tab: folders on the left on wide screens, above on narrow ones. -->
  <div class="grid gap-10" :class="manifest && 'lg:grid-cols-[15rem_minmax(0,52rem)] lg:gap-14'">
    <TranscriptFolders
      v-if="manifest"
      :manifest="manifest"
      :active-file="sample?.file ?? null"
      @pick="readSample"
    />

    <section aria-labelledby="transcript-title" class="min-w-0 max-w-[52rem]">
      <h2 id="transcript-title" class="mb-3 text-sm font-semibold text-quiet">Transcript</h2>

      <div aria-live="polite" :aria-busy="status === 'loading'">
        <!-- Review: summary (with warnings), speaker roles, then the turn-by-turn view. -->
        <div
          v-if="transcript && (status === 'review' || status === 'confirmed')"
          data-testid="transcript-review"
          class="space-y-8 rounded-glass transition-shadow"
          :class="overReview && 'shadow-[0_0_0_2px_var(--color-ink)]'"
          @dragenter="onReviewDragOver"
          @dragover="onReviewDragOver"
          @dragleave="overReview = false"
          @drop="onReviewDrop"
        >
          <p v-if="sample" data-testid="sample-notice" class="text-sm text-quiet">
            Synthetic sample: {{ sample.title }}. {{ manifest?.notice }}
          </p>
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
  </div>
</template>
