import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { PredictError, predict } from '@/lib/api'
import type { PredictResponse, Sample } from '@/types'

export type Status = 'idle' | 'loading' | 'result' | 'error'

/** Holds what is on the stage and the prediction for it. */
export function usePredict() {
  const status = ref<Status>('idle')
  const previewUrl = ref<string | null>(null)
  /** Set when the image on stage is a bundled slide (so we know its true class). */
  const sample = shallowRef<Sample | null>(null)
  const result = shallowRef<PredictResponse | null>(null)
  const errorMessage = ref('')

  let controller: AbortController | null = null

  function clearPreview() {
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = null
  }

  async function run(file: File, forSample: Sample | null) {
    controller?.abort()
    controller = new AbortController()
    const { signal } = controller

    clearPreview()
    previewUrl.value = URL.createObjectURL(file)
    sample.value = forSample
    result.value = null
    errorMessage.value = ''
    status.value = 'loading'

    try {
      result.value = await predict(file, signal)
      status.value = 'result'
    } catch (error) {
      if (signal.aborted) return
      errorMessage.value =
        error instanceof PredictError ? error.message : 'Something went wrong. Try again.'
      status.value = 'error'
    }
  }

  function readFile(file: File) {
    return run(file, null)
  }

  async function readSample(chosen: Sample) {
    try {
      const response = await fetch(`${import.meta.env.BASE_URL}${chosen.file}`)
      if (!response.ok) throw new Error(String(response.status))
      const blob = await response.blob()
      await run(new File([blob], `${chosen.code}.jpg`, { type: 'image/jpeg' }), chosen)
    } catch {
      controller?.abort()
      clearPreview()
      sample.value = null
      result.value = null
      errorMessage.value = "This slide couldn't be loaded. Reload the page and try again."
      status.value = 'error'
    }
  }

  function reset() {
    controller?.abort()
    clearPreview()
    sample.value = null
    result.value = null
    errorMessage.value = ''
    status.value = 'idle'
  }

  onBeforeUnmount(() => {
    controller?.abort()
    clearPreview()
  })

  return { status, previewUrl, sample, result, errorMessage, readFile, readSample, reset }
}
