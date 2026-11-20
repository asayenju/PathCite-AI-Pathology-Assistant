<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Reading from '@/components/Reading.vue'
import SlideRack from '@/components/SlideRack.vue'
import Stage from '@/components/Stage.vue'
import { TooltipProvider } from '@/components/ui/tooltip'
import { usePredict } from '@/composables/usePredict'
import { sampleUrl } from '@/lib/slide'
import type { Sample } from '@/types'

const samples = ref<Sample[]>([])
const { status, previewUrl, sample, result, errorMessage, readFile, readSample, reset } = usePredict()

onMounted(async () => {
  try {
    const response = await fetch(sampleUrl('samples/samples.json'))
    samples.value = (await response.json()).samples
  } catch {
    samples.value = []
  }
})

const slideLabel = computed(() => sample.value?.code ?? null)

function onSlideDropped(code: string) {
  const dropped = samples.value.find((s) => s.code === code)
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

      <main
        class="grid flex-1 gap-10 pb-12 pt-4 lg:grid-cols-[13rem_minmax(0,28rem)_minmax(0,1fr)] lg:gap-14 lg:pt-10"
      >
        <SlideRack :samples="samples" :active-code="slideLabel" @pick="readSample" />

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

      <footer class="border-t border-frost py-5 text-sm text-quiet">
        Slides from the NCT-CRC-HE-100K colorectal histology set (Kather et al., CC-BY-4.0).
      </footer>
    </div>
  </TooltipProvider>
</template>
