<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ChevronRight, FileText, Folder, FolderOpen } from 'lucide-vue-next'
import { TRANSCRIPT_MIME, vendorName } from '@/lib/transcriptSamples'
import type { TranscriptManifest, TranscriptSample } from '@/types'

const props = defineProps<{ manifest: TranscriptManifest; activeFile: string | null }>()
const emit = defineEmits<{ pick: [sample: TranscriptSample] }>()

const open = reactive<Record<string, boolean>>({ [props.manifest.folders[0]?.slug ?? '']: true })
const dragging = ref<string | null>(null)

function toggle(slug: string) {
  open[slug] = !open[slug]
}

/** Enter and Space select explicitly (and suppress the native click, so a key press picks once). */
function onKey(event: KeyboardEvent, sample: TranscriptSample) {
  event.preventDefault()
  emit('pick', sample)
}

function onDragStart(event: DragEvent, sample: TranscriptSample) {
  if (!event.dataTransfer) return
  // The drop zone looks for this type; the payload is the sample's file path, which is unique.
  event.dataTransfer.setData(TRANSCRIPT_MIME, sample.file)
  event.dataTransfer.effectAllowed = 'copy'
  dragging.value = sample.file
}
</script>

<template>
  <section aria-labelledby="transcript-folders-title" data-testid="transcript-folders">
    <h2 id="transcript-folders-title" class="mb-3 text-sm font-semibold text-quiet">Sample transcripts</h2>
    <p class="mb-2 max-w-[28ch] text-sm text-quiet">
      Open a folder, then drag a sample onto the drop zone or press Enter on it.
    </p>
    <p data-testid="samples-notice" class="mb-4 max-w-[28ch] text-xs leading-snug text-quiet">
      {{ manifest.notice }}
    </p>

    <ul class="lg:max-h-[calc(100vh-17rem)] lg:overflow-y-auto lg:pr-2">
      <li v-for="folder in manifest.folders" :key="folder.slug" class="border-b border-frost first:border-t">
        <button
          type="button"
          class="flex w-full items-center gap-2 py-2.5 text-left"
          :aria-expanded="!!open[folder.slug]"
          :aria-controls="`tfolder-${folder.slug}`"
          @click="toggle(folder.slug)"
        >
          <ChevronRight
            class="size-4 shrink-0 text-quiet transition-transform duration-150"
            :class="open[folder.slug] && 'rotate-90'"
            aria-hidden="true"
          />
          <component :is="open[folder.slug] ? FolderOpen : Folder" class="size-4 shrink-0" aria-hidden="true" />
          <span class="cond min-w-0 flex-1 truncate font-bold tracking-wide">{{ folder.name }}</span>
          <span class="tnum text-xs text-quiet">{{ folder.items.length }}</span>
        </button>

        <ul
          v-if="open[folder.slug]"
          :id="`tfolder-${folder.slug}`"
          class="mb-3 ml-[7px] space-y-1.5 border-l border-frost pl-4"
        >
          <li v-for="sample in folder.items" :key="sample.file">
            <button
              type="button"
              draggable="true"
              :data-file="sample.file"
              :aria-label="`${sample.title}. Synthetic sample, ${vendorName(sample.vendor)} format.`"
              :aria-pressed="activeFile === sample.file"
              class="flex w-full cursor-grab items-start gap-2 rounded-glass bg-glass px-2.5 py-2 text-left shadow-[0_0_0_1px_var(--color-frost)] active:cursor-grabbing aria-pressed:shadow-[0_0_0_2px_var(--color-ink)]"
              :class="{ 'opacity-40': dragging === sample.file }"
              @click="emit('pick', sample)"
              @keydown.enter="onKey($event, sample)"
              @keydown.space="onKey($event, sample)"
              @dragstart="onDragStart($event, sample)"
              @dragend="dragging = null"
            >
              <FileText class="mt-0.5 size-4 shrink-0 text-quiet" aria-hidden="true" />
              <span class="min-w-0 flex-1">
                <span class="block text-sm leading-snug">{{ sample.title }}</span>
                <span class="mt-1 flex flex-wrap items-center gap-1.5" aria-hidden="true">
                  <span
                    data-testid="vendor-badge"
                    class="rounded-full border border-frost px-1.5 py-px text-[0.7rem] font-medium text-quiet"
                  >
                    {{ vendorName(sample.vendor) }}
                  </span>
                  <span class="text-[0.7rem] text-quiet">Synthetic sample</span>
                </span>
              </span>
            </button>
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>
