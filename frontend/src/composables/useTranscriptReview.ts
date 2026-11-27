import { computed, getCurrentInstance, onBeforeUnmount, ref } from 'vue'
import { PredictError } from '@/lib/api'
import { defaultClaimChecker } from '@/lib/claimChecker'
import { defaultSpanTriager } from '@/lib/jevReviewSource'
import { reviewExportNotice, reviewFilename } from '@/lib/review'
import type {
  CheckedResult,
  CheckResult,
  ClaimChecker,
  ClarifySource,
  ReviewComment,
  ReviewEvent,
  ReviewSource,
  ReviewVerdict,
  RoledTurn,
  SpanTriager,
  TriageKind,
} from '@/types'

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

/**
 * A claim's check: `checking` while the ClaimChecker runs, then the checker's status (`checked`, with the result in
 * `check`; `not_checked` from the test stub), or `error` if it failed. A check aborted by stop() ends as
 * `not_checked`.
 */
export type CheckStatus = 'checking' | CheckResult['status'] | 'error'

/** A checked result in the export: the CheckedResult without its `status` (that is `check_status`). */
export type ExportedCheck = Omit<CheckedResult, 'status'>

/**
 * A received comment plus what the person did with it. The triage fields (confirmed … checkError) are null/false on
 * fact-check comments. A transcription card answered "Yes" on a claim-based span, or "No" with a correction that
 * Jev calls a claim, switches its `verdict` to `claim`; `triage.kind` keeps what Jev first said.
 */
export type ReviewThread = ReviewComment & {
  status: CommentStatus
  disagreeReason: string | null
  thread: ThreadMessage[]
  /** Transcription cards: true after "Yes, it's correct", false after a "No" correction went through, else null. */
  confirmed: boolean | null
  /** The doctor's typed correction (trimmed), once triaged. The transcript text itself is never changed. */
  correction: string | null
  /** The text the claim is checked as when it differs from the quote (a correction triaged as a claim). */
  checkedAs: string | null
  /** True while a correction is being triaged; the card can't be answered again meanwhile. */
  correctionPending: boolean
  /** Why the last correction failed (the card stays answerable); null otherwise. */
  correctionError: string | null
  /** Null for anything that is not a claim. */
  checkStatus: CheckStatus | null
  /** Message for checkStatus `error`. */
  checkError: string | null
  /**
   * The checker's answer when checkStatus is `checked`, else null. The comment's own `verdict` stays `claim`: the
   * check verdict (accurate … disputed) is what the card shows.
   */
  check: CheckedResult | null
}

/** The triage state of a comment nobody has answered: what every received comment starts with. */
export const EMPTY_TRIAGE_STATE = {
  confirmed: null,
  correction: null,
  checkedAs: null,
  correctionPending: false,
  correctionError: null,
  checkStatus: null,
  checkError: null,
  check: null,
} as const satisfies Partial<ReviewThread>

export interface ReviewExport {
  notice: string
  source: string
  sample_id: string | null
  exported_at: string
  /** The triage model id from the stream's done event (e.g. jev-1.14.0); null for the mock. */
  model: string | null
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
    /** Jev's original kind, p_claim and p_asr; null for fact-check comments. */
    kind: TriageKind | null
    p_claim: number | null
    p_asr: number | null
    /** The Yes/No answer on a transcription card. */
    confirmed: boolean | null
    correction: string | null
    checked_as: string | null
    check_status: CheckStatus | null
    /** The checker's verdict, issue, suggestion, review flags, sources and model ids; null unless checked. */
    check: ExportedCheck | null
  }[]
}

export interface UseTranscriptReviewOptions {
  /** Streams the review. Defaults to an error-only source, so callers pass e.g. jevReviewSource(). */
  source?: ReviewSource
  /** Answers replies. Without one, replies get the failure message. */
  clarify?: ClarifySource
  /** `source` field of the export, e.g. "jev" or "mock". */
  sourceName?: string
  /** Checks claims. Defaults to the HTTP checker, POST /api/check (lib/claimChecker.ts). */
  checker?: ClaimChecker
  /** Triages a typed correction. Defaults to POST /api/triage/span (lib/jevReviewSource.ts). */
  triageSpan?: SpanTriager
}

export const REVIEW_ERROR_MESSAGE = 'The review stopped because of an error. Try again.'
export const REPLY_ERROR_MESSAGE = "Couldn't get an answer. Try again."
export const CHECK_ERROR_MESSAGE = "This claim couldn't be checked."
export const CORRECTION_ERROR_MESSAGE = "Couldn't check your correction. Try again."
/** How many earlier doctor turns go along as context for a correction or a check. */
const CONTEXT_TURNS = 2
const NO_SOURCE_MESSAGE = "Agent review isn't available for this transcript yet."

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** A plain copy of a check for the export (no reactive proxies, no `status`). */
function exportedCheck({ status: _status, ...check }: CheckedResult): ExportedCheck {
  return {
    ...check,
    review_reasons: [...check.review_reasons],
    sources: check.sources.map((s) => ({ ...s })),
  }
}

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
  /** Spans Jev triaged as small talk (span_skipped events): counted, never shown. */
  const skippedSpans = ref(0)
  /** The triage model from the done event, e.g. jev-1.14.0. */
  const model = ref<string | null>(null)

  const checker = options.checker ?? defaultClaimChecker
  const triager = options.triageSpan ?? defaultSpanTriager

  /** Turn id → position in the transcript given to start(); turns only seen in events go after, in arrival order. */
  let turnIndex = new Map<string, number>()
  /** The turns given to start(), for the context of checks and corrections. Read only, never modified. */
  let reviewTurns: RoledTurn[] = []
  let runController: AbortController | null = null
  const replyControllers = new Set<AbortController>()
  /** Comment id → its running check / correction. An answer only lands if its controller is still the one here. */
  const checkControllers = new Map<string, AbortController>()
  const correctionControllers = new Map<string, AbortController>()
  let messageSeq = 0

  const hasResults = computed(() => comments.value.length > 0)

  const counts = computed(() => {
    const c = { open: 0, resolved: 0, disagreed: 0 }
    for (const comment of comments.value) if (comment.verdict !== 'accurate') c[comment.status]++
    return c
  })

  /** For the triage header: claim comments, unanswered open transcription cards, and skipped spans. */
  const triageCounts = computed(() => {
    let claims = 0
    let openTranscriptions = 0
    for (const c of comments.value) {
      if (c.verdict === 'claim') claims++
      else if (c.verdict === 'transcription' && c.status === 'open' && c.confirmed === null) openTranscriptions++
    }
    return { claims, openTranscriptions, skipped: skippedSpans.value }
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

  /** Aborts every running check. With `markNotChecked` (stop), those claims end as not_checked. */
  function abortChecks(markNotChecked: boolean) {
    for (const [id, controller] of checkControllers) {
      controller.abort()
      const c = markNotChecked ? find(id) : undefined
      if (c?.checkStatus === 'checking') c.checkStatus = 'not_checked'
    }
    checkControllers.clear()
  }

  function abortCorrections() {
    for (const controller of correctionControllers.values()) controller.abort()
    correctionControllers.clear()
  }

  /** Everything start() and reset() throw away: the run, replies, checks and corrections. */
  function abortAll() {
    runController?.abort()
    runController = null
    abortReplies()
    abortChecks(false)
    abortCorrections()
  }

  function clearResults() {
    errorMessage.value = ''
    currentTurnId.value = null
    doneTurnIds.value = []
    comments.value = []
    activeCommentId.value = null
    skippedSpans.value = 0
    model.value = null
  }

  /** Text of up to CONTEXT_TURNS doctor turns before `turnId`, oldest first. */
  function previousDoctorTurns(turnId: string): string[] {
    const at = reviewTurns.findIndex((t) => t.id === turnId)
    if (at < 0) return []
    return reviewTurns
      .slice(0, at)
      .filter((t) => t.role === 'doctor')
      .slice(-CONTEXT_TURNS)
      .map((t) => t.text)
  }

  /** Sends a claim comment to the checker: checking, then the checker's status, or error. */
  function startCheck(id: string) {
    const c = find(id)
    if (!c) return
    checkControllers.get(id)?.abort()
    const controller = new AbortController()
    checkControllers.set(id, controller)
    c.checkStatus = 'checking'
    c.checkError = null
    c.check = null
    const claim = { turn_id: c.turn_id, quote: c.checkedAs ?? c.quote, context: previousDoctorTurns(c.turn_id) }
    // Looked up again on settle: stop/reset/start may have replaced the check or the comments meanwhile.
    const settle = (update: (c: ReviewThread) => void) => {
      if (checkControllers.get(id) !== controller) return
      checkControllers.delete(id)
      const current = find(id)
      if (current) update(current)
    }
    let pending: Promise<CheckResult>
    try {
      pending = checker(claim, controller.signal)
    } catch (error) {
      pending = Promise.reject(error)
    }
    pending.then(
      (result) =>
        settle((t) => {
          t.checkStatus = result.status
          t.check = result.status === 'checked' ? result : null
        }),
      (error) =>
        settle((t) => {
          if (isAbort(error)) {
            t.checkStatus = 'not_checked'
            return
          }
          t.checkStatus = 'error'
          t.checkError = error instanceof PredictError ? error.message : CHECK_ERROR_MESSAGE
        }),
    )
  }

  /** A transcription card that still waits for Yes / No. */
  const answerable = (c: ReviewThread) =>
    Boolean(c.triage) && c.verdict === 'transcription' && c.confirmed === null && !c.correctionPending

  function apply(event: ReviewEvent) {
    switch (event.type) {
      case 'turn_started':
        if (!turnIndex.has(event.turn_id)) turnIndex.set(event.turn_id, turnIndex.size)
        currentTurnId.value = event.turn_id
        break
      case 'comment':
        if (find(event.comment.id)) break
        comments.value.push({
          ...event.comment,
          status: 'open',
          disagreeReason: null,
          thread: [],
          ...EMPTY_TRIAGE_STATE,
        })
        if (event.comment.verdict === 'claim') startCheck(event.comment.id)
        break
      case 'span_skipped':
        skippedSpans.value++
        break
      case 'turn_done':
        if (!doneTurnIds.value.includes(event.turn_id)) doneTurnIds.value.push(event.turn_id)
        if (currentTurnId.value === event.turn_id) currentTurnId.value = null
        break
      case 'done':
        currentTurnId.value = null
        if (event.model) model.value = event.model
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
   * Reviews `turns`, replacing any previous run and its results (pending replies, checks and corrections are
   * aborted too). Resolves when the run ends; never rejects.
   */
  async function start(
    turns: RoledTurn[],
    meta: { sampleId: string | null; source?: ReviewSource } = { sampleId: null },
  ): Promise<void> {
    abortAll()
    const controller = new AbortController()
    runController = controller
    const { signal } = controller
    clearResults()
    sampleId.value = meta.sampleId
    turnIndex = new Map(turns.map((t, i) => [t.id, i]))
    reviewTurns = turns
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

  /**
   * Stops a running review and keeps the comments received so far. Running claim checks are aborted and end as
   * not_checked; replies and corrections in flight carry on.
   */
  function stop() {
    if (status.value !== 'running') return
    runController?.abort()
    runController = null
    abortChecks(true)
    currentTurnId.value = null
    status.value = 'stopped'
  }

  /**
   * Invalidation (Clear, a new import, Edit roles): aborts the run and every pending reply, check and correction,
   * then clears to idle.
   */
  function reset() {
    abortAll()
    clearResults()
    sampleId.value = null
    turnIndex = new Map()
    reviewTurns = []
    status.value = 'idle'
  }

  /** Reply is for fact-check comments only: Jev labels spans and can't answer questions. */
  function canReply(comment: Pick<ReviewComment, 'triage'>): boolean {
    return !comment.triage
  }

  /**
   * "Yes, it's correct" on a transcription card: a claim-based span becomes a claim and goes to the checker, a
   * small-talk span is resolved. Ignored for anything but an unanswered transcription card.
   */
  function confirmTranscription(id: string) {
    const c = find(id)
    if (!c || !answerable(c)) return
    c.confirmed = true
    c.correctionError = null
    if (c.triage!.base_kind === 'claim') {
      c.verdict = 'claim'
      startCheck(id)
    } else {
      c.status = 'resolved'
      c.disagreeReason = null
    }
  }

  /**
   * "No" with the doctor's correction: the trimmed text goes to triageSpan with up to 2 earlier doctor turns. A claim
   * makes the card a claim checked as the typed text; small talk resolves it. On failure the card shows
   * correctionError and stays answerable. Blank text, or a card that isn't an unanswered transcription card, is
   * ignored with no call. Only the comment changes; the transcript text is never edited. reset() and start() abort
   * it.
   */
  async function correctTranscription(id: string, text: string): Promise<void> {
    const typed = text.trim()
    const c = find(id)
    if (!typed || !c || !answerable(c)) return
    c.correctionPending = true
    c.correctionError = null
    const controller = new AbortController()
    correctionControllers.set(id, controller)
    const { signal } = controller
    const current = () => (correctionControllers.get(id) === controller && !signal.aborted ? find(id) : undefined)
    try {
      const result = await triager(typed, previousDoctorTurns(c.turn_id), signal)
      const t = current()
      if (!t) return
      t.correctionPending = false
      t.confirmed = false
      t.correction = typed
      if (result.kind === 'claim') {
        t.verdict = 'claim'
        t.checkedAs = typed
        startCheck(id)
      } else {
        t.status = 'resolved'
        t.disagreeReason = null
      }
    } catch (error) {
      const t = current()
      if (!t || isAbort(error)) return
      t.correctionPending = false
      t.correctionError = error instanceof PredictError ? error.message : CORRECTION_ERROR_MESSAGE
    } finally {
      if (correctionControllers.get(id) === controller) correctionControllers.delete(id)
    }
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
   * answer (or REPLY_ERROR_MESSAGE). Blank questions, and triage comments (see canReply), are ignored. Works while
   * the review is running; reset() and start() abort it and the pending message goes with the cleared comments.
   */
  async function reply(id: string, question: string): Promise<void> {
    const text = question.trim()
    const c = find(id)
    if (!text || !c || !canReply(c)) return
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
        notice: reviewExportNotice(options.sourceName ?? 'mock'),
        source: options.sourceName ?? 'mock',
        sample_id: id,
        exported_at: now.toISOString(),
        model: model.value,
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
          kind: c.triage?.kind ?? null,
          p_claim: c.triage?.p_claim ?? null,
          p_asr: c.triage?.p_asr ?? null,
          confirmed: c.confirmed,
          correction: c.correction,
          checked_as: c.checkedAs,
          check_status: c.checkStatus,
          check: c.check ? exportedCheck(c.check) : null,
        })),
      },
    }
  }

  if (getCurrentInstance())
    onBeforeUnmount(() => {
      abortAll()
    })

  return {
    status,
    errorMessage,
    currentTurnId,
    doneTurnIds,
    comments,
    activeCommentId,
    sampleId,
    skippedSpans,
    model,
    hasResults,
    counts,
    triageCounts,
    issueOrder,
    start,
    stop,
    reset,
    resolve,
    reopen,
    disagree,
    setActive,
    reply,
    canReply,
    confirmTranscription,
    correctTranscription,
    nextIssue,
    prevIssue,
    exportReview,
  }
}
