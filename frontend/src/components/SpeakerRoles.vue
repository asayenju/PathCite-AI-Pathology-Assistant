<script setup lang="ts">
import { Button } from '@/components/ui/button'
import { ROLES, ROLE_LABELS, formatClock } from '@/lib/transcripts'
import type { Role, TranscriptSpeaker } from '@/types'

/** One row per speaker: who they are, how much they said, and the role the reviewer gives them. */
defineProps<{
  speakers: TranscriptSpeaker[]
  roles: Record<string, Role | null>
  confirmed: boolean
  canConfirm: boolean
}>()

const emit = defineEmits<{
  setRole: [speakerId: string, role: Role | null]
  confirm: []
  edit: []
}>()

function onChange(speakerId: string, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  emit('setRole', speakerId, value === '' ? null : (value as Role))
}
</script>

<template>
  <section aria-labelledby="speaker-roles-title" class="space-y-4" data-testid="speaker-roles">
    <div>
      <h3 id="speaker-roles-title" class="text-lg font-semibold">Speaker roles</h3>
      <p class="text-sm text-quiet">
        Check who is who. Suggestions come from what each speaker said and can be wrong.
      </p>
    </div>

    <ul class="divide-y divide-frost rounded-glass border border-frost bg-glass">
      <li
        v-for="speaker in speakers"
        :key="speaker.id"
        class="grid gap-3 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-start"
        :data-testid="`speaker-${speaker.id}`"
      >
        <div class="min-w-0 space-y-1">
          <p class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span class="font-semibold">{{ speaker.id }}</span>
            <span class="text-sm text-quiet">{{ speaker.source_label }}</span>
            <span class="tnum text-sm">
              {{ formatClock(speaker.talk_time_s) }} talk time · {{ speaker.word_count }} words
            </span>
          </p>
          <ul v-if="speaker.preview.length" class="space-y-0.5 text-sm text-quiet">
            <li v-for="(line, i) in speaker.preview" :key="i" class="truncate">“{{ line }}”</li>
          </ul>
        </div>

        <div class="flex items-center gap-2">
          <label :for="`role-${speaker.id}`" class="sr-only">Role for {{ speaker.id }}</label>
          <select
            :id="`role-${speaker.id}`"
            class="h-8 rounded-md border border-frost bg-bench px-2 text-sm disabled:opacity-60"
            :value="roles[speaker.id] ?? ''"
            :disabled="confirmed"
            @change="onChange(speaker.id, $event)"
          >
            <option v-for="role in ROLES" :key="role" :value="role">{{ ROLE_LABELS[role] }}</option>
            <option value="">Unassigned</option>
          </select>
          <span
            v-if="speaker.suggested_role && roles[speaker.id] === speaker.suggested_role"
            class="rounded-full border border-frost px-2 py-0.5 text-xs text-quiet"
            data-testid="suggested-badge"
          >
            suggested
          </span>
        </div>
      </li>
    </ul>

    <div v-if="confirmed" class="flex flex-wrap items-center gap-3">
      <p role="status" class="text-sm font-semibold">Roles confirmed.</p>
      <Button variant="outline" size="sm" @click="emit('edit')">Edit roles</Button>
    </div>
    <div v-else class="flex flex-wrap items-center gap-3">
      <Button size="sm" :disabled="!canConfirm" aria-describedby="confirm-help" @click="emit('confirm')">
        Confirm roles
      </Button>
      <p v-if="!canConfirm" id="confirm-help" class="text-sm text-quiet">
        Mark at least one speaker as the doctor.
      </p>
    </div>
  </section>
</template>
