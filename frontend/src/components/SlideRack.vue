<script setup lang="ts">
import { ref } from 'vue'
import { CLASS_NAMES } from '@/lib/classes'
import { SLIDE_MIME, sampleUrl } from '@/lib/slide'
import type { Sample } from '@/types'

defineProps<{ samples: Sample[]; activeCode: string | null }>()
const emit = defineEmits<{ pick: [sample: Sample] }>()

const dragging = ref<string | null>(null)

function onDragStart(event: DragEvent, sample: Sample) {
  if (!event.dataTransfer) return
  event.dataTransfer.setData(SLIDE_MIME, sample.code)
  event.dataTransfer.effectAllowed = 'copy'
  dragging.value = sample.code
}
</script>

<template>
  <section aria-labelledby="rack-title">
    <h2 id="rack-title" class="mb-3 text-sm font-semibold text-quiet">Slides</h2>
    <p class="mb-4 max-w-[28ch] text-sm text-quiet">Drag a slide onto the stage, or press Enter on one.</p>

    <ul
      class="-mx-4 flex snap-x gap-2 overflow-x-auto px-4 pb-3 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0"
    >
      <li v-for="sample in samples" :key="sample.code" class="snap-start">
        <button
          type="button"
          draggable="true"
          :aria-label="`${CLASS_NAMES[sample.code]} slide, ${sample.code}`"
          :aria-pressed="activeCode === sample.code"
          class="group flex h-16 w-48 cursor-grab touch-pan-x overflow-hidden rounded-glass bg-glass text-left shadow-[0_0_0_1px_var(--color-frost)] transition-transform duration-150 hover:translate-x-1 active:cursor-grabbing aria-pressed:translate-x-2 aria-pressed:shadow-[0_0_0_2px_var(--color-ink)]"
          :class="{ 'opacity-40': dragging === sample.code }"
          @click="emit('pick', sample)"
          @dragstart="onDragStart($event, sample)"
          @dragend="dragging = null"
        >
          <img
            :src="sampleUrl(sample.file)"
            alt=""
            width="64"
            height="64"
            draggable="false"
            class="size-16 shrink-0 object-cover"
          />
          <span class="flex min-w-0 flex-1 flex-col justify-center bg-frost px-2.5">
            <span class="cond text-base leading-none font-bold tracking-wide">{{ sample.code }}</span>
            <span class="cond mt-1 truncate text-[0.7rem] leading-tight text-quiet">
              {{ CLASS_NAMES[sample.code] }}
            </span>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>
