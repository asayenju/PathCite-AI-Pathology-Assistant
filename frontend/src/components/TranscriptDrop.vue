<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { CircleAlert, Sparkles } from 'lucide-vue-next'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useTranscriptImport } from '@/composables/useTranscriptImport'
import { useTranscriptReview } from '@/composables/useTranscriptReview'
import { jevReviewSource } from '@/lib/jevReviewSource'
import { JEV_REVIEW_LABEL, downloadJson } from '@/lib/review'
import { TRANSCRIPT_MIME, findSample } from '@/lib/transcriptSamples'
import { TRANSCRIPT_ACCEPT } from '@/lib/transcripts'
import type { Role, TranscriptManifest, TranscriptSample } from '@/types'
import CommentActions from './CommentActions.vue'
import CommentThread from './CommentThread.vue'
import ReviewHeader from './ReviewHeader.vue'
import ReviewMargin from './ReviewMargin.vue'
import SpeakerRoles from './SpeakerRoles.vue'
import TranscriptFolders from './TranscriptFolders.vue'
import TranscriptSummary from './TranscriptSummary.vue'
import TranscriptReviewLayout from './TranscriptReviewLayout.vue'
import TranscriptView from './TranscriptView.vue'
import TranscriptionAnswer from './TranscriptionAnswer.vue'

/**
 * The Transcripts view: drop or pick a diarized transcript JSON (or a bundled synthetic sample) and check the speaker
 * roles. Memory only. Without a manifest the sample folders are simply not shown.
 *
 * With at least one doctor, "Review with agent" streams Jev triage of the doctor turns (POST /api/triage, see
 * docs/PRD-jev-triage.md) for any transcript, samples and uploads alike: claims go to the (stub) claim checker and
 * possible transcription errors ask the doctor "Is this correct?". Only doctor turns are sent. Clear, a new file or
 * sample, and a role change discard the review. The hand-written mock in src/mocks/review is test data only.
 */
const props = withDefaults(defineProps<{ manifest?: TranscriptManifest | null }>(), { manifest: null })

const {
  status,
  transcript,
  errorMessage,
  fileName,
  roles,
  sample,
  hasDoctor,
  roledTurns,
  readFile,
  readSample,
  setRole,
  reset,
} = useTranscriptImport()

const {
  status: reviewStatus,
  errorMessage: reviewError,
  currentTurnId,
  doneTurnIds,
  comments,
  activeCommentId,
  hasResults,
  counts,
  triageCounts,
  issueOrder,
  start: startReviewRun,
  stop: stopReview,
  reset: resetReview,
  resolve,
  reopen,
  disagree,
  setActive,
  reply,
  canReply,
  confirmTranscription,
  correctTranscription,
  nextIssue,
  prevIssue,
  exportReview,
} = useTranscriptReview({ sourceName: 'jev' })

const showResolved = ref(false)
/** A review was started for the transcript in place (running, done, stopped or error). */
const reviewing = computed(() => reviewStatus.value !== 'idle')
const canReview = computed(() => !!roledTurns.value && hasDoctor.value)
const reviewUnavailable = 'Mark at least one speaker as the doctor to run a review.'

/**
 * Where to scroll after an activation: clicking a highlight goes to its comment, clicking a comment goes to its
 * highlight. `n` makes a repeat click on the same item scroll again.
 */
const focusRequest = ref<{ id: string; target: 'card' | 'highlight'; n: number } | null>(null)
let focusCount = 0

function activateFrom(target: 'card' | 'highlight') {
  return (id: string) => {
    setActive(id)
    focusRequest.value = { id, target, n: ++focusCount }
  }
}
const onHighlightActivate = activateFrom('card')
const onCardActivate = activateFrom('highlight')

function goTo(step: () => string | null) {
  const id = step()
  if (id) focusRequest.value = { id, target: 'highlight', n: ++focusCount }
}

function discardReview() {
  resetReview()
  showResolved.value = false
  focusRequest.value = null
}

/** Start, or restart (the run in place is discarded), the review of the turns with their current roles. */
function startReview() {
  const turns = roledTurns.value
  if (!turns || !hasDoctor.value) return
  discardReview()
  // Made per run, so it uses the fetch in place now. It sends only the doctor turns.
  void startReviewRun(turns, { sampleId: sample.value?.id ?? null, source: jevReviewSource() })
  // The review opens below the roles; bring it up so the agent's progress is in view.
  void nextTick(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    reviewPanel.value?.scrollIntoView?.({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  })
}

const reviewPanel = ref<HTMLElement | null>(null)

/**
 * After an answer the Yes / Submit control goes away (the card turns into a claim, or collapses to "Not a claim"),
 * so focus goes to the card itself, unless the person has moved focus elsewhere in the meantime.
 */
async function keepFocusOnCard(id: string) {
  await nextTick()
  const card = document.getElementById(`comment-${id}`)
  if (!card) return
  const current = document.activeElement
  if (!current || current === document.body || card.contains(current)) card.focus({ preventScroll: true })
}

function onConfirm(id: string) {
  confirmTranscription(id)
  void keepFocusOnCard(id)
}

async function onCorrect(id: string, text: string) {
  await correctTranscription(id, text)
  // On failure the form stays, with focus in it and the error shown.
  if (comments.value.find((c) => c.id === id)?.correctionError) return
  await keepFocusOnCard(id)
}

function onExport() {
  const { filename, data } = exportReview()
  downloadJson(filename, data)
}

// Every way the transcript can change goes through these, so a review never outlives its transcript.
function loadFile(file: File) {
  discardReview()
  readFile(file)
}

function loadSample(chosen: TranscriptSample) {
  discardReview()
  readSample(chosen)
}

function clear() {
  discardReview()
  reset()
}

/** A role change invalidates the review (it decides which turns are checked); ask first when there is something to lose. */
function onSetRole(speakerId: string, role: Role | null) {
  if ((roles.value[speakerId] ?? null) === role) return
  if (reviewing.value) {
    if (hasResults.value && !window.confirm('Changing roles discards the current review. Continue?')) return
    discardReview()
  }
  setRole(speakerId, role)
}

// Belt and braces: whatever replaces the transcript also drops the review.
watch(transcript, (now, before) => {
  if (now !== before) discardReview()
})

const over = ref(false)
const picker = ref<HTMLInputElement | null>(null)

const carriesSample = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes(TRANSCRIPT_MIME)

/** A dropped sample replaces whatever is in place. Returns true if the drop was a sample. */
function dropSample(event: DragEvent): boolean {
  if (!carriesSample(event)) return false
  const file = event.dataTransfer?.getData(TRANSCRIPT_MIME)
  if (!file) return true
  const chosen = findSample(props.manifest, file)
  if (chosen) loadSample(chosen)
  return true
}

function onDrop(event: DragEvent) {
  over.value = false
  if (dropSample(event)) return
  const file = event.dataTransfer?.files?.[0]
  if (file) loadFile(file)
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
  if (file) loadFile(file)
  input.value = '' // allow choosing the same file again
}
</script>

<template>
  <!-- Same column rhythm as the Slides tab: folders on the left on wide screens, above on narrow ones. -->
  <!-- While reviewing, the transcript column drops its 52rem cap: the comment margin needs the room. -->
  <div
    class="grid gap-10"
    :class="
      manifest && (reviewing ? 'lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-14' : 'lg:grid-cols-[15rem_minmax(0,52rem)] lg:gap-14')
    "
  >
    <TranscriptFolders
      v-if="manifest"
      :manifest="manifest"
      :active-file="sample?.file ?? null"
      @pick="loadSample"
    />

    <section aria-labelledby="transcript-title" class="min-w-0" :class="!reviewing && 'max-w-[52rem]'">
      <h2 id="transcript-title" class="mb-3 text-sm font-semibold text-quiet">Transcript</h2>

      <!-- Streaming review content has its own status line; announcing every card here would be noise. -->
      <div :aria-live="reviewing ? 'off' : 'polite'" :aria-busy="status === 'loading'">
        <!-- Review: summary (with warnings), speaker roles, then the turn-by-turn view. -->
        <div
          v-if="transcript && status === 'review'"
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
          <SpeakerRoles :speakers="transcript.speakers" :roles="roles" @set-role="onSetRole" />

          <div class="flex flex-wrap items-center gap-3" data-testid="review-start">
            <Button
              size="sm"
              :disabled="!canReview"
              :aria-describedby="canReview ? undefined : 'review-unavailable'"
              data-testid="review-button"
              @click="startReview"
            >
              <Sparkles aria-hidden="true" />
              {{ reviewing ? 'Restart review' : 'Review with agent' }}
            </Button>
            <p v-if="!canReview" id="review-unavailable" class="text-sm text-quiet" data-testid="review-unavailable">
              {{ reviewUnavailable }}
            </p>
          </div>

          <!-- Reviewing: transcript and comment margin share one scroll container. -->
          <div
            v-if="reviewing"
            ref="reviewPanel"
            class="review-card-in scroll-mt-4 space-y-4"
            data-testid="transcript-agent-review"
          >
            <ReviewHeader
              :status="reviewStatus"
              :counts="counts"
              :triage-counts="triageCounts"
              :error-message="reviewError"
              :show-resolved="showResolved"
              :has-open="counts.open > 0"
              :can-export="hasResults"
              :demo-label="JEV_REVIEW_LABEL"
              @update:show-resolved="showResolved = $event"
              @prev="goTo(prevIssue)"
              @next="goTo(nextIssue)"
              @stop="stopReview"
              @export="onExport"
            />
            <TranscriptReviewLayout
              :focus-request="focusRequest"
              :follow-turn-id="reviewStatus === 'running' ? currentTurnId : null"
            >
              <template #transcript>
                <TranscriptView
                  :turns="transcript.turns"
                  :roles="roles"
                  :comments="comments"
                  :active-comment-id="activeCommentId"
                  :current-turn-id="currentTurnId"
                  :done-turn-ids="doneTurnIds"
                  :reviewing="true"
                  :scroll="false"
                  @activate="onHighlightActivate"
                />
              </template>
              <template #margin="{ container }">
                <ReviewMargin
                  :comments="comments"
                  :order="issueOrder"
                  :active-comment-id="activeCommentId"
                  :show-resolved="showResolved"
                  :container="container"
                  @activate="onCardActivate"
                >
                  <template #actions="{ comment }">
                    <CommentActions
                      :comment="comment"
                      @resolve="resolve(comment.id)"
                      @reopen="reopen(comment.id)"
                      @disagree="disagree(comment.id, $event)"
                    />
                  </template>
                  <!-- Jev only labels spans and can't answer questions, so triage cards have no reply thread. -->
                  <template #thread="{ comment }">
                    <CommentThread v-if="canReply(comment)" :comment="comment" @reply="reply(comment.id, $event)" />
                  </template>
                  <template #answer="{ comment }">
                    <TranscriptionAnswer
                      :comment="comment"
                      @confirm="onConfirm(comment.id)"
                      @correct="onCorrect(comment.id, $event)"
                    />
                  </template>
                </ReviewMargin>
              </template>
            </TranscriptReviewLayout>
          </div>
          <TranscriptView v-else :turns="transcript.turns" :roles="roles" />
          <!-- Later steps consume the role-tagged turns from here. -->
          <slot name="review" :transcript="transcript" :status="status" :roled-turns="roledTurns" />
          <Button variant="outline" size="sm" @click="clear">Clear transcript</Button>
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
              <Button variant="outline" size="sm" class="mt-3" @click="clear">Clear</Button>
            </AlertDescription>
          </Alert>
        </div>
      </div>
    </section>
  </div>
</template>
