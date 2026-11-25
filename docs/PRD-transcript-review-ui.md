# PRD: Transcript Review UI

## What
After roles are confirmed, a **Review** button starts an agent review of the doctor turns. The UI then does four things:
- It walks through the doctor turns one at a time. The current turn shows "Reviewing…".
- It highlights spans: **inaccurate** in eosin with an ✕ icon, **incomplete** in amber with a + icon. **Accurate** spans get a faint green underline and a ✓, with no card.
- It shows a comment card in the right margin for each issue, lined up with its highlight. Each card has:
  - the verdict
  - what's wrong or missing
  - the suggested wording
  - the source (e.g. "AJCC 8th melanoma")
- It lets the person **Resolve / Reopen**, **Disagree** (optional reason), and **Reply** to ask for clarification. In the mock, the agent answers each reply with a canned clarification.

A header shows open, resolved and disagreed counts. It has Previous/Next issue buttons, a "Show resolved" toggle, **Stop**, and **Export review** (JSON). Today the agent is a mock, labelled "Demo review: hand-written mock, not an AI agent".

## Why
- The fact checker is only useful if a person can see where each problem is and act on it quickly. Inline highlights plus margin comments are a pattern clinicians already know from Google Docs.
- Building the UI against a fixed event contract first means the real agent (DeepSeek or another model) drops in later as a new `ReviewSource`, with no UI rewrite.
- "Disagree" and its reasons are labelled feedback. The export becomes eval data for the agent later.
- The review is progressive because LLM review takes seconds per turn. Streaming lets the person read and resolve early comments while later turns are still being checked.

## How
**Contract** (`src/types.ts`, shaped so a later SSE endpoint can emit it unchanged):
```
ReviewComment { id, turn_id, quote, start, end, verdict: 'inaccurate'|'incomplete'|'accurate',
                issue: string|null, suggestion: string|null, source: string|null }
ReviewEvent = {type:'turn_started', turn_id} | {type:'comment', comment} | {type:'turn_done', turn_id}
            | {type:'done'} | {type:'error', message}
ReviewSource = (turns: RoledTurn[], signal: AbortSignal) => AsyncIterable<ReviewEvent>
ClarifySource = (comment, question, signal) => Promise<string>
```
- **Anchoring:** a comment is located by `turn_id` + `quote`. The UI turns it into `start`/`end` character offsets in `turn.text`, using the first exact match. If the quote isn't found, the comment becomes an **orphan**: its card is shown at the top of the margin with "Couldn't find this text in the turn", and it has no highlight. Comments never span more than one turn.
- **Mock** (`src/mocks/review/`):
  - Hand-written comments for 3 samples that cover the edge cases:
    - `crc_adenocarcinoma` (Deepgram, word-level)
    - `breast_idc` (OpenAI diarized, text only, no words)
    - `melanoma` (AWS, punctuation attached to words)
  - Each sample has 1–2 accurate statements, at least 2 incomplete, at least 2 inaccurate, 1 pair of overlapping spans and 1 orphan.
  - `mockReviewSource` yields events with a delay per turn (0 in tests, using fake timers) and stops on abort.
  - `mockClarify` returns canned text after about 800 ms.
  - For uploaded (non-sample) transcripts, the Review button is disabled with the note "Agent review isn't available for uploaded transcripts yet."
- **State** in `composables/useTranscriptReview.ts`:
  - Review status: `idle | running | done | stopped | error`.
  - Each comment has a status of `open | resolved | disagreed`, an optional `disagreeReason`, and a `thread` of `{author:'you'|'agent', text, pending?}`.
  - Also tracked: `activeCommentId`, `currentTurnId`.
  - **Invalidation:** Clear, a new import, or Edit roles aborts the review and discards its results. Edit roles first asks for confirmation when comments exist.
  - Replies are allowed while the review is running. A disagreed comment can be reopened.
- **Rendering** (`TranscriptView.vue`):
  - A pure function `splitSegments(text, words, ranges)` splits each turn into runs that carry their highlight and low-confidence flags. A low-confidence underline and a highlight can sit on the same word. Overlapping highlights stack, the shortest range wins the click, and the stacked region gets a darker tint.
  - Word offsets are found by scanning `turn.text` in order. If the words don't line up with the text, the turn falls back to plain-text rendering, which keeps highlights and drops the confidence underlines.
  - Highlights are `<mark>` elements that can be focused, with `aria-describedby` pointing to their card. Enter or click activates the card. Verdict is shown by icon and label as well as colour.
- **Margin** (`ReviewMargin.vue`, `CommentCard.vue`):
  - Transcript and margin share one scroll container, replacing the current inner `max-h-[32rem]` list while reviewing.
  - A pure function `layoutCards(anchors, heights, activeId, gap)` places cards. Each card sits at its anchor's top, overlapping cards are pushed down, and the active card snaps to its anchor while the others move around it, as in Docs.
  - Positions are re-measured with `ResizeObserver` when cards expand or the window is resized. Animations respect `prefers-reduced-motion`.
  - Below `lg`, cards render as a list in transcript order. Clicking a card scrolls to its highlight.
  - Resolved and disagreed cards collapse to one line and hide unless "Show resolved" is on.
- **Export:** a JSON Blob download named `review-<sample-id>-<YYYYMMDD>.json`. It contains `notice`, `source: "mock"`, and each comment with its quote, verdict, status, disagree reason and thread. The transcript itself is not included, and nothing is sent to a server.

## Non-goals
Choosing or calling an LLM, RAG/retrieval, a backend review endpoint, SSE, reviewing patient turns, editing transcript text, applying suggestions to the text, comments spanning several turns, people commenting on their own selections, multiple users, persistence and auth.

## Research note: GitHub Student Pack and model choice (for the next step, not this PRD's scope)
- **GitHub Models was retired on July 30, 2026**, so there is no free DeepSeek access through it. The docs point to Azure AI Foundry instead.
- **Azure for Students:** $100 credit. Check whether DeepSeek deployments in Azure AI Foundry are covered before relying on it.
- **Copilot (Student):** free, but it is an IDE assistant and gives no API credits for the app.
- **Other offers useful later:**
  - Heroku: $13/month for 24 months (FastAPI hosting).
  - MongoDB Atlas: $50 credit (Atlas has vector search, which could serve RAG).
  - Sentry: 50K errors/year.
  - The pack has no LLM API credits (OpenAI, Anthropic, DeepSeek).
- **Challenge:** DeepSeek's own hosted API sends transcripts to servers in China. That's a problem for real patient conversations (HIPAA/GDPR). For anything beyond synthetic samples, use the open weights self-hosted or on Azure, or another provider that offers a BAA. Decide when the agent PRD is written.

## Testing
- **Unit:**
  - quote → offsets, including the orphan case.
  - `splitSegments`: overlaps, combined with low confidence, word/text mismatch fallback.
  - `layoutCards`: no overlaps, active card snaps to its anchor, stable ordering.
  - The mock source event order, and that it stops on abort.
- **Composable:**
  - Progressive arrival under fake timers.
  - Stop, and invalidation on clear, re-import and edit roles.
  - Resolve/reopen and disagree with a reason.
  - A reply shows a pending state, then the canned answer.
  - Export contents.
  - No web storage is used.
- **Component:**
  - Clicking a highlight activates its card, and the reverse.
  - Keyboard focus reaches highlights and cards.
  - "Show resolved" works and the counts update.
  - Review is disabled for uploads.
  - The demo label is shown.
- `npm test`, `npm run type-check` and `npm run build` pass. Do a manual browser check on the 3 mocked samples at desktop and narrow widths.

## Issues (GitHub #26–#31; dependencies: 1 → 2 → {3, 4} → 5 → 6)
**1. Review contract, anchoring and hand-written mock source**
- [ ] `ReviewComment`, `ReviewEvent`, `ReviewSource` and `ClarifySource` types are exported, and the docs explain the future SSE mapping.
- [ ] `anchorComment(turn, quote)` returns offsets or orphan, and is unit-tested (repeated quote, missing quote, punctuation, NFC).
- [ ] Hand-written mocks for crc_adenocarcinoma, breast_idc and melanoma. Each has ≥1 accurate, ≥2 incomplete, ≥2 inaccurate, 1 overlap and 1 orphan. A test asserts every non-orphan quote is found in its turn of the imported sample.
- [ ] `mockReviewSource` emits turn_started → comment* → turn_done per doctor turn, then done. The delay can be configured, and abort stops it with no further events. `mockClarify` resolves after a delay and can be aborted.

**2. `useTranscriptReview` state machine**
- [ ] States are `idle → running → done | stopped | error`, and `currentTurnId` follows the events.
- [ ] Comment actions: resolve, reopen, disagree (optional reason), reply. A reply adds a pending agent message that the mock answer replaces.
- [ ] Clear, a new import, or edit roles aborts and discards the review. Edit roles asks for confirmation when comments exist.
- [ ] Counts for open, resolved and disagreed, and next/previous open comment in transcript order.
- [ ] `exportReview()` builds the JSON described in the PRD, with no transcript text beyond the quotes. Tests confirm no localStorage or sessionStorage use.

**3. Highlighted transcript rendering**
- [ ] `splitSegments` handles overlaps, low confidence and highlight on the same word, and the word/text mismatch fallback (unit-tested).
- [ ] Inaccurate, incomplete and accurate spans are styled with colour plus icon and screen-reader label. Accurate spans show no card.
- [ ] The current turn shows "Reviewing…" (aria-live polite), and turns still waiting are dimmed.
- [ ] Highlights can be focused, carry aria-describedby to their card, and activate on click or Enter. Text-only turns (OpenAI) highlight correctly.

**4. Docs-style comment margin**
- [ ] `layoutCards` is a pure function and unit-tested: no overlaps, the active card snaps to its anchor, input order is stable.
- [ ] Margin and transcript share one scroll container, and the layout re-runs on resize or card expansion (ResizeObserver).
- [ ] Activating a card scrolls to and emphasises its highlight, and the reverse.
- [ ] Below `lg`, cards show as an ordered list under the transcript. Orphan cards are pinned at the top with an explanation.
- [ ] Motion respects `prefers-reduced-motion`.

**5. Comment card actions and threads**
- [ ] Resolve collapses the card, and "Show resolved" reveals it with Reopen.
- [ ] Disagree opens an optional reason field. The card collapses with a "Disagreed" badge and the reason shown, and it can be reopened.
- [ ] The reply box sends the question, shows "Agent is thinking…", then the canned answer. Empty replies are blocked, and the box works while the review is running.
- [ ] The header has counts, Previous/Next issue, Show resolved, Stop and Export. Every control can be used from the keyboard.

**6. Integration into the Transcripts view, export and docs**
- [ ] A Review button appears after confirm. It is enabled for the 3 mocked samples, and disabled for other samples and uploads with an explanation.
- [ ] The "Demo review: hand-written mock, not an AI agent" label and the research-use disclaimer are shown.
- [ ] Export downloads `review-<id>-<date>.json`.
- [ ] The existing import, role and sample flows are unchanged, and their tests pass. `frontend/README.md` and `CLAUDE.md` are updated.
- [ ] `npm test`, `npm run type-check` and `npm run build` pass.
