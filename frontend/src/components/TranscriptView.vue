<script setup lang="ts">
import { computed } from 'vue'
import { Check, Plus, X } from 'lucide-vue-next'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { ReviewThread } from '@/composables/useTranscriptReview'
import { splitSegments, type Segment, type SegmentRange, type SplitResult } from '@/lib/segments'
import { formatClock, roleLabel } from '@/lib/transcripts'
import type { ReviewVerdict, Role, TranscriptTurn } from '@/types'

/**
 * The turn-by-turn transcript, marked by role, with doubtful words underlined. With review comments it also
 * highlights each comment's span. DOM contract for the review margin: the first run of a comment's span carries
 * `data-anchor-for="<id>"`, every highlight carries `data-comment-ids`, and highlights with an issue point at
 * their card with `aria-describedby="comment-<id>"`.
 */
const props = withDefaults(
  defineProps<{
    turns: TranscriptTurn[]
    roles: Record<string, Role | null>
    comments?: ReviewThread[]
    activeCommentId?: string | null
    currentTurnId?: string | null
    doneTurnIds?: string[]
    /** A review has been started: doctor turns not reviewed yet are dimmed. */
    reviewing?: boolean
    /** True keeps the list in its own scroll box; false lets the parent scroll (the review margin needs that). */
    scroll?: boolean
  }>(),
  {
    comments: () => [],
    activeCommentId: null,
    currentTurnId: null,
    doneTurnIds: () => [],
    reviewing: false,
    scroll: true,
  },
)

const emit = defineEmits<{ activate: [commentId: string] }>()

/** Border and badge per role. The role is always written out too, colour is only a second cue. */
const ROLE_STYLES: Record<Role | 'none', { border: string; badge: string }> = {
  doctor: { border: 'border-l-ink', badge: 'bg-ink text-glass' },
  patient: { border: 'border-l-eosin', badge: 'border border-eosin bg-eosin/10 text-ink' },
  other: { border: 'border-l-amber', badge: 'border border-amber bg-amber/10 text-ink' },
  none: { border: 'border-l-frost border-dashed', badge: 'border border-dashed border-quiet text-quiet' },
}

/** Highlight per verdict: open, stacked (two or more comments overlap), faded (resolved or disagreed). */
const MARK_STYLES: Record<ReviewVerdict, { open: string; stacked: string; faded: string }> = {
  inaccurate: {
    open: 'border-b-2 border-eosin bg-eosin/15',
    stacked: 'border-b-2 border-eosin bg-eosin/30',
    faded: 'border-b-2 border-eosin/30 bg-eosin/5',
  },
  incomplete: {
    open: 'border-b-2 border-amber bg-amber/15',
    stacked: 'border-b-2 border-amber bg-amber/30',
    faded: 'border-b-2 border-amber/30 bg-amber/5',
  },
  accurate: {
    open: 'border-b border-moss/50 bg-transparent',
    stacked: 'border-b border-moss/50 bg-moss/10',
    faded: 'border-b border-moss/25 bg-transparent',
  },
}

const VERDICT_ICONS = { inaccurate: X, incomplete: Plus, accurate: Check } as const
const VERDICT_ICON_COLOURS: Record<ReviewVerdict, string> = {
  inaccurate: 'text-eosin',
  incomplete: 'text-amber',
  accurate: 'text-moss',
}

const styleFor = (role: Role | null | undefined) => ROLE_STYLES[role ?? 'none']
const percent = (confidence: number) => `${Math.round(confidence * 100)}%`

const commentById = computed(() => new Map(props.comments.map((c) => [c.id, c])))

/** Orphans (start null) get no highlight. */
const segmentsByTurn = computed(() => {
  const ranges = new Map<string, SegmentRange[]>()
  for (const c of props.comments) {
    if (c.start === null || c.end === null) continue
    const list = ranges.get(c.turn_id) ?? []
    list.push({ id: c.id, start: c.start, end: c.end, verdict: c.verdict })
    ranges.set(c.turn_id, list)
  }
  return new Map<string, SplitResult>(
    props.turns.map((t) => [t.id, splitSegments(t.text, t.words, ranges.get(t.id) ?? [])]),
  )
})

const runsFor = (turn: TranscriptTurn): Segment[] => segmentsByTurn.value.get(turn.id)?.runs ?? []

const commentsOf = (run: Segment) =>
  run.commentIds.map((id) => commentById.value.get(id)).filter((c): c is ReviewThread => c !== undefined)

/** The shortest covering comment with an issue: it wins clicks and is what the highlight describes. */
const targetOf = (run: Segment) => commentsOf(run).find((c) => c.verdict !== 'accurate') ?? null

/** Comments whose span starts at this run, i.e. the runs that anchor a card. */
const startingAt = (run: Segment) =>
  commentsOf(run).filter((c) => Math.max(c.start ?? 0, 0) === run.start)

const endingAt = (turn: TranscriptTurn, run: Segment) =>
  commentsOf(run).filter((c) => Math.min(c.end ?? 0, turn.text.length) === run.end)

function markAttrs(run: Segment): Record<string, string | number> {
  const attrs: Record<string, string | number> = { 'data-comment-ids': run.commentIds.join(' ') }
  const first = startingAt(run)[0]
  if (first) attrs['data-anchor-for'] = first.id
  const target = targetOf(run)
  if (target) {
    attrs['aria-describedby'] = `comment-${target.id}`
    attrs.tabindex = 0
  }
  return attrs
}

function markClass(run: Segment): string[] {
  const primary = targetOf(run) ?? commentsOf(run)[0]
  if (!primary) return []
  const styles = MARK_STYLES[primary.verdict]
  const tint = primary.status !== 'open' ? styles.faded : run.commentIds.length > 1 ? styles.stacked : styles.open
  const classes = ['rounded-[2px] text-inherit transition-colors', tint]
  if (targetOf(run)) classes.push('cursor-pointer')
  if (props.activeCommentId && run.commentIds.includes(props.activeCommentId)) {
    classes.push('ring-2 ring-ink ring-offset-1 ring-offset-glass')
  }
  return classes
}

function activate(run: Segment) {
  const target = targetOf(run)
  if (target) emit('activate', target.id)
}

function onKeydown(event: KeyboardEvent, run: Segment) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  if (!targetOf(run)) return
  event.preventDefault()
  activate(run)
}

const isDoctor = (turn: TranscriptTurn) => props.roles[turn.speaker] === 'doctor'
const isCurrent = (turn: TranscriptTurn) => props.reviewing && props.currentTurnId === turn.id
const isDimmed = (turn: TranscriptTurn) =>
  props.reviewing && isDoctor(turn) && !isCurrent(turn) && !props.doneTurnIds.includes(turn.id)

const liveMessage = computed(() => {
  if (!props.reviewing || !props.currentTurnId) return ''
  const index = props.turns.findIndex((t) => t.id === props.currentTurnId)
  const turn = props.turns[index]
  if (!turn) return ''
  return turn.start !== null ? `Reviewing turn at ${formatClock(turn.start)}` : `Reviewing turn ${index + 1}`
})
</script>

<template>
  <section aria-labelledby="transcript-view-title" class="space-y-3" data-testid="transcript-view">
    <h3 id="transcript-view-title" class="text-lg font-semibold">Conversation</h3>
    <p class="text-sm text-quiet">
      Dotted underlines mark words the speech-to-text was unsure of. Hover or focus one for its score.
    </p>
    <p class="sr-only" aria-live="polite" data-testid="review-live">{{ liveMessage }}</p>

    <TooltipProvider :delay-duration="150">
      <ol
        class="space-y-2"
        :class="{ 'max-h-[32rem] overflow-y-auto pr-1': scroll }"
        :tabindex="scroll ? 0 : undefined"
        aria-label="Transcript turns"
        data-testid="transcript-turns"
      >
        <li
          v-for="turn in turns"
          :key="turn.id"
          class="rounded-glass border-l-4 bg-glass px-4 py-2 transition-opacity"
          :class="[styleFor(roles[turn.speaker]).border, { 'opacity-50': isDimmed(turn) }]"
          :data-testid="`turn-${turn.id}`"
          :data-dimmed="isDimmed(turn) ? '' : undefined"
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
            <span
              v-if="isCurrent(turn)"
              class="rounded-full border border-ink/30 bg-ink/5 px-2 py-0.5 text-xs font-medium motion-safe:animate-pulse"
              data-testid="reviewing-pill"
            >
              Reviewing…
            </span>
          </p>

          <p class="leading-relaxed">
            <template v-for="run in runsFor(turn)" :key="run.start">
              <component
                :is="run.commentIds.length ? 'mark' : 'span'"
                v-bind="run.commentIds.length ? markAttrs(run) : {}"
                :class="markClass(run)"
                @click="activate(run)"
                @keydown="onKeydown($event, run)"
              >
                <span
                  v-for="extra in startingAt(run).slice(1)"
                  :key="extra.id"
                  :data-anchor-for="extra.id"
                  aria-hidden="true"
                ></span>
                <Tooltip v-if="run.lowConfidence !== null">
                  <TooltipTrigger as-child>
                    <span
                      tabindex="0"
                      class="underline decoration-dotted decoration-2 underline-offset-4"
                      data-testid="low-confidence-word"
                      >{{ run.text
                      }}<span v-if="run.wordBoundaryAfter" class="sr-only">
                        (transcription confidence {{ percent(run.lowConfidence) }})</span
                      ></span
                    >
                  </TooltipTrigger>
                  <TooltipContent>Transcription confidence {{ percent(run.lowConfidence) }}</TooltipContent>
                </Tooltip>
                <template v-else>{{ run.text }}</template>
                <template v-for="c in endingAt(turn, run)" :key="`end-${c.id}`">
                  <component
                    :is="VERDICT_ICONS[c.verdict]"
                    aria-hidden="true"
                    class="ml-0.5 inline size-3 align-baseline"
                    :class="VERDICT_ICON_COLOURS[c.verdict]"
                    :stroke-width="3"
                    data-testid="verdict-icon"
                  />
                  <span class="sr-only"> ({{ c.verdict }} statement)</span>
                </template>
              </component>
            </template>
          </p>
        </li>
      </ol>
    </TooltipProvider>
  </section>
</template>
