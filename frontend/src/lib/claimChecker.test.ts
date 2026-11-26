import { afterEach, describe, expect, it, vi } from 'vitest'
import { STUB_CHECK_DELAY_MS, stubClaimChecker } from './claimChecker'

const claim = { turn_id: 't1', quote: 'Moderately differentiated means it has not spread.', context: [] }

afterEach(() => vi.useRealTimers())

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
