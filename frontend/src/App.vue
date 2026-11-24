<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Reading from '@/components/Reading.vue'
import SlideFolders from '@/components/SlideFolders.vue'
import Stage from '@/components/Stage.vue'
import TranscriptDrop from '@/components/TranscriptDrop.vue'
import { TooltipProvider } from '@/components/ui/tooltip'
import { usePredict } from '@/composables/usePredict'
import { sampleUrl } from '@/lib/slide'
import { loadTranscriptManifest } from '@/lib/transcriptSamples'
import type { Sample, TranscriptManifest } from '@/types'

type View = 'slides' | 'transcripts'
const VIEWS: { id: View; label: string }[] = [
  { id: 'slides', label: 'Slides' },
  { id: 'transcripts', label: 'Transcripts' },
]
/** No router: both panels stay mounted (v-show), so switching keeps each view's state. */
const view = ref<View>('slides')
const tabRefs = ref<HTMLButtonElement[]>([])

function onTabKey(event: KeyboardEvent, index: number) {
  const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
  if (!step) return
  event.preventDefault()
  const next = (index + step + VIEWS.length) % VIEWS.length
  view.value = VIEWS[next]!.id
  tabRefs.value[next]?.focus()
}

const samples = ref<Sample[]>([])
/** Bundled synthetic transcripts. Null (panel hidden) if the manifest is missing or malformed. */
const transcriptManifest = ref<TranscriptManifest | null>(null)
const { status, previewUrl, sample, result, errorMessage, readFile, readSample, reset } = usePredict()

onMounted(async () => {
  loadTranscriptManifest().then((manifest) => (transcriptManifest.value = manifest))
  try {
    const response = await fetch(sampleUrl('samples/samples.json'))
    samples.value = (await response.json()).samples
  } catch {
    samples.value = []
  }
})

const slideLabel = computed(() => sample.value?.code ?? null)

/** `file` is the dragged slide's unique path (several slides share a class). */
function onSlideDropped(file: string) {
  const dropped = samples.value.find((s) => s.file === file)
  if (dropped) readSample(dropped)
}
</script>

<template>
  <TooltipProvider :delay-duration="150">
    <div class="mx-auto flex min-h-screen max-w-[1280px] flex-col px-4 sm:px-8">
      <header class="flex items-baseline justify-between gap-4 py-5">
        <p class="cond text-2xl font-bold tracking-tight">PathCite</p>
        <p class="text-sm text-quiet">Research model. Not a diagnosis.</p>
      </header>

      <nav class="pt-1">
        <div role="tablist" aria-label="View" class="inline-flex gap-1 rounded-glass bg-frost/60 p-1">
          <button
            v-for="(tab, index) in VIEWS"
            :id="`tab-${tab.id}`"
            :key="tab.id"
            ref="tabRefs"
            type="button"
            role="tab"
            :aria-selected="view === tab.id"
            :aria-controls="`panel-${tab.id}`"
            :tabindex="view === tab.id ? 0 : -1"
            class="rounded-glass px-4 py-1.5 text-sm font-semibold transition-colors"
            :class="view === tab.id ? 'bg-glass text-ink shadow-xs' : 'text-quiet hover:text-ink'"
            @click="view = tab.id"
            @keydown="onTabKey($event, index)"
          >
            {{ tab.label }}
          </button>
        </div>
      </nav>

      <div
        v-show="view === 'slides'"
        id="panel-slides"
        role="tabpanel"
        aria-labelledby="tab-slides"
        class="flex flex-1 flex-col"
      >
        <p class="max-w-[62ch] pt-2 text-[1.05rem] leading-relaxed lg:pt-6">
          PathCite looks at one small patch of colorectal tissue, stained with H&amp;E, and says which of 9 tissue types
          it most resembles, such as tumor, stroma, muscle, mucus or fat. It also gives a score for how strongly the
          model favours that answer. Open a folder and drag a slide onto the stage, or drop your own PNG, JPEG or TIFF
          image. This is a research model and it can be wrong, so treat every reading as a hint to check by eye.
        </p>

        <main
          class="grid flex-1 gap-10 pb-12 pt-4 lg:grid-cols-[15rem_minmax(0,26rem)_minmax(0,1fr)] lg:gap-14 lg:pt-10"
        >
          <SlideFolders :samples="samples" :active-file="sample?.file ?? null" @pick="readSample" />

          <Stage
            :status="status"
            :preview-url="previewUrl"
            :scale-known="sample !== null"
            @slide="onSlideDropped"
            @file="readFile"
            @clear="reset"
          />

          <Reading
            :status="status"
            :result="result"
            :error-message="errorMessage"
            :slide-label="slideLabel"
            @clear="reset"
          />
        </main>
      </div>

      <div
        v-show="view === 'transcripts'"
        id="panel-transcripts"
        role="tabpanel"
        aria-labelledby="tab-transcripts"
        class="flex-1 pb-12"
      >
        <p class="max-w-[62ch] pt-2 text-[1.05rem] leading-relaxed lg:pt-6">
          Import a speaker-labelled (diarized) transcript of a visit, exported as JSON from your speech-to-text tool.
          PathCite reads the speakers and turns so you can check them. The file is read in memory and is not saved.
        </p>
        <div class="pt-4 lg:pt-10">
          <TranscriptDrop :manifest="transcriptManifest" />
        </div>
      </div>

      <footer class="border-t border-frost py-5 text-sm text-quiet">
        Slides from the NCT-CRC-HE-100K colorectal histology set (Kather et al., CC-BY-4.0).
      </footer>
    </div>
  </TooltipProvider>
</template>
