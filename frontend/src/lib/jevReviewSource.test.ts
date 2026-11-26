import { describe, expect, it, vi } from 'vitest'
import type { ReviewEvent, ReviewSource, RoledTurn } from '@/types'
import { messageFor, PredictError } from './api'
import { TRIAGE_STREAM_ERROR_MESSAGE, jevReviewSource, triageSpan } from './jevReviewSource'

const encoder = new TextEncoder()

const turn = (id: string, role: RoledTurn['role'], text = `text of ${id}`, over: Partial<RoledTurn> = {}): RoledTurn => ({
  id,
  speaker: role === 'doctor' ? 'S1' : 'S2',
  start: null,
  end: null,
  text,
  is_final: true,
  words: [],
  min_confidence: null,
  role,
  ...over,
})

/** A streamed 200 response made of the given chunks (strings are UTF-8 encoded). */
function streamResponse(chunks: (string | Uint8Array)[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === 'string' ? encoder.encode(c) : c)
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })
}

/** A streamed response the test feeds by hand. It ignores the fetch signal, like a slow server. */
function controlledResponse() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>
  let cancelled = false
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c
    },
    cancel() {
      cancelled = true
    },
  })
  return {
    response: new Response(body, { status: 200 }),
    send: (line: string) => ctrl.enqueue(encoder.encode(line)),
    fail: (error: unknown) => ctrl.error(error),
    get cancelled() {
      return cancelled
    },
  }
}

const ndjson = (...events: object[]) => events.map((e) => JSON.stringify(e) + '\n').join('')

const claimSpan = {
  type: 'span',
  turn_id: 't1',
  start: 0,
  end: 20,
  quote: 'Staging tells us a lot.',
  kind: 'claim',
  base_kind: 'claim',
  p_claim: 0.91,
  p_asr: 0.05,
}
const asrSpan = {
  type: 'span',
  turn_id: 't1',
  start: 21,
  end: 45,
  quote: 'The growth in your colin.',
  kind: 'transcription',
  base_kind: 'skip',
  p_claim: 0.2,
  p_asr: 0.88,
}
const skipSpan = {
  type: 'span',
  turn_id: 't1',
  start: 46,
  end: 60,
  quote: 'Please sit down.',
  kind: 'skip',
  base_kind: 'skip',
  p_claim: 0.02,
  p_asr: null,
}

async function collect(source: ReviewSource, turns: RoledTurn[], signal = new AbortController().signal) {
  const events: ReviewEvent[] = []
  for await (const e of source(turns, signal)) events.push(e)
  return events
}

const fetchReturning = (response: Response) => vi.fn<typeof fetch>(async () => response)

describe('jevReviewSource request', () => {
  it('posts only doctor turns to /api/triage, with word confidences when the turn has them', async () => {
    const fetchImpl = fetchReturning(streamResponse([ndjson({ type: 'done', model: 'jev-1.14.0' })]))
    const turns = [
      turn('t1', 'doctor', 'Hello there.', {
        words: [
          { text: 'Hello', start: 0, end: 0.4, confidence: 0.99 },
          { text: 'there.', start: 0.4, end: 0.8, confidence: 0.52 },
        ],
      }),
      turn('t2', 'patient'),
      turn('t3', null),
      turn('t4', 'other'),
      turn('t5', 'doctor', 'No confidences here.', {
        words: [{ text: 'No', start: null, end: null, confidence: null }],
      }),
    ]
    const signal = new AbortController().signal
    await collect(jevReviewSource({ fetchImpl }), turns, signal)

    expect(fetchImpl).toHaveBeenCalledOnce()
    const [url, init] = fetchImpl.mock.calls[0]!
    expect(url).toBe('/api/triage')
    expect(init!.method).toBe('POST')
    expect(init!.signal).toBe(signal)
    expect(new Headers(init!.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init!.body as string)).toEqual({
      turns: [
        {
          id: 't1',
          text: 'Hello there.',
          words: [
            { text: 'Hello', confidence: 0.99 },
            { text: 'there.', confidence: 0.52 },
          ],
        },
        { id: 't5', text: 'No confidences here.' },
      ],
    })
  })
})

describe('jevReviewSource events', () => {
  it('maps turn, span and done events', async () => {
    const fetchImpl = fetchReturning(
      streamResponse([
        ndjson(
          { type: 'turn_started', turn_id: 't1' },
          claimSpan,
          asrSpan,
          skipSpan,
          { type: 'turn_done', turn_id: 't1' },
          { type: 'done', model: 'jev-1.14.0' },
        ),
      ]),
    )
    const events = await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])
    expect(events).toEqual([
      { type: 'turn_started', turn_id: 't1' },
      {
        type: 'comment',
        comment: {
          id: 't1:0',
          turn_id: 't1',
          quote: 'Staging tells us a lot.',
          start: 0,
          end: 20,
          verdict: 'claim',
          issue: null,
          suggestion: null,
          source: null,
          triage: { kind: 'claim', base_kind: 'claim', p_claim: 0.91, p_asr: 0.05 },
        },
      },
      {
        type: 'comment',
        comment: {
          id: 't1:21',
          turn_id: 't1',
          quote: 'The growth in your colin.',
          start: 21,
          end: 45,
          verdict: 'transcription',
          issue: null,
          suggestion: null,
          source: null,
          triage: { kind: 'transcription', base_kind: 'skip', p_claim: 0.2, p_asr: 0.88 },
        },
      },
      { type: 'span_skipped', turn_id: 't1', start: 46, end: 60 },
      { type: 'turn_done', turn_id: 't1' },
      { type: 'done', model: 'jev-1.14.0' },
    ])
  })

  it('parses lines split across chunks, several lines per chunk, blank lines and a last line without \\n', async () => {
    const text = ndjson({ type: 'turn_started', turn_id: 't1' }, { ...claimSpan, quote: 'Café – naïve.' })
    const bytes = encoder.encode(text)
    // Split inside the multi-byte "é" too.
    const cut = bytes.indexOf(0xc3) + 1
    const fetchImpl = fetchReturning(
      streamResponse([
        bytes.slice(0, 7),
        bytes.slice(7, cut),
        bytes.slice(cut),
        '\n\n  \r\n',
        JSON.stringify({ type: 'turn_done', turn_id: 't1' }) + '\r\n' + '{"type":"do',
        'ne","model":"jev-1.14.0"}',
      ]),
    )
    const events = await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])
    expect(events.map((e) => e.type)).toEqual(['turn_started', 'comment', 'turn_done', 'done'])
    expect(events[1]).toMatchObject({ comment: { quote: 'Café – naïve.' } })
    expect(events[3]).toEqual({ type: 'done', model: 'jev-1.14.0' })
  })

  it('ignores unknown event types', async () => {
    const fetchImpl = fetchReturning(streamResponse([ndjson({ type: 'heartbeat' }, { type: 'done', model: 'm' })]))
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])).toEqual([
      { type: 'done', model: 'm' },
    ])
  })

  it('stops after done', async () => {
    const fetchImpl = fetchReturning(
      streamResponse([ndjson({ type: 'done', model: 'm' }, { type: 'turn_started', turn_id: 't9' })]),
    )
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])).toEqual([
      { type: 'done', model: 'm' },
    ])
  })
})

describe('jevReviewSource errors', () => {
  it.each([
    [503, 'triage_unavailable'],
    [429, 'triage_rate_limited'],
    [502, 'triage_upstream_error'],
    [422, 'invalid_triage_request'],
  ])('a %i %s before the stream becomes one error event', async (status, code) => {
    const fetchImpl = fetchReturning(new Response(JSON.stringify({ detail: 'raw', code }), { status }))
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])).toEqual([
      { type: 'error', message: messageFor(code) },
    ])
  })

  it('an error line mid-stream keeps earlier events and ends with its message', async () => {
    const fetchImpl = fetchReturning(
      streamResponse([
        ndjson(
          { type: 'turn_started', turn_id: 't1' },
          claimSpan,
          { type: 'error', code: 'triage_rate_limited' },
          { type: 'turn_done', turn_id: 't1' },
        ),
      ]),
    )
    const events = await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])
    expect(events.map((e) => e.type)).toEqual(['turn_started', 'comment', 'error'])
    expect(events[2]).toEqual({ type: 'error', message: messageFor('triage_rate_limited') })
  })

  it('a failed fetch gives the start-uvicorn message', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      throw new TypeError('Failed to fetch')
    })
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])).toEqual([
      { type: 'error', message: messageFor('network') },
    ])
    expect(messageFor('network')).toMatch(/uvicorn/)
  })

  it('a non-JSON 5xx (dev proxy, backend down) gives the start-uvicorn message', async () => {
    const fetchImpl = fetchReturning(new Response('proxy error', { status: 500 }))
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])).toEqual([
      { type: 'error', message: messageFor('network') },
    ])
  })

  it('a connection dropped mid-stream gives the start-uvicorn message', async () => {
    const c = controlledResponse()
    const source = jevReviewSource({ fetchImpl: fetchReturning(c.response) })
    const it = source([turn('t1', 'doctor')], new AbortController().signal)[Symbol.asyncIterator]()
    c.send(ndjson({ type: 'turn_started', turn_id: 't1' }))
    expect((await it.next()).value).toEqual({ type: 'turn_started', turn_id: 't1' })
    c.fail(new TypeError('network error'))
    expect(await it.next()).toEqual({ done: false, value: { type: 'error', message: messageFor('network') } })
    expect((await it.next()).done).toBe(true)
  })

  it.each([
    ['not JSON', '{"type": "span", oops\n'],
    ['a span missing fields', ndjson({ type: 'span', turn_id: 't1', start: 0, kind: 'claim' })],
    ['a span with an unknown kind', ndjson({ ...claimSpan, kind: 'banter' })],
    ['a JSON array', '[1, 2]\n'],
  ])('%s gives a generic error and ends the stream', async (_name, line) => {
    const fetchImpl = fetchReturning(
      streamResponse([ndjson({ type: 'turn_started', turn_id: 't1' }), line, ndjson({ type: 'done' })]),
    )
    const events = await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])
    expect(events).toEqual([
      { type: 'turn_started', turn_id: 't1' },
      { type: 'error', message: TRIAGE_STREAM_ERROR_MESSAGE },
    ])
  })

  it('a stream that ends without done is an error', async () => {
    const fetchImpl = fetchReturning(streamResponse([ndjson({ type: 'turn_started', turn_id: 't1' })]))
    const events = await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')])
    expect(events).toEqual([
      { type: 'turn_started', turn_id: 't1' },
      { type: 'error', message: TRIAGE_STREAM_ERROR_MESSAGE },
    ])
  })
})

describe('jevReviewSource abort', () => {
  it('aborting mid-stream cancels the body and yields nothing more', async () => {
    const c = controlledResponse()
    const fetchImpl = fetchReturning(c.response)
    const controller = new AbortController()
    const it = jevReviewSource({ fetchImpl })([turn('t1', 'doctor')], controller.signal)[Symbol.asyncIterator]()
    c.send(ndjson({ type: 'turn_started', turn_id: 't1' }))
    expect((await it.next()).value).toEqual({ type: 'turn_started', turn_id: 't1' })

    const next = it.next() // waiting on the body
    controller.abort()
    expect(await next).toEqual({ done: true, value: undefined })
    expect(c.cancelled).toBe(true)
    expect(fetchImpl.mock.calls[0]![1]!.signal!.aborted).toBe(true)
  })

  it('aborting with a line already buffered yields nothing more', async () => {
    const controller = new AbortController()
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      controller.abort() // e.g. Stop pressed while the response arrived
      return streamResponse([ndjson({ type: 'turn_started', turn_id: 't1' }, { type: 'done' })])
    })
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')], controller.signal)).toEqual([])
  })

  it('an AbortError from fetch ends quietly', async () => {
    const controller = new AbortController()
    controller.abort()
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      throw new DOMException('aborted', 'AbortError')
    })
    expect(await collect(jevReviewSource({ fetchImpl }), [turn('t1', 'doctor')], controller.signal)).toEqual([])
  })

  it('stopping iteration early (the consumer breaks) cancels the body', async () => {
    const c = controlledResponse()
    const it = jevReviewSource({ fetchImpl: fetchReturning(c.response) })(
      [turn('t1', 'doctor')],
      new AbortController().signal,
    )[Symbol.asyncIterator]()
    c.send(ndjson({ type: 'turn_started', turn_id: 't1' }))
    await it.next()
    await it.return!(undefined)
    expect(c.cancelled).toBe(true)
  })
})

describe('triageSpan', () => {
  it('posts the text and previous doctor turns to /api/triage/span and returns the decision', async () => {
    const fetchImpl = fetchReturning(new Response(JSON.stringify({ kind: 'claim', p_claim: 0.93 }), { status: 200 }))
    const signal = new AbortController().signal
    const result = await triageSpan('The growth in your colon.', ['Hello.', 'Your results are back.'], signal, {
      fetchImpl,
    })
    expect(result).toEqual({ kind: 'claim', p_claim: 0.93 })
    const [url, init] = fetchImpl.mock.calls[0]!
    expect(url).toBe('/api/triage/span')
    expect(init!.method).toBe('POST')
    expect(init!.signal).toBe(signal)
    expect(JSON.parse(init!.body as string)).toEqual({
      text: 'The growth in your colon.',
      previous_doctor_turns: ['Hello.', 'Your results are back.'],
    })
  })

  it('maps {detail, code} errors to readable PredictErrors', async () => {
    const fetchImpl = fetchReturning(
      new Response(JSON.stringify({ detail: 'raw', code: 'triage_unavailable' }), { status: 503 }),
    )
    const error = await triageSpan('x', [], new AbortController().signal, { fetchImpl }).catch((e) => e)
    expect(error).toBeInstanceOf(PredictError)
    expect(error.code).toBe('triage_unavailable')
    expect(error.message).toBe(messageFor('triage_unavailable'))
  })

  it('maps a failed fetch to the network message', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      throw new TypeError('Failed to fetch')
    })
    const error = await triageSpan('x', [], new AbortController().signal, { fetchImpl }).catch((e) => e)
    expect(error.code).toBe('network')
    expect(error.message).toBe(messageFor('network'))
  })

  it('rethrows aborts untouched', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      throw new DOMException('aborted', 'AbortError')
    })
    const error = await triageSpan('x', [], new AbortController().signal, { fetchImpl }).catch((e) => e)
    expect(error.name).toBe('AbortError')
  })

  it('rejects a malformed answer', async () => {
    const fetchImpl = fetchReturning(new Response(JSON.stringify({ kind: 'maybe' }), { status: 200 }))
    const error = await triageSpan('x', [], new AbortController().signal, { fetchImpl }).catch((e) => e)
    expect(error).toBeInstanceOf(PredictError)
    expect(error.message).toBe(TRIAGE_STREAM_ERROR_MESSAGE)
  })
})
