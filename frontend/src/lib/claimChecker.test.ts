import { afterEach, describe, expect, it, vi } from 'vitest'
import { MESSAGE_CODES, PredictError, messageFor } from './api'
import {
  CHECK_RESPONSE_ERROR_MESSAGE,
  MAX_CONCURRENT_CHECKS,
  STUB_CHECK_DELAY_MS,
  createHttpClaimChecker,
  defaultClaimChecker,
  stubClaimChecker,
} from './claimChecker'
import type { CheckedResult } from '@/types'

const claim = { turn_id: 't1', quote: 'Moderately differentiated means it has not spread.', context: [] }

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('stubClaimChecker', () => {
  it('resolves not_checked after a short delay', async () => {
    vi.useFakeTimers()
    let result: unknown = null
    void stubClaimChecker(claim, new AbortController().signal).then((r) => (result = r))
    await vi.advanceTimersByTimeAsync(0)
    expect(result).toBeNull()
    await vi.advanceTimersByTimeAsync(STUB_CHECK_DELAY_MS)
    expect(result).toEqual({ status: 'not_checked' })
  })

  it('resolves at once with delayMs 0', async () => {
    await expect(stubClaimChecker(claim, new AbortController().signal, { delayMs: 0 })).resolves.toEqual({
      status: 'not_checked',
    })
  })

  it('rejects with an AbortError when aborted mid-wait', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const pending = stubClaimChecker(claim, controller.signal)
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('rejects with an AbortError when already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(stubClaimChecker(claim, controller.signal, { delayMs: 0 })).rejects.toMatchObject({
      name: 'AbortError',
    })
  })
})

/* ---------------------------------------------------------------------------------------------------------------- */

const checked: CheckedResult = {
  status: 'checked',
  verdict: 'inaccurate',
  issue: 'Differentiation grade says nothing about spread.',
  suggestion: 'Grade describes how the cells look; staging describes spread.',
  needs_review: true,
  review_reasons: ['models_disagree'],
  sources: [
    {
      chunk_id: 12,
      title: 'Colorectal cancer (NG151)',
      section: 'Recommendations > Staging',
      url: 'https://www.nice.org.uk/guidance/ng151',
      trust: 'high',
      year: 2021,
      quote: 'Grade does not determine stage.',
      stance: 'contradicts',
      luna_stance: 'contradicts',
      jev_stance: 'contradicts',
      jev_p: 0.91,
    },
  ],
  model: 'gpt-6-luna',
  jev_model: 'jev-1.14.0',
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

interface FetchCall {
  url: string
  init: RequestInit
  resolve: (r: Response) => void
  reject: (e: unknown) => void
}

/** A fetch the test settles by hand. Each call is recorded; an abort rejects it with an AbortError, like fetch. */
function manualFetch() {
  const calls: FetchCall[] = []
  const fetchImpl = vi.fn(
    (url: string, init: RequestInit) =>
      new Promise<Response>((resolve, reject) => {
        const signal = init.signal!
        if (signal.aborted) return reject(new DOMException('aborted', 'AbortError'))
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
        calls.push({ url, init, resolve, reject })
      }),
  )
  return { fetchImpl: fetchImpl as unknown as typeof fetch, spy: fetchImpl, calls }
}

const claimN = (n: number) => ({ turn_id: `t${n}`, quote: `claim ${n}`, context: [] })
const turnOf = (c: FetchCall) => JSON.parse(c.init.body as string).turn_id
const live = () => new AbortController().signal

describe('createHttpClaimChecker', () => {
  it('posts the claim as JSON to /api/check and returns the checked result', async () => {
    const fetchImpl = vi.fn(async () => json(checked))
    const check = createHttpClaimChecker({ fetchImpl: fetchImpl as unknown as typeof fetch })
    const withContext = { ...claim, context: ['Earlier turn one.', 'Earlier turn two.'] }
    await expect(check(withContext, live())).resolves.toEqual(checked)
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/check')
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init.body as string)).toEqual(withContext)
  })

  it('accepts every verdict and fills missing optional source fields with null', async () => {
    for (const verdict of ['accurate', 'inaccurate', 'incomplete', 'unverified', 'disputed'] as const) {
      const source = { chunk_id: 1, section: 'S', url: 'https://x.org', trust: 'low', quote: 'q', stance: 'unclear' }
      const fetchImpl = async () => json({ ...checked, verdict, sources: [source], jev_model: null })
      const result = await createHttpClaimChecker({ fetchImpl })(claim, live())
      expect(result).toMatchObject({ status: 'checked', verdict, jev_model: null })
      expect((result as CheckedResult).sources[0]).toEqual({
        ...source,
        title: null,
        year: null,
        luna_stance: null,
        jev_stance: null,
        jev_p: null,
      })
    }
  })

  it('keeps review reasons it does not know', async () => {
    const fetchImpl = async () => json({ ...checked, review_reasons: ['sources_disagree', 'something_new'] })
    const result = (await createHttpClaimChecker({ fetchImpl })(claim, live())) as CheckedResult
    expect(result.review_reasons).toEqual(['sources_disagree', 'something_new'])
  })

  it.each([
    ['a wrong status', { ...checked, status: 'nope' }],
    ['an unknown verdict', { ...checked, verdict: 'probably' }],
    ['sources that are not a list', { ...checked, sources: null }],
    ['a source with an unknown stance', { ...checked, sources: [{ ...checked.sources[0], stance: 'maybe' }] }],
    ['a source with an unknown trust', { ...checked, sources: [{ ...checked.sources[0], trust: 'max' }] }],
    ['a source without a quote', { ...checked, sources: [{ ...checked.sources[0], quote: 3 }] }],
    ['review reasons that are not strings', { ...checked, review_reasons: [1] }],
    ['needs_review that is not a boolean', { ...checked, needs_review: 'yes' }],
  ])('rejects a malformed answer (%s) with a readable error', async (_name, body) => {
    const error = await createHttpClaimChecker({ fetchImpl: async () => json(body) })(claim, live()).catch((e) => e)
    expect(error).toBeInstanceOf(PredictError)
    expect(error.message).toBe(CHECK_RESPONSE_ERROR_MESSAGE)
  })

  it('rejects a non-JSON 200 with a readable error', async () => {
    const fetchImpl = async () => new Response('<html>', { status: 200 })
    const error = await createHttpClaimChecker({ fetchImpl })(claim, live()).catch((e) => e)
    expect(error).toMatchObject({ message: CHECK_RESPONSE_ERROR_MESSAGE })
  })

  it.each([
    [503, 'check_unavailable', /OpenAI/],
    [429, 'check_rate_limited', /rate-limit/],
    [502, 'check_upstream_error', /Try again/],
    [422, 'invalid_check_request', /claim/],
  ])('maps %i %s through messageFor', async (status, code, pattern) => {
    const fetchImpl = async () => json({ detail: 'raw backend detail', code }, status)
    const error = await createHttpClaimChecker({ fetchImpl })(claim, live()).catch((e) => e)
    expect(error).toBeInstanceOf(PredictError)
    expect(error.code).toBe(code)
    expect(error.message).toBe(messageFor(code))
    expect(error.message).toMatch(pattern)
    expect(error.message).not.toContain('raw backend detail')
    expect(MESSAGE_CODES).toContain(code)
  })

  it('reports an unreachable backend when fetch fails', async () => {
    const fetchImpl = async () => {
      throw new TypeError('Failed to fetch')
    }
    const error = await createHttpClaimChecker({ fetchImpl })(claim, live()).catch((e) => e)
    expect(error).toMatchObject({ code: 'network', message: messageFor('network') })
  })

  it('rejects at once with an AbortError, without fetching, when the signal is already aborted', async () => {
    const { fetchImpl, spy } = manualFetch()
    const controller = new AbortController()
    controller.abort()
    await expect(createHttpClaimChecker({ fetchImpl })(claim, controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    })
    expect(spy).not.toHaveBeenCalled()
  })

  it('rejects with an AbortError when aborted while the request runs', async () => {
    const { fetchImpl, calls } = manualFetch()
    const controller = new AbortController()
    const pending = createHttpClaimChecker({ fetchImpl })(claim, controller.signal)
    expect(calls).toHaveLength(1)
    expect(calls[0]!.init.signal).toBe(controller.signal)
    controller.abort()
    const error = await pending.catch((e) => e)
    expect(error).toBeInstanceOf(DOMException)
    expect(error.name).toBe('AbortError')
  })

  it(`runs at most ${MAX_CONCURRENT_CHECKS} checks at once; the 5th starts when one finishes`, async () => {
    expect(MAX_CONCURRENT_CHECKS).toBe(4)
    const { fetchImpl, calls } = manualFetch()
    const check = createHttpClaimChecker({ fetchImpl })
    const results = [1, 2, 3, 4, 5].map((n) => check(claimN(n), live()))
    await Promise.resolve()
    expect(calls.map(turnOf)).toEqual(['t1', 't2', 't3', 't4'])

    calls[1]!.resolve(json(checked))
    await expect(results[1]).resolves.toEqual(checked)
    await vi.waitFor(() => expect(calls).toHaveLength(5))
    expect(turnOf(calls[4]!)).toBe('t5')
    for (const i of [0, 2, 3, 4]) calls[i]!.resolve(json(checked))
    await Promise.all(results)
  })

  it('a failed check frees its slot too', async () => {
    const { fetchImpl, calls } = manualFetch()
    const check = createHttpClaimChecker({ fetchImpl })
    const results = [1, 2, 3, 4, 5].map((n) => check(claimN(n), live()))
    calls[0]!.reject(new TypeError('Failed to fetch'))
    await expect(results[0]).rejects.toMatchObject({ code: 'network' })
    await vi.waitFor(() => expect(calls).toHaveLength(5))
  })

  it('an aborted queued check rejects at once and never fetches; the next one takes the slot', async () => {
    const { fetchImpl, spy, calls } = manualFetch()
    const check = createHttpClaimChecker({ fetchImpl })
    const running = [1, 2, 3, 4].map((n) => check(claimN(n), live()))
    const queuedController = new AbortController()
    const queued = check(claimN(5), queuedController.signal)
    const last = check(claimN(6), live())

    queuedController.abort()
    await expect(queued).rejects.toMatchObject({ name: 'AbortError' })
    expect(calls).toHaveLength(4)

    calls[0]!.resolve(json(checked))
    await running[0]
    await vi.waitFor(() => expect(calls).toHaveLength(5))
    expect(turnOf(calls[4]!)).toBe('t6')
    for (const c of calls.slice(1)) c.resolve(json(checked))
    await Promise.all([...running, last])
    expect(spy).toHaveBeenCalledTimes(5)
  })

  it('an aborted running check frees its slot', async () => {
    const { fetchImpl, calls } = manualFetch()
    const check = createHttpClaimChecker({ fetchImpl })
    const controllers = [1, 2, 3, 4].map(() => new AbortController())
    const running = controllers.map((c, i) => check(claimN(i + 1), c.signal))
    const fifth = check(claimN(5), live())
    controllers[2]!.abort()
    await expect(running[2]).rejects.toMatchObject({ name: 'AbortError' })
    await vi.waitFor(() => expect(calls).toHaveLength(5))
    calls[4]!.resolve(json(checked))
    await expect(fifth).resolves.toEqual(checked)
  })

  it('is the default checker, using the global fetch at call time', async () => {
    const fetchMock = vi.fn(async () => json(checked))
    vi.stubGlobal('fetch', fetchMock)
    await expect(defaultClaimChecker(claim, live())).resolves.toEqual(checked)
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe('/api/check')
  })
})
