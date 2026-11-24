<script setup lang="ts">
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { formatClock, isLowConfidence, roleLabel } from '@/lib/transcripts'
import type { Role, TranscriptTurn } from '@/types'

/** The turn-by-turn transcript, marked by role, with doubtful words underlined. */
defineProps<{ turns: TranscriptTurn[]; roles: Record<string, Role | null> }>()

/** Border and badge per role. The role is always written out too, colour is only a second cue. */
const ROLE_STYLES: Record<Role | 'none', { border: string; badge: string }> = {
  doctor: { border: 'border-l-ink', badge: 'bg-ink text-glass' },
  patient: { border: 'border-l-eosin', badge: 'border border-eosin bg-eosin/10 text-ink' },
  other: { border: 'border-l-amber', badge: 'border border-amber bg-amber/10 text-ink' },
  none: { border: 'border-l-frost border-dashed', badge: 'border border-dashed border-quiet text-quiet' },
}

const styleFor = (role: Role | null | undefined) => ROLE_STYLES[role ?? 'none']
const percent = (confidence: number) => `${Math.round(confidence * 100)}%`
</script>

<template>
  <section aria-labelledby="transcript-view-title" class="space-y-3" data-testid="transcript-view">
    <h3 id="transcript-view-title" class="text-lg font-semibold">Conversation</h3>
    <p class="text-sm text-quiet">
      Dotted underlines mark words the speech-to-text was unsure of. Hover or focus one for its score.
    </p>

    <TooltipProvider :delay-duration="150">
      <ol class="max-h-[32rem] space-y-2 overflow-y-auto pr-1" tabindex="0" aria-label="Transcript turns">
        <li
          v-for="turn in turns"
          :key="turn.id"
          class="rounded-glass border-l-4 bg-glass px-4 py-2"
          :class="styleFor(roles[turn.speaker]).border"
          :data-testid="`turn-${turn.id}`"
        >
          <p class="mb-1 flex flex-wrap items-center gap-2 text-sm">
            <span class="tnum text-quiet" data-testid="turn-time">{{ formatClock(turn.start) }}</span>
            <span class="font-semibold">{{ turn.speaker }}</span>
            <span
              class="rounded-full px-2 py-0.5 text-xs font-medium"
              :class="styleFor(roles[turn.speaker]).badge"
              data-testid="turn-role"
            >
              {{ roleLabel(roles[turn.speaker] ?? null) }}
            </span>
          </p>

          <p v-if="!turn.words.length" class="leading-relaxed">{{ turn.text }}</p>
          <p v-else class="leading-relaxed">
            <template v-for="(word, i) in turn.words" :key="i">
              <Tooltip v-if="word.confidence !== null && isLowConfidence(word.confidence)">
                <TooltipTrigger as-child>
                  <span
                    tabindex="0"
                    class="underline decoration-dotted decoration-2 underline-offset-4"
                    data-testid="low-confidence-word"
                    >{{ word.text
                    }}<span class="sr-only"> (transcription confidence {{ percent(word.confidence) }})</span></span
                  >
                </TooltipTrigger>
                <TooltipContent>Transcription confidence {{ percent(word.confidence) }}</TooltipContent>
              </Tooltip>
              <span v-else>{{ word.text }}</span>{{ i < turn.words.length - 1 ? ' ' : '' }}
            </template>
          </p>
        </li>
      </ol>
    </TooltipProvider>
  </section>
</template>
