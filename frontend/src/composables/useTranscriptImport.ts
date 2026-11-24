import { computed, getCurrentInstance, onBeforeUnmount, ref, shallowRef } from 'vue'
import { PredictError } from '@/lib/api'
import { sampleUrl } from '@/lib/slide'
import { SAMPLE_LOAD_ERROR } from '@/lib/transcriptSamples'
import { importTranscript } from '@/lib/transcripts'
import type { Role, RoledTurn, TranscriptImport, TranscriptSample } from '@/types'

/**
 * idle → loading → review (or error) → confirmed. Review is where speaker roles are assigned; confirm
 * needs at least one doctor, and editRoles goes back from confirmed to review.
 */
export type TranscriptStatus = 'idle' | 'loading' | 'review' | 'confirmed' | 'error'

/** Holds the imported transcript. Memory only: nothing goes to localStorage or sessionStorage. */
export function useTranscriptImport() {
  const status = ref<TranscriptStatus>('idle')
  const transcript = shallowRef<TranscriptImport | null>(null)
  const errorMessage = ref('')
  const fileName = ref<string | null>(null)
  /** Role per speaker id; null is unassigned. Seeded from the server's suggestions on import. */
  const roles = ref<Record<string, Role | null>>({})
  /** Set while the transcript in place (or loading) is one of the bundled synthetic samples. */
  const sample = shallowRef<TranscriptSample | null>(null)

  /** Several doctors are allowed (resident plus attending). */
  const canConfirm = computed(() => Object.values(roles.value).some((role) => role === 'doctor'))

  /** The turns with their speaker's role, only once confirmed. */
  const confirmedTranscript = computed<RoledTurn[] | null>(() => {
    if (status.value !== 'confirmed' || !transcript.value) return null
    return transcript.value.turns.map((turn) => ({ ...turn, role: roles.value[turn.speaker] ?? null }))
  })

  let controller: AbortController | null = null

  /** Drop whatever is in place or in flight and show the loading state for `name`. */
  function begin(name: string): AbortSignal {
    controller?.abort()
    controller = new AbortController()
    fileName.value = name
    transcript.value = null
    roles.value = {}
    errorMessage.value = ''
    status.value = 'loading'
    return controller.signal
  }

  /** THE import path: validate, upload, review. Uploads and samples both end here. Clears the sample marker. */
  async function readFile(file: File) {
    sample.value = null
    const signal = begin(file.name)
    try {
      const imported = await importTranscript(file, signal)
      transcript.value = imported
      roles.value = Object.fromEntries(imported.speakers.map((s) => [s.id, s.suggested_role]))
      status.value = 'review'
    } catch (error) {
      if (signal.aborted) return
      errorMessage.value =
        error instanceof PredictError ? error.message : 'Something went wrong. Try again.'
      status.value = 'error'
    }
  }

  /**
   * A bundled sample: fetch its JSON, wrap it as a File and import it exactly like an upload. It replaces
   * whatever is in place, in any state, and a newer file or sample aborts it.
   */
  async function readSample(chosen: TranscriptSample) {
    const name = `${chosen.id}.json`
    const signal = begin(name)
    sample.value = chosen
    let file: File
    try {
      const response = await fetch(sampleUrl(chosen.file), { signal })
      if (!response.ok) throw new Error(String(response.status))
      file = new File([await response.blob()], name, { type: 'application/json' })
    } catch {
      if (signal.aborted) return
      errorMessage.value = SAMPLE_LOAD_ERROR
      status.value = 'error'
      return
    }
    if (signal.aborted) return
    const done = readFile(file)
    sample.value = chosen // readFile clears it synchronously; this import is the sample's
    await done
  }

  function setRole(speakerId: string, role: Role | null) {
    if (status.value !== 'review') return
    roles.value = { ...roles.value, [speakerId]: role }
  }

  function confirm() {
    if (status.value === 'review' && canConfirm.value) status.value = 'confirmed'
  }

  function editRoles() {
    if (status.value === 'confirmed') status.value = 'review'
  }

  function reset() {
    controller?.abort()
    controller = null
    transcript.value = null
    roles.value = {}
    errorMessage.value = ''
    fileName.value = null
    sample.value = null
    status.value = 'idle'
  }

  if (getCurrentInstance()) onBeforeUnmount(() => controller?.abort())

  return {
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
  }
}
