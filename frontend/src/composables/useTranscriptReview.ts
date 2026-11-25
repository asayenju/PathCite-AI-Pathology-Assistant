import { computed, getCurrentInstance, onBeforeUnmount, ref } from 'vue'
import { REVIEW_EXPORT_NOTICE, reviewFilename } from '@/lib/review'
import type { ClarifySource, ReviewComment, ReviewEvent, ReviewSource, ReviewVerdict, RoledTurn } from '@/types'

/** idle → running → done | stopped (stop()) | error. reset() goes back to idle from anywhere. */
export type ReviewStatus = 'idle' | 'running' | 'done' | 'stopped' | 'error'

export type CommentStatus = 'open' | 'resolved' | 'disagreed'

export interface ThreadMessage {
  id: string
  author: 'you' | 'agent'
  text: string
  /** True while the agent's answer is on its way (text is ''). */
  pending: boolean
}

/** A received comment plus what the person did with it. */
export type ReviewThread = ReviewComment & {
  status: CommentStatus
  disagreeReason: string | null
  thread: ThreadMessage[]
}

export interface ReviewExport {
  notice: string
  source: string
  sample_id: string | null
  exported_at: string
  comments: {
    id: string
    turn_id: string
    quote: string
    verdict: ReviewVerdict
    issue: string | null
    suggestion: string | null
    source: string | null
    status: CommentStatus
    disagree_reason: string | null
    thread: { author: 'you' | 'agent'; text: string }[]
  }[]
}

export interface UseTranscriptReviewOptions {
  /** Streams the review. Defaults to an error-only source, so callers pass e.g. mockReviewSource(sampleId). */
  source?: ReviewSource
  /** Answers replies. Without one, replies get the failure message. */
  clarify?: ClarifySource
  /** `source` field of the export. */
  sourceName?: string
}

export const REVIEW_ERROR_MESSAGE = 'The review stopped because of an error. Try again.'
export const REPLY_ERROR_MESSAGE = "Couldn't get an answer. Try again."
const NO_SOURCE_MESSAGE = "Agent review isn't available for this transcript yet."

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/**
 * Runs an agent review over roled turns and holds the comments and the person's responses to them.
 * Memory only: nothing goes to localStorage or sessionStorage, and nothing is sent anywhere except through the
 * injected sources. `source` can also be passed per run to start(), so the sample-specific mock can be chosen late.
 */
export function useTranscriptReview(options: UseTranscriptReviewOptions = {}) {
  const status = ref<ReviewStatus>('idle')
  const errorMessage = ref('')
  /** The turn being reviewed right now (between turn_started and turn_done). */
  const currentTurnId = ref<string | null>(null)
  /** Turns whose turn_done arrived, in arrival order. */
  const doneTurnIds = ref<string[]>([])
  /** In arrival order. Includes accurate comments (highlight only, no card) and orphans (start/end null). */
  const comments = ref<ReviewThread[]>([])
  const activeCommentId = ref<string | null>(null)
  /** sampleId passed to the last start(); the default for exportReview. */
  const sampleId = ref<string | null>(null)

  /** Turn id → position in the transcript given to start(); turns only seen in events go after, in arrival order. */
  let turnIndex = new Map<string, number>()
  let runController: AbortController | null = null
  const replyControllers = new Set<AbortController>()
  let messageSeq = 0

  const hasResults = computed(() => comments.value.length > 0)

  const counts = computed(() => {
    const c = { open: 0, resolved: 0, disagreed: 0 }
    for (const comment of comments.value) if (comment.verdict !== 'accurate') c[comment.status]++
    return c
  })

  /** Non-accurate comments in transcript order: by turn, orphans first within a turn, then by start offset. */
  const issueOrder = computed<string[]>(() => {
    const position = (turnId: string) => turnIndex.get(turnId) ?? Number.MAX_SAFE_INTEGER
    return comments.value
      .map((comment, arrival) => ({ comment, arrival }))
      .filter(({ comment }) => comment.verdict !== 'accurate')
      .sort(
        (a, b) =>
          position(a.comment.turn_id) - position(b.comment.turn_id) ||
          (a.comment.start ?? -1) - (b.comment.start ?? -1) ||
          a.arrival - b.arrival,
      )
      .map(({ comment }) => comment.id)
  })

  function find(id: string): ReviewThread | undefined {
    return comments.value.find((c) => c.id === id)
  }

  function abortReplies() {
    for (const c of replyControllers) c.abort()
    replyControllers.clear()
  }

  function clearResults() {
    errorMessage.value = ''
    currentTurnId.value = null
    doneTurnIds.value = []
    comments.value = []
    activeCommentId.value = null
  }

  function apply(event: ReviewEvent) {
    switch (event.type) {
      case 'turn_started':
        if (!turnIndex.has(event.turn_id)) turnIndex.set(event.turn_id, turnIndex.size)
        currentTurnId.value = event.turn_id
        break
      case 'comment':
        if (find(event.comment.id)) break
        comments.value.push({ ...event.comment, status: 'open', disagreeReason: null, thread: [] })
        break
      case 'turn_done':
        if (!doneTurnIds.value.includes(event.turn_id)) doneTurnIds.value.push(event.turn_id)
        if (currentTurnId.value === event.turn_id) currentTurnId.value = null
        break
      case 'done':
        currentTurnId.value = null
        status.value = 'done'
        break
      case 'error':
        currentTurnId.value = null
        errorMessage.value = event.message || REVIEW_ERROR_MESSAGE
        status.value = 'error'
        break
    }
  }

  /**
   * Reviews `turns`, replacing any previous run and its results (pending replies are aborted too).
   * Resolves when the run ends; never rejects.
   */
  async function start(
    turns: RoledTurn[],
    meta: { sampleId: string | null; source?: ReviewSource } = { sampleId: null },
  ): Promise<void> {
    runController?.abort()
    abortReplies()
    const controller = new AbortController()
    runController = controller
    const { signal } = controller
    clearResults()
    sampleId.value = meta.sampleId
    turnIndex = new Map(turns.map((t, i) => [t.id, i]))
    status.value = 'running'

    const source = meta.source ?? options.source
    if (!source) {
      apply({ type: 'error', message: NO_SOURCE_MESSAGE })
      return
    }
    try {
      for await (const event of source(turns, signal)) {
        if (signal.aborted) return
        apply(event)
        if (event.type === 'done' || event.type === 'error') break
      }
      if (signal.aborted) return
      if (status.value === 'running') apply({ type: 'done' }) // stream ended without done: treat as finished
    } catch (error) {
      if (signal.aborted || isAbort(error)) return
      currentTurnId.value = null
      errorMessage.value = REVIEW_ERROR_MESSAGE
      status.value = 'error'
    } finally {
      if (runController === controller) runController = null
    }
  }

  /** Stops a running review and keeps the comments received so far. */
  function stop() {
    if (status.value !== 'running') return
    runController?.abort()
    runController = null
    currentTurnId.value = null
    status.value = 'stopped'
  }

  /** Invalidation (Clear, a new import, Edit roles): aborts the run and every pending reply, then clears to idle. */
  function reset() {
    runController?.abort()
    runController = null
    abortReplies()
    clearResults()
    sampleId.value = null
    turnIndex = new Map()
    status.value = 'idle'
  }

  function resolve(id: string) {
    const c = find(id)
    if (!c) return
    c.status = 'resolved'
    c.disagreeReason = null
  }

  /** Back to open from resolved or disagreed; drops the disagree reason. */
  function reopen(id: string) {
    const c = find(id)
    if (!c) return
    c.status = 'open'
    c.disagreeReason = null
  }

  /** Marks the comment wrong. The reason is trimmed; blank becomes null. */
  function disagree(id: string, reason?: string | null) {
    const c = find(id)
    if (!c) return
    c.status = 'disagreed'
    c.disagreeReason = reason?.trim() || null
  }

  function setActive(id: string | null) {
    activeCommentId.value = id === null || find(id) ? id : null
  }

  /**
   * Asks the agent about a comment: appends your question and a pending agent message, then fills it with the
   * answer (or REPLY_ERROR_MESSAGE). Blank questions are ignored. Works while the review is running; reset() and
   * start() abort it and the pending message goes with the cleared comments.
   */
  async function reply(id: string, question: string): Promise<void> {
    const text = question.trim()
    const c = find(id)
    if (!text || !c) return
    c.thread.push({ id: `m${++messageSeq}`, author: 'you', text, pending: false })
    const pendingId = `m${++messageSeq}`
    c.thread.push({ id: pendingId, author: 'agent', text: '', pending: true })

    const controller = new AbortController()
    replyControllers.add(controller)
    const { signal } = controller
    // Looked up again on settle: start()/reset() may have replaced the comments in the meantime.
    const settle = (answer: string) => {
      const message = find(id)?.thread.find((m) => m.id === pendingId)
      if (!message) return
      message.text = answer
      message.pending = false
    }
    const comment: ReviewComment = {
      id: c.id,
      turn_id: c.turn_id,
      quote: c.quote,
      start: c.start,
      end: c.end,
      verdict: c.verdict,
      issue: c.issue,
      suggestion: c.suggestion,
      source: c.source,
    }
    try {
      if (!options.clarify) throw new Error('no clarify source')
      const answer = await options.clarify(comment, text, signal)
      if (signal.aborted) return
      settle(answer)
    } catch (error) {
      if (signal.aborted || isAbort(error)) return
      settle(REPLY_ERROR_MESSAGE)
    } finally {
      replyControllers.delete(controller)
    }
  }

  function step(direction: 1 | -1): string | null {
    const order = issueOrder.value
    const open = new Set(comments.value.filter((c) => c.status === 'open').map((c) => c.id))
    if (open.size === 0) return null
    const n = order.length
    const at = activeCommentId.value ? order.indexOf(activeCommentId.value) : -1
    // With nothing (or a non-issue) active, next starts from the first issue and prev from the last.
    const from = at >= 0 ? at : direction === 1 ? -1 : n
    for (let k = 1; k <= n; k++) {
      const id = order[(((from + direction * k) % n) + n) % n]!
      if (open.has(id)) {
        activeCommentId.value = id
        return id
      }
    }
    return null
  }

  /** Activates the next open issue in transcript order, wrapping around. Returns its id, or null if none is open. */
  const nextIssue = () => step(1)
  /** Activates the previous open issue in transcript order, wrapping around. */
  const prevIssue = () => step(-1)

  /**
   * The review as a JSON-ready object plus its filename. Holds quotes but no other transcript text, and leaves out
   * pending agent messages. Save it with downloadJson from lib/review.
   */
  function exportReview(meta: { sampleId?: string | null; now?: Date } = {}): {
    filename: string
    data: ReviewExport
  } {
    const now = meta.now ?? new Date()
    const id = meta.sampleId !== undefined ? meta.sampleId : sampleId.value
    return {
      filename: reviewFilename(id, now),
      data: {
        notice: REVIEW_EXPORT_NOTICE,
        source: options.sourceName ?? 'mock',
        sample_id: id,
        exported_at: now.toISOString(),
        comments: comments.value.map((c) => ({
          id: c.id,
          turn_id: c.turn_id,
          quote: c.quote,
          verdict: c.verdict,
          issue: c.issue,
          suggestion: c.suggestion,
          source: c.source,
          status: c.status,
          disagree_reason: c.disagreeReason,
          thread: c.thread.filter((m) => !m.pending).map((m) => ({ author: m.author, text: m.text })),
        })),
      },
    }
  }

  if (getCurrentInstance())
    onBeforeUnmount(() => {
      runController?.abort()
      abortReplies()
    })

  return {
    status,
    errorMessage,
    currentTurnId,
    doneTurnIds,
    comments,
    activeCommentId,
    sampleId,
    hasResults,
    counts,
    issueOrder,
    start,
    stop,
    reset,
    resolve,
    reopen,
    disagree,
    setActive,
    reply,
    nextIssue,
    prevIssue,
    exportReview,
  }
}
