import { computed, getCurrentInstance, onBeforeUnmount, ref, shallowRef } from 'vue'
import { PredictError } from '@/lib/api'
import { importTranscript } from '@/lib/transcripts'
import type { Role, RoledTurn, TranscriptImport } from '@/types'

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

  /** Several doctors are allowed (resident plus attending). */
  const canConfirm = computed(() => Object.values(roles.value).some((role) => role === 'doctor'))

  /** The turns with their speaker's role, only once confirmed. */
  const confirmedTranscript = computed<RoledTurn[] | null>(() => {
    if (status.value !== 'confirmed' || !transcript.value) return null
    return transcript.value.turns.map((turn) => ({ ...turn, role: roles.value[turn.speaker] ?? null }))
  })

  let controller: AbortController | null = null

  async function readFile(file: File) {
    controller?.abort()
    controller = new AbortController()
    const { signal } = controller

    fileName.value = file.name
    transcript.value = null
    roles.value = {}
    errorMessage.value = ''
    status.value = 'loading'

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
    status.value = 'idle'
  }

  if (getCurrentInstance()) onBeforeUnmount(() => controller?.abort())

  return {
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
  }
}
