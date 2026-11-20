<script setup lang="ts">
import { ref } from 'vue'
import { ACCEPTED_TYPES } from '@/lib/api'
import { SLIDE_MIME } from '@/lib/slide'
import type { Status } from '@/composables/usePredict'

defineProps<{
  status: Status
  previewUrl: string | null
  /** Set for bundled slides only: their pixel size is known, so a true scale bar can be drawn. */
  scaleKnown: boolean
}>()

const emit = defineEmits<{ slide: [code: string]; file: [file: File]; clear: [] }>()

/** NCT-CRC patches are 224 px wide at 0.5 um/px, so 50 px is 25 um. */
const SCALE_BAR_UM = 25
const SCALE_BAR_PERCENT = (SCALE_BAR_UM / 0.5 / 224) * 100

const over = ref(false)
const fullPatch = ref(false)
const picker = ref<HTMLInputElement | null>(null)

function onDrop(event: DragEvent) {
  over.value = false
  const code = event.dataTransfer?.getData(SLIDE_MIME)
  if (code) return emit('slide', code)
  const file = event.dataTransfer?.files?.[0]
  if (file) emit('file', file)
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('file', file)
  input.value = '' // allow choosing the same file again
}
</script>

<template>
  <section aria-labelledby="stage-title" class="mx-auto w-full max-w-[460px]">
    <h2 id="stage-title" class="sr-only">Stage</h2>

    <div class="relative p-3">
      <!-- Compass ticks around the field, like the graticule of an eyepiece -->
      <svg
        class="pointer-events-none absolute inset-0 size-full text-ink"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d="M50 0v2.4M50 97.6V100M0 50h2.4M97.6 50H100" stroke="currentColor" stroke-width="0.5" fill="none" />
      </svg>

      <button
        type="button"
        class="relative block aspect-square w-full cursor-pointer overflow-hidden bg-glass transition-[border-radius,box-shadow] duration-300 [&>*]:pointer-events-none"
        :class="[
          fullPatch ? 'rounded-glass' : 'rounded-full',
          over ? 'shadow-[0_0_0_6px_var(--color-ink)]' : 'shadow-[0_0_0_2px_var(--color-ink)]',
          status === 'loading' && 'ring-pulse',
        ]"
        aria-label="Stage. Drop a slide or an image here, or press Enter to choose a file."
        @click="picker?.click()"
        @dragenter.prevent="over = true"
        @dragover.prevent="over = true"
        @dragleave="over = false"
        @drop.prevent="onDrop"
      >
        <img
          v-if="previewUrl"
          :key="previewUrl"
          :src="previewUrl"
          alt="The patch under the microscope"
          class="focus-pull size-full object-cover"
        />

        <template v-else>
          <svg class="absolute inset-0 size-full text-ink/25" viewBox="0 0 100 100" aria-hidden="true">
            <path d="M50 8v84M8 50h84" stroke="currentColor" stroke-width="0.25" fill="none" />
          </svg>
          <span class="absolute inset-0 grid place-content-center gap-1 text-center">
            <span class="cond text-3xl font-semibold tracking-tight">Drop a slide here</span>
            <span class="text-sm text-quiet">or choose an image file</span>
          </span>
        </template>

        <span
          v-if="previewUrl && scaleKnown"
          class="absolute bottom-[10%] left-1/2 flex -translate-x-1/2 flex-col items-center gap-1 text-glass"
          :style="{ width: `${SCALE_BAR_PERCENT}%` }"
        >
          <span class="tnum text-xs font-medium [text-shadow:0_1px_3px_rgb(0_0_0/0.7)]">{{ SCALE_BAR_UM }} µm</span>
          <span class="h-0.5 w-full bg-glass shadow-[0_1px_3px_rgb(0_0_0/0.6)]" />
        </span>
      </button>

      <input ref="picker" type="file" :accept="ACCEPTED_TYPES.join(',')" class="hidden" @change="onPick" />
    </div>

    <div class="mt-2 flex min-h-8 items-center justify-center gap-5 text-sm">
      <template v-if="previewUrl">
        <button type="button" class="underline underline-offset-4 hover:text-quiet" @click="fullPatch = !fullPatch">
          {{ fullPatch ? 'Show circular field' : 'Show full patch' }}
        </button>
        <button type="button" class="underline underline-offset-4 hover:text-quiet" @click="emit('clear')">
          Clear stage
        </button>
      </template>
    </div>
  </section>
</template>
