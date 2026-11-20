<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ChevronRight, Folder, FolderOpen } from 'lucide-vue-next'
import { CLASS_CODES, CLASS_NAMES, type ClassCode } from '@/lib/classes'
import { SLIDE_MIME, sampleUrl } from '@/lib/slide'
import type { Sample } from '@/types'

const props = defineProps<{ samples: Sample[]; activeFile: string | null }>()
const emit = defineEmits<{ pick: [sample: Sample] }>()

/** One folder per tissue class, in model order. The folder name is the slide's true label. */
const folders = computed(() =>
  CLASS_CODES.map((code) => ({ code, items: props.samples.filter((s) => s.code === code) })).filter(
    (folder) => folder.items.length > 0,
  ),
)

const open = reactive<Partial<Record<ClassCode, boolean>>>({ [CLASS_CODES[0]]: true })
const dragging = ref<string | null>(null)

function toggle(code: ClassCode) {
  open[code] = !open[code]
}

function onDragStart(event: DragEvent, sample: Sample) {
  if (!event.dataTransfer) return
  // The stage looks for this type; the payload is the slide's file path, which is unique.
  event.dataTransfer.setData(SLIDE_MIME, sample.file)
  event.dataTransfer.effectAllowed = 'copy'
  dragging.value = sample.file
}
</script>

<template>
  <section aria-labelledby="folders-title">
    <h2 id="folders-title" class="mb-3 text-sm font-semibold text-quiet">Slide folders</h2>
    <p class="mb-4 max-w-[28ch] text-sm text-quiet">
      Open a folder, then drag a slide onto the stage or press Enter on it.
    </p>

    <ul class="lg:max-h-[calc(100vh-17rem)] lg:overflow-y-auto lg:pr-2">
      <li v-for="folder in folders" :key="folder.code" class="border-b border-frost first:border-t">
        <button
          type="button"
          class="flex w-full items-center gap-2 py-2.5 text-left"
          :aria-expanded="!!open[folder.code]"
          :aria-controls="`folder-${folder.code}`"
          @click="toggle(folder.code)"
        >
          <ChevronRight
            class="size-4 shrink-0 text-quiet transition-transform duration-150"
            :class="open[folder.code] && 'rotate-90'"
            aria-hidden="true"
          />
          <component :is="open[folder.code] ? FolderOpen : Folder" class="size-4 shrink-0" aria-hidden="true" />
          <span class="cond font-bold tracking-wide">{{ folder.code }}</span>
          <span class="cond min-w-0 flex-1 truncate text-sm text-quiet">{{ CLASS_NAMES[folder.code] }}</span>
          <span class="tnum text-xs text-quiet">{{ folder.items.length }}</span>
        </button>

        <!-- The guide line marks the slides as belonging to the folder above -->
        <ul
          v-if="open[folder.code]"
          :id="`folder-${folder.code}`"
          class="mb-3 ml-[7px] grid grid-cols-4 gap-1.5 border-l border-frost pl-4 sm:grid-cols-6 lg:grid-cols-3"
        >
          <li v-for="(sample, index) in folder.items" :key="sample.file">
            <button
              type="button"
              draggable="true"
              :aria-label="`${CLASS_NAMES[folder.code]}, slide ${index + 1}`"
              :aria-pressed="activeFile === sample.file"
              class="block size-16 cursor-grab overflow-hidden rounded-glass bg-glass shadow-[0_0_0_1px_var(--color-frost)] active:cursor-grabbing aria-pressed:shadow-[0_0_0_2px_var(--color-ink)]"
              :class="{ 'opacity-40': dragging === sample.file }"
              @click="emit('pick', sample)"
              @dragstart="onDragStart($event, sample)"
              @dragend="dragging = null"
            >
              <img
                :src="sampleUrl(sample.file)"
                alt=""
                width="64"
                height="64"
                loading="lazy"
                draggable="false"
                class="size-full object-cover"
              />
            </button>
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>
