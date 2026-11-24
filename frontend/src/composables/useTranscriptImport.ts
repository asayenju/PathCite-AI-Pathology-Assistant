import { getCurrentInstance, onBeforeUnmount, ref, shallowRef } from 'vue'
import { PredictError } from '@/lib/api'
import { importTranscript } from '@/lib/transcripts'
import type { TranscriptImport } from '@/types'

/**
 * idle → loading → review (or error). `confirmed` is reached once roles are assigned and confirmed
 * (speaker role assignment adds that step on top of this composable).
 */
export type TranscriptStatus = 'idle' | 'loading' | 'review' | 'confirmed' | 'error'

/** Holds the imported transcript. Memory only: nothing goes to localStorage or sessionStorage. */
export function useTranscriptImport() {
  const status = ref<TranscriptStatus>('idle')
  const transcript = shallowRef<TranscriptImport | null>(null)
  const errorMessage = ref('')
  const fileName = ref<string | null>(null)

  let controller: AbortController | null = null

  async function readFile(file: File) {
    controller?.abort()
    controller = new AbortController()
    const { signal } = controller

    fileName.value = file.name
    transcript.value = null
    errorMessage.value = ''
    status.value = 'loading'

    try {
      transcript.value = await importTranscript(file, signal)
      status.value = 'review'
    } catch (error) {
      if (signal.aborted) return
      errorMessage.value =
        error instanceof PredictError ? error.message : 'Something went wrong. Try again.'
      status.value = 'error'
    }
  }

  function reset() {
    controller?.abort()
    controller = null
    transcript.value = null
    errorMessage.value = ''
    fileName.value = null
    status.value = 'idle'
  }

  if (getCurrentInstance()) onBeforeUnmount(() => controller?.abort())

  return { status, transcript, errorMessage, fileName, readFile, reset }
}
